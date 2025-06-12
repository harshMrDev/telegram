import os
import re
import asyncio
import yt_dlp
from telegram import Update, Document, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from tinydb import TinyDB, Query

db = TinyDB('user_modes.json')
User = Query()

def sanitize_filename(name):
    return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)

YOUTUBE_REGEX = re.compile(
    r'(https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[\w\-\_\?&=]+)'
)
def extract_youtube_links(text):
    return YOUTUBE_REGEX.findall(text or "")

async def download_youtube(link, mode):
    def get_stream():
        outtmpl = "/tmp/%(title).60s.%(ext)s"
        if mode == 'audio':
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': outtmpl,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'ffmpeg_location': '/usr/bin/ffmpeg' if os.path.exists('/usr/bin/ffmpeg') else None
            }
        elif mode == 'video_360':
            ydl_opts = {
                'format': 'bestvideo[height<=360]+bestaudio/best[height<=360]/best[height<=360]',
                'outtmpl': outtmpl,
                'merge_output_format': 'mp4',
                'ffmpeg_location': '/usr/bin/ffmpeg' if os.path.exists('/usr/bin/ffmpeg') else None
            }
        elif mode == 'video_480':
            ydl_opts = {
                'format': 'bestvideo[height<=480]+bestaudio/best[height<=480]/best[height<=480]',
                'outtmpl': outtmpl,
                'merge_output_format': 'mp4',
                'ffmpeg_location': '/usr/bin/ffmpeg' if os.path.exists('/usr/bin/ffmpeg') else None
            }
        else:
            raise Exception("Invalid mode")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link, download=True)
            if mode == 'audio':
                filename = ydl.prepare_filename(info).rsplit('.', 1)[0] + '.mp3'
            else:
                ext = 'mp4'
                filename = ydl.prepare_filename(info).rsplit('.', 1)[0] + f'.{ext}'
            safe_filename = '/tmp/' + sanitize_filename(os.path.basename(filename))
            if filename != safe_filename and os.path.exists(filename):
                os.rename(filename, safe_filename)
            return safe_filename if os.path.exists(safe_filename) else filename
    return await asyncio.to_thread(get_stream)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 *YouTube Downloader Bot*\n\n"
        "Send a YouTube link (or a .txt file with links).\n"
        "I'll ask for Audio/Video and (if video) ask for quality.\n"
        "*Limits*: Only files up to 50MB can be sent.",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send a YouTube link (or a .txt file with one link per line).\n"
        "I'll ask if you want audio or video, then for video: the quality.\n"
        "Files over 50MB can't be sent due to Telegram limits.",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    links = []
    if update.message.document:
        doc: Document = update.message.document
        if doc.mime_type == 'text/plain':
            file = await doc.get_file()
            file_path = f"/tmp/{doc.file_name}"
            await file.download_to_drive(file_path)
            with open(file_path, "r") as f:
                for line in f:
                    links += extract_youtube_links(line.strip())
            os.remove(file_path)
    else:
        links = extract_youtube_links(update.message.text or '')

    if not links:
        await update.message.reply_text("No YouTube links found in your message or file.")
        return

    # Store links and step in user_data
    context.user_data['pending_links'] = links
    context.user_data['step'] = 'choose_format'
    keyboard = [
        [InlineKeyboardButton("🎵 Audio", callback_data='choose_audio'),
         InlineKeyboardButton("📺 Video", callback_data='choose_video')],
        [InlineKeyboardButton("❌ Cancel", callback_data='choose_cancel')]
    ]
    await update.message.reply_text("Choose format:", reply_markup=InlineKeyboardMarkup(keyboard))

async def inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    step = context.user_data.get('step')
    links = context.user_data.get('pending_links', [])

    if step == 'choose_format':
        if query.data == 'choose_audio':
            context.user_data['chosen_mode'] = 'audio'
            context.user_data['step'] = None
            await query.edit_message_text("Downloading audio...")
            await process_and_send(update, context, links, 'audio')
            context.user_data.clear()
        elif query.data == 'choose_video':
            context.user_data['step'] = 'choose_quality'
            keyboard = [
                [InlineKeyboardButton("📺 360p", callback_data='video_360'),
                 InlineKeyboardButton("📺 480p", callback_data='video_480')],
                [InlineKeyboardButton("❌ Cancel", callback_data='choose_cancel')]
            ]
            await query.edit_message_text("Choose video quality:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif query.data == 'choose_cancel':
            await query.edit_message_text("Cancelled.")
            context.user_data.clear()
    elif step == 'choose_quality':
        if query.data in ['video_360', 'video_480']:
            mode = query.data
            context.user_data['chosen_mode'] = mode
            context.user_data['step'] = None
            await query.edit_message_text(f"Downloading {mode.replace('_', '')} ...")
            await process_and_send(update, context, links, mode)
            context.user_data.clear()
        elif query.data == 'choose_cancel':
            await query.edit_message_text("Cancelled.")
            context.user_data.clear()
    else:
        await query.edit_message_text("Session expired. Please resend the link.")

async def process_and_send(update, context, links, mode):
    for link in links:
        try:
            msg = await update.effective_chat.send_message(f"Processing: {link}")
            file_path = await download_youtube(link, mode)
            if not os.path.isfile(file_path):
                await update.effective_chat.send_message(f"ERROR: File not found: {file_path}")
                continue
            if os.path.getsize(file_path) == 0:
                await update.effective_chat.send_message(f"ERROR: File is empty: {file_path}")
                os.remove(file_path)
                continue
            if os.path.getsize(file_path) > 50*1024*1024:
                await update.effective_chat.send_message(f"File too large for Telegram: {os.path.basename(file_path)}")
                os.remove(file_path)
                continue
            with open(file_path, "rb") as docf:
                await update.effective_chat.send_document(document=docf, filename=os.path.basename(file_path))
            os.remove(file_path)
            await msg.delete()
        except Exception as e:
            await update.effective_chat.send_message(f"Failed for {link}:\n{str(e)}")

def main():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable not set!")
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(
        filters.TEXT | filters.Document.MimeType("text/plain"), handle_message
    ))
    application.add_handler(CallbackQueryHandler(inline_callback))
    application.run_polling()

if __name__ == "__main__":
    main()

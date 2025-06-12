import os
import re
import asyncio
import yt_dlp
from telegram import Update, Document
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from tinydb import TinyDB, Query

# --- Persistent User Mode Storage using TinyDB ---
db = TinyDB('user_modes.json')
User = Query()

def set_user_mode(user_id, mode):
    db.upsert({'user_id': user_id, 'mode': mode}, User.user_id == user_id)

def get_user_mode(user_id):
    result = db.get(User.user_id == user_id)
    return result['mode'] if result else 'audio'

# --- YouTube URL extraction ---
YOUTUBE_REGEX = re.compile(
    r'(https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[\w\-\_\?&=]+)'
)

def extract_youtube_links(text):
    """
    Returns a list of all YouTube links found in the given text.
    """
    return YOUTUBE_REGEX.findall(text or "")

def sanitize_filename(name):
    # Remove unsupported characters for file names
    return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)

# --- Downloading functions using yt-dlp ---
async def download_youtube(link, mode):
    """
    Download from YouTube in the desired mode (audio, video_360, video_480).
    Returns the downloaded file's path.
    """
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
            # Ensure filename is safe
            safe_filename = '/tmp/' + sanitize_filename(os.path.basename(filename))
            if filename != safe_filename and os.path.exists(filename):
                os.rename(filename, safe_filename)
            return safe_filename if os.path.exists(safe_filename) else filename
    return await asyncio.to_thread(get_stream)

# --- Command Handlers ---
async def set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = update.message.text.lstrip('/').lower()
    valid_modes = ['audio', 'video_360', 'video_480']
    if mode in valid_modes:
        set_user_mode(str(update.effective_user.id), mode)
        await update.message.reply_text(f"Mode set to {mode}")
    else:
        await update.message.reply_text("Invalid mode. Use /audio, /video_360, or /video_480.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 *YouTube Downloader Bot*\n\n"
        "Send `/audio`, `/video_360`, or `/video_480` to set your mode.\n"
        "Then send YouTube links (plain text or a .txt file with one link per line) and I’ll send you the downloads!\n\n"
        "*Limits*: Only files up to 50MB can be sent due to Telegram restrictions.",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "1. Set your mode: /audio, /video_360, or /video_480\n"
        "2. Send YouTube links (plain text or .txt file)\n"
        "3. I’ll download and send each file. (Max 50MB per file)",
        parse_mode="Markdown"
    )

# --- Main Message Handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    mode = get_user_mode(user_id)
    links = []

    # If a .txt document is sent
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
        # Plain text message
        links = extract_youtube_links(update.message.text or '')

    if not links:
        await update.message.reply_text("No YouTube links found in your message or file.")
        return

    for link in links:
        try:
            await update.message.reply_text(f"Processing: {link}")
            file_path = await download_youtube(link, mode)
            if not os.path.isfile(file_path):
                await update.message.reply_text(f"ERROR: File not found: {file_path}")
                continue
            if os.path.getsize(file_path) == 0:
                await update.message.reply_text(f"ERROR: File is empty: {file_path}")
                os.remove(file_path)
                continue
            if os.path.getsize(file_path) > 50*1024*1024:
                await update.message.reply_text(f"File too large for Telegram: {os.path.basename(file_path)}")
                os.remove(file_path)
                continue
            with open(file_path, "rb") as docf:
                await update.message.reply_document(document=docf, filename=os.path.basename(file_path))
            os.remove(file_path)
        except Exception as e:
            await update.message.reply_text(f"Failed for {link}:\n{str(e)}")

# --- Main Application ---
def main():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable not set!")

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    for cmd in ['audio', 'video_360', 'video_480']:
        application.add_handler(CommandHandler(cmd, set_mode))

    # Message handler for text and .txt files
    application.add_handler(MessageHandler(
        filters.TEXT | filters.Document.MimeType("text/plain"), handle_message
    ))

    application.run_polling()

if __name__ == "__main__":
    main()

import os
import re
import asyncio
from pytube import YouTube
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
    r'(https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[\w\-]+)'
)

def extract_youtube_links(text):
    """
    Returns a list of all YouTube links found in the given text.
    """
    return YOUTUBE_REGEX.findall(text or "")

# --- Downloading functions ---
async def download_youtube(link, mode):
    """
    Download from YouTube in the desired mode (audio, video_360, video_480).
    Returns the downloaded file's path.
    """
    def get_stream():
        yt = YouTube(link)
        if mode == 'audio':
            stream = yt.streams.filter(only_audio=True, file_extension='mp4').first()
            filename = f"/tmp/{yt.title}.mp3"
        elif mode == 'video_360':
            stream = yt.streams.filter(res="360p", progressive=True, file_extension='mp4').first()
            filename = f"/tmp/{yt.title}_360p.mp4"
        elif mode == 'video_480':
            stream = yt.streams.filter(res="480p", progressive=True, file_extension='mp4').first()
            filename = f"/tmp/{yt.title}_480p.mp4"
        else:
            raise Exception("Invalid mode")
        if not stream:
            raise Exception(f"No stream found for mode {mode} ({link})")
        out_file = stream.download(output_path="/tmp")
        # Convert to mp3 if audio
        if mode == 'audio':
            base, _ = os.path.splitext(out_file)
            os.rename(out_file, filename)
        else:
            filename = out_file
        return filename
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
        "Welcome! Send /audio, /video_360, or /video_480 to set your mode.\n"
        "Then send YouTube links (plain text or a .txt file with one link per line) and I’ll send you the downloads!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "1. Set your mode: /audio, /video_360, or /video_480\n"
        "2. Send YouTube links (plain text or .txt file)\n"
        "3. I’ll download and send each file. (Max 50MB per file)"
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
            # Telegram file size limit for bots is 50MB
            if os.path.getsize(file_path) > 50*1024*1024:
                await update.message.reply_text(f"File too large for Telegram: {os.path.basename(file_path)}")
                os.remove(file_path)
                continue
            await update.message.reply_document(open(file_path, "rb"))
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
    from telegram.ext import filters

# ...

    application.add_handler(
    MessageHandler(
        filters.TEXT | (filters.Document.MimeType("text/plain")),
        handle_message
      )
   )

    application.run_polling()

if __name__ == "__main__":
    main()

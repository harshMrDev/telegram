import os
import asyncio
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)
import yt_dlp

TOKEN = os.getenv("TOKEN", "8135426795:AAHsutX7dZX2mIP6dTOz7XeH7bxv8iNN_yM")
MAX_TG_FILESIZE = 48 * 1024 * 1024  # 48MB

WELCOME_TEXT = (
    "Hey! 👋\n"
    "Send me a YouTube link and I'll fetch the video for you.\n"
    "To get audio only, add 'audio' after the link (e.g. https://youtu.be/xyz audio)\n"
    "Note: Only files under 50MB can be sent here."
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT)

def yt_download(url: str) -> str:
    opts = {
        "format": "best[ext=mp4][filesize<50M]/best[filesize<50M]/best",
        "outtmpl": "yt_video.%(ext)s",
        "noplaylist": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        requested = info.get("requested_downloads")
        if requested and "filepath" in requested[0]:
            return requested[0]["filepath"]
        return ydl.prepare_filename(info)

def yt_download_audio(url: str) -> str:
    opts = {
        "format": "bestaudio[filesize<50M]/bestaudio/best",
        "outtmpl": "yt_audio.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # The output file will be yt_audio.mp3 (or .m4a, etc)
        # Find the actual file
        requested = info.get("requested_downloads")
        if requested and "filepath" in requested[0]:
            filepath = requested[0]["filepath"]
            # If postprocessing, change extension to .mp3
            if filepath.endswith(('.webm', '.m4a', '.opus')):
                filepath = os.path.splitext(filepath)[0] + ".mp3"
            return filepath
        return ydl.prepare_filename(info)

async def handle_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split()
    url = parts[0]
    is_audio = len(parts) > 1 and parts[1].lower() == "audio"

    if not any(x in url for x in ("youtube.com", "youtu.be")):
        await update.message.reply_text("❌ That's not a valid YouTube link.")
        return

    await update.message.reply_text(f"⏳ Downloading your {'audio' if is_audio else 'video'}... Please wait.")

    try:
        if is_audio:
            filepath = await asyncio.to_thread(yt_download_audio, url)
            if not os.path.exists(filepath):
                await update.message.reply_text("⚠️ Couldn't find the downloaded file.")
                return

            filesize = os.path.getsize(filepath)
            if filesize > MAX_TG_FILESIZE:
                await update.message.reply_text(
                    "❌ Sorry, the downloaded audio is too large for Telegram (>50MB)."
                )
                os.remove(filepath)
                return

            with open(filepath, "rb") as audio:
                await update.message.reply_audio(audio=audio, caption="✅ Here is your audio!")
            os.remove(filepath)
        else:
            filepath = await asyncio.to_thread(yt_download, url)
            if not os.path.exists(filepath):
                await update.message.reply_text("⚠️ Couldn't find the downloaded file.")
                return

            filesize = os.path.getsize(filepath)
            if filesize > MAX_TG_FILESIZE:
                await update.message.reply_text(
                    "❌ Sorry, the downloaded video is too large for Telegram (>50MB)."
                )
                os.remove(filepath)
                return

            with open(filepath, "rb") as vid:
                await update.message.reply_video(video=vid, caption="✅ Here you go!")
            os.remove(filepath)
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Error: `{e}`", parse_mode="Markdown"
        )

async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_youtube))
    print("Bot started.")
    await app.run_polling(close_loop=False)

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "already running" in str(e):
            import nest_asyncio
            nest_asyncio.apply()
            loop = asyncio.get_event_loop()
            loop.create_task(main())
            loop.run_forever()
        else:
            raise

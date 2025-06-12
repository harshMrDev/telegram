import os
import re
import math
import asyncio
from datetime import datetime
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
async def download_youtube(link, mode, cookies_file=None):
    """
    Download from YouTube in the desired mode (audio, video_360, video_480).
    Returns the downloaded file's path.
    """
    def get_stream():
        outtmpl = "/tmp/%(title).60s.%(ext)s"
        ydl_opts = {}
        if mode == 'audio':
            ydl_opts = {
               # 'cookiefile': 'cookies.txt',
                'format': 'bestaudio/best',
                'outtmpl': outtmpl,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
        elif mode == 'video_360':
            ydl_opts = {
                'format': 'bestvideo[height<=360]+bestaudio/best[height<=360]/best[height<=360]',
                'outtmpl': outtmpl,
                'merge_output_format': 'mp4',
            }
        elif mode == 'video_480':
            ydl_opts = {
                'format': 'bestvideo[height<=480]+bestaudio/best[height<=480]/best[height<=480]',
                'outtmpl': outtmpl,
                'merge_output_format': 'mp4',
            }
        else:
            raise Exception("Invalid mode")
        # Add cookies for anti-bot bypass if present
        if cookies_file and os.path.exists(cookies_file):
            ydl_opts['cookiefile'] = cookies_file
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

# --- File splitter and merger HTML generator ---
class SmartFileSplitter:
    def __init__(self, max_size_mb=49):
        self.max_bytes = max_size_mb * 1024 * 1024
        self.timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')

    def create_merger_html(self, filename, total_parts, file_ext):
        html_content = f'''
<!DOCTYPE html>
<html>
<head>
    <title>Auto File Merger</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 20px auto;
            padding: 20px;
            background: #f0f2f5;
        }}
        .container {{
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }}
        button {{
            background: #0088cc;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
            margin: 10px 0;
        }}
        button:hover {{
            background: #006699;
        }}
        #status {{
            margin: 15px 0;
            padding: 10px;
            border-radius: 5px;
        }}
        .progress {{
            margin: 10px 0;
            padding: 10px;
            background: #e1f5fe;
            border-radius: 5px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h2>📁 Telegram Video Merger</h2>
        <p>This will automatically merge your downloaded parts into the final video.</p>
        <button onclick="mergeParts()">🎯 Merge Video Parts</button>
        <div id="status"></div>
    </div>
    <script>
        const fileName = "{filename}";
        const totalParts = {total_parts};
        const fileExt = "{file_ext}";
        async function mergeParts() {{
            const status = document.getElementById('status');
            status.innerHTML = '⏳ Starting merge process...';
            try {{
                let chunks = [];
                for(let i = 1; i <= totalParts; i++) {{
                    status.innerHTML = `⏳ Reading part ${{i}}/${{totalParts}}...`;
                    const partName = `${{fileName}}_part${{String(i).padStart(3, '0')}}of${{String(totalParts).padStart(3, '0')}}${{fileExt}}`;
                    try {{
                        const response = await fetch(partName);
                        if(!response.ok) throw new Error(`Part ${{i}} not found`);
                        const blob = await response.blob();
                        chunks.push(blob);
                    }} catch(e) {{
                        status.innerHTML = `❌ Error: Make sure all parts are in the same folder as this HTML file`;
                        return;
                    }}
                }}
                status.innerHTML = '⚡ Combining all parts...';
                const finalBlob = new Blob(chunks, {{type: 'video/mp4'}});
                const url = URL.createObjectURL(finalBlob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${{fileName}}_merged${{fileExt}}`;
                status.innerHTML = '✅ Merge complete! Starting download...';
                a.click();
                URL.revokeObjectURL(url);
                status.innerHTML = '✅ Process complete! Check your downloads folder.';
            }} catch(error) {{
                status.innerHTML = `❌ Error: ${{error.message}}`;
            }}
        }}
    </script>
</body>
</html>
'''
        return html_content

    async def split_and_send(self, file_path, update, context):
        if not os.path.exists(file_path):
            await update.message.reply_text("❌ Error: File not found!")
            return

        file_size = os.path.getsize(file_path)
        base_name = os.path.basename(file_path)
        file_name, ext = os.path.splitext(base_name)

        # If file is under 50MB, send directly
        if file_size <= self.max_bytes:
            with open(file_path, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    caption=f"📁 {base_name}\n💾 {file_size / (1024*1024):.1f}MB"
                )
            return

        # Calculate total parts
        total_parts = math.ceil(file_size / self.max_bytes)
        status_msg = await update.message.reply_text(
            f"📁 *Processing:* `{base_name}`\n"
            f"💾 *Size:* `{file_size / (1024*1024):.1f}MB`\n"
            f"📦 *Parts:* `{total_parts}`\n"
            f"⏳ *Starting...*",
            parse_mode='Markdown'
        )

        parts_dir = f"/tmp/split_{self.timestamp}"
        os.makedirs(parts_dir, exist_ok=True)

        try:
            # First, create and send the merger HTML file
            merger_html = self.create_merger_html(file_name, total_parts, ext)
            merger_path = os.path.join(parts_dir, f"{file_name}_merger.html")
            with open(merger_path, 'w') as f:
                f.write(merger_html)
            with open(merger_path, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    caption="📝 Step 1: Download this HTML file"
                )

            # Now split and send the parts
            parts_sent = 0
            with open(file_path, 'rb') as f:
                for part_num in range(1, total_parts + 1):
                    chunk = f.read(self.max_bytes)
                    if not chunk:
                        break

                    part_name = f"{file_name}_part{part_num:03d}of{total_parts:03d}{ext}"
                    part_path = os.path.join(parts_dir, part_name)

                    with open(part_path, 'wb') as part_file:
                        part_file.write(chunk)

                    with open(part_path, 'rb') as part_file:
                        await update.message.reply_document(
                            document=part_file,
                            caption=f"📦 Step 2: Download Part {part_num}/{total_parts}"
                        )

                    parts_sent += 1
                    await status_msg.edit_text(
                        f"📁 *File:* `{base_name}`\n"
                        f"💾 *Size:* `{file_size / (1024*1024):.1f}MB`\n"
                        f"📦 *Progress:* `{parts_sent}/{total_parts}`\n"
                        f"⏳ *Uploading parts...*",
                        parse_mode='Markdown'
                    )

                    os.remove(part_path)

            await update.message.reply_text(
                "📝 *How to get your video:*\n\n"
                "1️⃣ Download *all* parts and the HTML file\n"
                "2️⃣ Put them in the *same folder*\n"
                "3️⃣ Open the HTML file in your browser\n"
                "4️⃣ Click the 'Merge Video Parts' button\n"
                "5️⃣ Wait for automatic download!\n\n"
                "⚠️ *Note:* Keep all files in the same folder!",
                parse_mode='Markdown'
            )

        finally:
            if os.path.exists(parts_dir):
                os.rmdir(parts_dir)

            await status_msg.edit_text(
                f"✅ *Upload Complete!*\n"
                f"📁 *File:* `{base_name}`\n"
                f"💾 *Size:* `{file_size / (1024*1024):.1f}MB`\n"
                f"📦 *Parts:* `{total_parts}`\n"
                f"📝 *Follow the instructions above to merge!*",
                parse_mode='Markdown'
            )

# --- Telegram logic ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 *YouTube Downloader Bot*\n\n"
        "Send a YouTube link (or a .txt file with links).\n"
        "I'll ask for Audio/Video and (if video) ask for quality.\n"
        "*Limits*: Only files up to 50MB can be sent directly. Large files are split and can be merged easily via browser.",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send a YouTube link (or a .txt file with one link per line).\n"
        "I'll ask if you want audio or video, then for video: the quality.\n"
        "Files over 50MB are split into parts and can be merged with an HTML file I provide.",
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
    # If you have a cookies.txt, set the path here
    cookies_file = 'cookies.txt' if os.path.exists('cookies.txt') else None
    for link in links:
        try:
            msg = await update.effective_chat.send_message(f"🎯 Processing: {link}")
            file_path = await download_youtube(link, mode, cookies_file)
            splitter = SmartFileSplitter()
            await splitter.split_and_send(file_path, update, context)
            if os.path.exists(file_path):
                os.remove(file_path)
            await msg.delete()
        except Exception as e:
            await update.effective_chat.send_message(f"❌ Failed for {link}:\n`{str(e)}`", parse_mode='Markdown')

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


import asyncio
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "234063581"))
MAX_BYTES = 49 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("zenvora")


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 تنزيل فيديو", callback_data="video")],
        [InlineKeyboardButton("🎵 استخراج MP3", callback_data="audio")],
        [InlineKeyboardButton("ℹ️ مساعدة", callback_data="help")],
    ])


def quality_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("أفضل جودة", callback_data="q_best"),
         InlineKeyboardButton("720p", callback_data="q_720")],
        [InlineKeyboardButton("480p", callback_data="q_480"),
         InlineKeyboardButton("360p", callback_data="q_360")],
        [InlineKeyboardButton("⬅️ القائمة", callback_data="home")],
    ])


def valid_url(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except ValueError:
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("هذا البوت خاص.")
        return
    context.user_data.clear()
    await update.effective_message.reply_text(
        "🎬 أهلاً بك في Zenvora Media",
        reply_markup=main_menu()
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != OWNER_ID:
        return

    data = q.data

    if data == "home":
        context.user_data.clear()
        await q.edit_message_text("اختر الخدمة:", reply_markup=main_menu())

    elif data == "video":
        context.user_data.clear()
        context.user_data["mode"] = "video"
        await q.edit_message_text("اختر جودة الفيديو:", reply_markup=quality_menu())

    elif data == "audio":
        context.user_data.clear()
        context.user_data.update(mode="audio", awaiting_url=True)
        await q.edit_message_text("أرسل رابط فيديو عام لاستخراج MP3.")

    elif data.startswith("q_"):
        context.user_data.clear()
        context.user_data.update(
            mode="video", quality=data[2:], awaiting_url=True
        )
        await q.edit_message_text("أرسل رابط الفيديو العام.")

    elif data == "help":
        await q.edit_message_text(
            "اختر خدمة، ثم أرسل رابطاً عاماً مدعوماً. "
            "الحد الأقصى التقريبي للملف 49 MB. "
            "استخراج MP3 يتطلب FFmpeg.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ القائمة", callback_data="home")
            ]])
        )


async def process_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    if not user or user.id != OWNER_ID:
        return

    mode = context.user_data.get("mode")
    if mode not in ("video", "audio") or not context.user_data.get("awaiting_url"):
        await message.reply_text("ابدأ من /start واختر خدمة أولاً.")
        return

    url = (message.text or "").strip()
    if not valid_url(url):
        await message.reply_text("أرسل رابطاً صحيحاً يبدأ بـ http:// أو https://")
        return

    status = await message.reply_text("⏳ جارٍ تجهيز التنزيل...")

    try:
        with tempfile.TemporaryDirectory(prefix="zenvora_") as tmp:
            template = str(Path(tmp) / "media.%(ext)s")
            opts = {
                "outtmpl": template,
                "noplaylist": True,
                "max_filesize": MAX_BYTES,
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 20,
                "retries": 2,
            }

            if mode == "audio":
                opts["format"] = "bestaudio/best"
                opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]
            else:
                quality = context.user_data.get("quality", "best")
                heights = {"720": 720, "480": 480, "360": 360}
                height = heights.get(quality)
                if height:
                    opts["format"] = (
                        f"best[height<={height}][ext=mp4]/"
                        f"best[height<={height}]/best"
                    )
                else:
                    opts["format"] = "best[ext=mp4]/best"

            def download():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return Path(ydl.prepare_filename(info))

            path = await asyncio.to_thread(download)

            if mode == "audio":
                matches = list(Path(tmp).glob("*.mp3"))
                if not matches:
                    raise RuntimeError("MP3 output missing; check FFmpeg")
                path = matches[0]

            if not path.exists():
                candidates = [p for p in Path(tmp).iterdir() if p.is_file()]
                if not candidates:
                    raise FileNotFoundError("No downloaded file")
                path = candidates[0]

            if path.stat().st_size > MAX_BYTES:
                raise ValueError("File exceeds upload limit")

            await status.edit_text("📤 جارٍ إرسال الملف...")
            with path.open("rb") as media:
                if mode == "audio":
                    await message.reply_audio(audio=media, filename="Zenvora.mp3")
                else:
                    await message.reply_document(document=media, filename=path.name)

            await status.edit_text("✅ تم إرسال الملف.")
    except Exception as exc:
        log.warning("Download failed: %s", type(exc).__name__)
        await status.edit_text(
            "❌ تعذر التنزيل. تحقق من الرابط العام وحجم الملف. "
            "قد يحتاج استخراج MP3 إلى FFmpeg."
        )
    finally:
        context.user_data.clear()


def main():
    if not TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_link))
    app.run_polling()


if __name__ == "__main__":
    main()
  
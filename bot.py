import os
import asyncio
import logging
import tempfile
from io import BytesIO
from telegram import (
    Update,
    InputFile,
    InputMediaPhoto,
)
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from playwright.async_api import async_playwright

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
URL = "https://gratisfy.xyz/chat"
PORT = int(os.environ.get("PORT", "8080"))
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

streams = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت البث المباشر\n\n"
        "📌 /stream — بدء بث شاشة الموقع (لقطة كل 3 ثوانٍ)\n"
        "📌 /stop  — إيقاف البث\n\n"
        "⚠️ قد يستهلك البث موارد الجهاز."
    )

async def stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id in streams and streams[chat_id].get("active"):
        await update.message.reply_text("⚠️ البث يعمل بالفعل! أرسل /stop لإيقافه.")
        return

    await update.message.reply_text(f"⏳ جاري فتح المتصفح والاتصال بـ {URL} ...")

    try:
        p = await async_playwright().start()

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--no-first-run",
                "--no-zygote",
            ]
        )

        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.goto(URL, wait_until="networkidle", timeout=60000)

        # ─── أول لقطة ───
        first_screenshot = await page.screenshot(type="jpeg", quality=80)

        sent_msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(BytesIO(first_screenshot), filename="live.jpg"),
            caption="📡 جاري البث المباشر...",
        )

        streams[chat_id] = {
            "playwright": p,
            "browser": browser,
            "page": page,
            "message_id": sent_msg.message_id,
            "active": True,
        }

        await update.message.reply_text("✅ تم بدء البث! 🎥\nسأُحدّث نفس الرسالة كل 3 ثوانٍ.")

        task = asyncio.create_task(broadcast_loop(chat_id, context))
        streams[chat_id]["task"] = task

    except Exception as e:
        logger.exception("Error starting stream")
        await update.message.reply_text(f"❌ فشل بدء البث:\n`{e}`", parse_mode=ParseMode.MARKDOWN)

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id not in streams:
        await update.message.reply_text("❌ لا يوجد بث نشط حالياً.")
        return

    streams[chat_id]["active"] = False
    task = streams[chat_id].get("task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    msg_id = streams[chat_id].get("message_id")
    await cleanup_stream(chat_id)

    if msg_id:
        try:
            await context.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption="⏹️ توقف البث المباشر.",
            )
        except Exception:
            pass

    await update.message.reply_text("⏹️ تم إيقاف البث وتصفية المتصفح.")

# ─── حلقة التحديث ───
async def broadcast_loop(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        while streams.get(chat_id, {}).get("active", False):
            page = streams[chat_id]["page"]
            msg_id = streams[chat_id]["message_id"]

            # 1. التقاط لقطة
            screenshot_bytes = await page.screenshot(type="jpeg", quality=80)

            # 2. حفظها في ملف مؤقت (ضروري لـ edit_message_media)
            tmp_path = f"/tmp/live_{chat_id}.jpg"
            with open(tmp_path, "wb") as f:
                f.write(screenshot_bytes)

            try:
                # ✅ التصحيح: استخدام مسار ملف حقيقي مع InputFile
                await context.bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=msg_id,
                    media=InputMediaPhoto(
                        media=InputFile(tmp_path),
                        caption="📡 لقطة مباشرة · مُحدّثة الآن",
                    ),
                )
            finally:
                # حذف الملف المؤقت لتوفير المساحة
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            await asyncio.sleep(3)

    except asyncio.CancelledError:
        logger.info(f"Stream cancelled for {chat_id}")
    except Exception as e:
        logger.error(f"Stream error: {e}")
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ توقف البث بسبب خطأ: {e}")
        except Exception:
            pass
    finally:
        await cleanup_stream(chat_id)

async def cleanup_stream(chat_id: int):
    if chat_id not in streams:
        return
    data = streams.pop(chat_id, {})
    try:
        if "page" in data:
            await data["page"].close()
        if "browser" in data:
            await data["browser"].close()
        if "playwright" in data:
            await data["playwright"].stop()
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

def main():
    if not TOKEN:
        raise RuntimeError("❌ متغير BOT_TOKEN غير موجود!")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stream", stream))
    app.add_handler(CommandHandler("stop", stop))

    if RAILWAY_DOMAIN:
        secret_path = TOKEN.split(":")[-1]
        webhook_url = f"https://{RAILWAY_DOMAIN}/{secret_path}"
        logger.info(f"🚀 Webhook: {webhook_url}")

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=secret_path,
            webhook_url=webhook_url,
            drop_pending_updates=True,
        )
    else:
        logger.warning("⚠️ RAILWAY_PUBLIC_DOMAIN غير موجود! سيعمل بالـ Polling.")
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

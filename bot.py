import os
import asyncio
import logging
from io import BytesIO
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── الإعدادات ───
TOKEN = os.environ.get("BOT_TOKEN")
URL = "https://gratisfy.xyz/chat"
PORT = int(os.environ.get("PORT", "8080"))

# Railway يضع اسم النطاق هنا تلقائياً إذا فعّلت Public Domain
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

# تخزين حالة البث
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

        streams[chat_id] = {
            "playwright": p,
            "browser": browser,
            "page": page,
            "active": True,
        }

        await update.message.reply_text("✅ تم بدء البث! 🎥\nسأرسل لقطة كل 3 ثوانٍ.")

        task = asyncio.create_task(broadcast_loop(chat_id, context))
        streams[chat_id]["task"] = task

    except Exception as e:
        logger.exception("Error starting stream")
        await update.message.reply_text(f"❌ فشل بدء البث:\n`{e}`", parse_mode="Markdown")

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

    await cleanup_stream(chat_id)
    await update.message.reply_text("⏹️ تم إيقاف البث وتصفية المتصفح.")

async def broadcast_loop(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        while streams.get(chat_id, {}).get("active", False):
            page = streams[chat_id]["page"]
            screenshot = await page.screenshot(type="jpeg", quality=80)

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(BytesIO(screenshot), filename="live.jpg"),
                caption="📡 لقطة مباشرة"
            )

            await asyncio.sleep(3)

    except asyncio.CancelledError:
        logger.info(f"Stream cancelled for {chat_id}")
    except Exception as e:
        logger.error(f"Stream error: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ توقف البث: {e}")
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

    # ─── Webhook mode (مطلوب على Railway) ───
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
        # احتياطي محلي فقط
        logger.warning("⚠️ RAILWAY_PUBLIC_DOMAIN غير موجود! سيعمل بالـ Polling.")
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

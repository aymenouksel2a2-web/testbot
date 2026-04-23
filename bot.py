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
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

# تخزين حالة البث لكل مستخدم
streams = {}  # chat_id -> {browser, page, task, active}

# ─── الأوامر ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت البث المباشر\n\n"
        "📌 /stream — بدء بث شاشة الموقع (لقطة كل 3 ثوانٍ)\n"
        "📌 /stop  — إيقاف البث\n\n"
        "⚠️ قد يستهلك البث موارد الجهاز."
    )

async def stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id in streams:
        await update.message.reply_text("⚠️ البث يعمل بالفعل! أرسل /stop لإيقافه.")
        return

    await update.message.reply_text(f"⏳ جاري فتح المتصفح والاتصال بـ {URL} ...")

    try:
        # تشغيل Playwright
        p = await async_playwright().start()

        # فتح Chromium في وضع headless (بدون واجهة) — ضروري على Railway
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )

        # فتح صفحة بالأبعاد المطلوبة
        page = await browser.new_page(viewport={"width": 1280, "height": 720})

        # الذهاب للموقع والانتظار حتى يستقر
        await page.goto(URL, wait_until="networkidle", timeout=60000)

        # حفظ البيانات لإغلاقها لاحقاً
        streams[chat_id] = {
            "playwright": p,
            "browser": browser,
            "page": page,
            "active": True,
        }

        await update.message.reply_text("✅ تم بدء البث! 🎥\nسأرسل لك لقطة كل 3 ثوانٍ.")

        # تشغيل البث في الخلفية
        task = asyncio.create_task(broadcast_loop(chat_id, context))
        streams[chat_id]["task"] = task

    except Exception as e:
        logger.error(f"Error starting stream: {e}")
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

# ─── حلقة البث ───
async def broadcast_loop(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        while streams.get(chat_id, {}).get("active", False):
            page = streams[chat_id]["page"]

            # التقاط لقطة الشاشة كـ JPEG (أخف من PNG)
            screenshot_bytes = await page.screenshot(type="jpeg", quality=80)

            # إرسال الصورة
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(BytesIO(screenshot_bytes), filename="live.jpg"),
                caption="📡 لقطة مباشرة"
            )

            # انتظار 3 ثوانٍ قبل اللقطة التالية
            await asyncio.sleep(3)

    except asyncio.CancelledError:
        logger.info(f"Stream cancelled for chat_id={chat_id}")
    except Exception as e:
        logger.error(f"Stream error: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ توقف البث بسبب خطأ: {e}")
    finally:
        await cleanup_stream(chat_id)

# ─── تنظيف الموارد ───
async def cleanup_stream(chat_id: int):
    if chat_id not in streams:
        return

    data = streams.pop(chat_id)
    try:
        if "page" in data:
            await data["page"].close()
        if "browser" in data:
            await data["browser"].close()
        if "playwright" in data:
            await data["playwright"].stop()
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

# ─── التشغيل ───
def main():
    if not TOKEN:
        logger.error("❌ لم يتم تعيين BOT_TOKEN")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stream", stream))
    app.add_handler(CommandHandler("stop", stop))

    # نشر البوت عبر Webhook (مُحسّن لـ Railway)
    if RAILWAY_DOMAIN:
        secret_path = TOKEN.split(":")[-1]
        webhook_url = f"https://{RAILWAY_DOMAIN}/{secret_path}"
        logger.info(f"🚀 Webhook: {webhook_url}")

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=secret_path,
            webhook_url=webhook_url,
        )
    else:
        logger.info("🔄 Running in local polling mode...")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

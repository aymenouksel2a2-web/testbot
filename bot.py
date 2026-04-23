import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# إعداد اللوق
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# قراءة التوكن من متغيرات البيئة (للأمان)
TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً! أنا بوت يعمل على Railway 🚂")

def main():
    if not TOKEN:
        logger.error("❌ لم يتم العثور على BOT_TOKEN في متغيرات البيئة")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    # Railway يوفر متغير PORT واسم نطاق عام (Public Domain)
    PORT = int(os.environ.get("PORT", "8080"))
    RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

    if RAILWAY_DOMAIN:
        # ✅ نستخدم Webhook على Railway (أفضل وأستر للاستضافة)
        secret_path = TOKEN.split(":")[-1]  # جزء من التوكن كمسار سري
        webhook_url = f"https://{RAILWAY_DOMAIN}/{secret_path}"
        logger.info(f"🚀 تشغيل Webhook على: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=secret_path,
            webhook_url=webhook_url,
        )
    else:
        # 🖥️ نستخدم Polling محلياً أثناء التطوير
        logger.info("🔄 تشغيل Polling (وضع التطوير)...")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

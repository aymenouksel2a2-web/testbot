import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ─── الإعدادات ───
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")           # توكن البوت (مطلوب)
PORT = int(os.environ.get("PORT", "8080"))    # منفذ Railway (افتراضي 8080)

# Railway يُوفّر هذا المتغير تلقائياً بعد النشر
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
WEBHOOK_URL = f"https://{RAILWAY_DOMAIN}/" if RAILWAY_DOMAIN else os.environ.get("WEBHOOK_URL")


# ─── الأوامر ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"أهلاً {user.first_name}! 👋\n"
        "أنا بوت يعمل على Railway 🚀\n"
        "جرب إرسال أي رسالة وسأرددها."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 الأوامر المتوفرة:\n"
        "/start - بدء المحادثة\n"
        "/help - عرض هذه القائمة"
    )


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"رددت: {update.message.text}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("حدث خطأ: %s", context.error)


# ─── التشغيل ───
def main():
    if not TOKEN:
        raise ValueError("❌ لم يتم تعيين متغير البيئة BOT_TOKEN!")

    app = Application.builder().token(TOKEN).build()

    # تسجيل المعالجات
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    app.add_error_handler(error_handler)

    # التشغيل (Webhook على Railway - Polling محلياً)
    if WEBHOOK_URL and WEBHOOK_URL != "https:///":
        logger.info(f"✅ تشغيل Webhook: {WEBHOOK_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL,
        )
    else:
        logger.info("🔄 تشغيل Polling (وضع التطوير)...")
        app.run_polling()


if __name__ == "__main__":
    main()

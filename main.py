import logging
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# تفعيل السجل Logs لتتبع الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# جلب التوكن من متغيرات البيئة (سنضيفه لاحقاً في Railway)
TOKEN = os.environ.get("TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرد على أمر /start"""
    await update.message.reply_text("أهلاً بك! 🤖\nأنا بوت يعمل على Railway بنجاح.")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رد الرسائل النصية"""
    user_text = update.message.text
    await update.message.reply_text(f"📩 أرسلت: {user_text}")

def main():
    if not TOKEN:
        raise ValueError("❌ لم يتم العثور على TOKEN! تأكد من إضافته في متغيرات البيئة.")

    # بناء التطبيق
    application = Application.builder().token(TOKEN).build()

    # إضافة الأوامر
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

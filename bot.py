import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# إعداد السجل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# التوكن من متغيرات البيئة
TOKEN = os.environ.get("BOT_TOKEN")

# ============================
# الأوامر
# ============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البداية /start"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 أهلاً {user.first_name}!\n\n"
        f"أنا بوت تيليغرام جاهز للعمل 🤖\n\n"
        f"الأوامر المتاحة:\n"
        f"/start - بدء البوت\n"
        f"/help - المساعدة\n"
        f"/about - معلومات عني"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر المساعدة /help"""
    await update.message.reply_text(
        "📚 *المساعدة*\n\n"
        "يمكنك إرسال أي رسالة وسأرد عليك!\n\n"
        "الأوامر:\n"
        "/start - بدء البوت\n"
        "/help - عرض المساعدة\n"
        "/about - معلومات عن البوت",
        parse_mode='Markdown'
    )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /about"""
    await update.message.reply_text(
        "🤖 *معلومات البوت*\n\n"
        "• تم بناؤه بـ Python\n"
        "• يعمل على Railway\n"
        "• مكتبة python-telegram-bot\n\n"
        "👨‍💻 تم التطوير بكل ❤️",
        parse_mode='Markdown'
    )

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرد على الرسائل العادية"""
    message = update.message.text
    await update.message.reply_text(
        f"📨 استلمت رسالتك:\n\n{message}\n\n"
        f"كيف يمكنني مساعدتك؟ 😊"
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأخطاء"""
    logger.error(f"حدث خطأ: {context.error}")

# ============================
# تشغيل البوت
# ============================

def main():
    if not TOKEN:
        logger.error("❌ لم يتم العثور على BOT_TOKEN!")
        return

    # إنشاء التطبيق
    app = Application.builder().token(TOKEN).build()

    # إضافة المعالجات
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # معالج الأخطاء
    app.add_error_handler(error_handler)

    logger.info("✅ البوت يعمل...")
    
    # تشغيل البوت
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

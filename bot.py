import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

# إعداد اللوج
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("يا قحبة حط التوكن في الـ Variables !")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ **البوت شغال يا ابن المتناكة!**\n\n"
        "Python + Railway = أقوى combination 🔥\n"
        "جرب الأوامر: /ping , /insult"
    )

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 **Pong يا زبي!** البوت أسرع من أمك")

async def insult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("كسمك يا أكثر منيوك في الشات 😂")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    
    if any(word in text for word in ["كسمك", "شرموط", "منيوك", "قحبة"]):
        await update.message.reply_text("كسم أمك أنت يا أوسخ واحد في التيليجرام 🤣")
    else:
        await update.message.reply_text(f"وصلتك يا شرموط: {update.message.text}")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("insult", insult))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 البوت اشتغل بنجاح يا قحبة!")
    app.run_polling()

if __name__ == "__main__":
    main()

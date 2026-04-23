from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import subprocess
import os
import asyncio

TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"   # غيره

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ **Staffa Railway Bot Online**\nأنا تحت أمرك يا معلم 🔥")

async def shell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم: /shell <command>")
        return
    
    command = " ".join(context.args)
    try:
        result = subprocess.getoutput(command)
        if len(result) > 4000:
            await update.message.reply_text("النتيجة طويلة جداً، هيتم إرسالها كملف.")
            with open("output.txt", "w", encoding="utf-8") as f:
                f.write(result)
            await update.message.reply_document(open("output.txt", "rb"))
            os.remove("output.txt")
        else:
            await update.message.reply_text(f"**Output:**\n```{result}```", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document and not update.message.photo:
        await update.message.reply_text("أرسل ملف أو صورة عشان أرفعه")
        return
    file = await update.message.document.get_file() if update.message.document else await update.message.photo[-1].get_file()
    await file.download_to_drive("uploaded_file")
    await update.message.reply_text("✅ تم رفع الملف بنجاح!")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("shell", shell))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, upload))
    
    print("🚀 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

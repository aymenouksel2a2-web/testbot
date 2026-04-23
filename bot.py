import os
import re
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- الثوابت ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# --- دوال الاستخراج ---
def extract_text_from_url(url: str) -> str:
    """
    تقوم بجلب محتوى صفحة الويب من الرابط المحدد واستخراج النص النقي منها.
    """
    try:
        # إضافة headers لتقليد متصفح حقيقي (لتجنب الحظر من بعض المواقع)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        # جلب محتوى الصفحة
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()  # للتأكد من نجاح الطلب
        
        # تحليل محتوى HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # استخراج النص الخام من كامل الصفحة
        raw_text = soup.get_text(separator='\n', strip=True)
        
        # تنظيف النص (إزالة الأسطر الفارغة المتعددة)
        cleaned_lines = [line for line in raw_text.splitlines() if line.strip()]
        cleaned_text = '\n'.join(cleaned_lines)
        
        return cleaned_text
    except Exception as e:
        return f"❌ فشل استخراج النص: {e}"

# --- دوال التعامل مع تيليجرام ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرد على أمر /start"""
    await update.message.reply_text(
        "👋 مرحبًا! أنا بوت استخراج النصوص.\n"
        "أرسل لي رابط (URL) لأي صفحة ويب، وسأقوم باستخراج النص الموجود فيها وإرساله لك.\n"
        "✍️ مثال: https://example.com"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل النصية الواردة من المستخدم"""
    user_text = update.message.text
    
    # التعبير النمطي للبحث عن روابط URL في النص
    url_pattern = r'(https?://[^\s]+)'
    urls = re.findall(url_pattern, user_text)
    
    if urls:
        # إرسال رسالة انتظار
        await update.message.reply_text("⏳ جاري استخراج النص من الرابط، قد يستغرق ذلك لحظة...")
        
        # معالجة أول رابط فقط لتجنب الإرباك
        url = urls[0]
        extracted_text = extract_text_from_url(url)
        
        # تيليجرام لديه حد أقصى لطول الرسالة (4096 حرفًا)
        max_length = 4096
        if len(extracted_text) > max_length:
            # إذا كان النص طويلاً، نرسل جزءًا منه
            preview = extracted_text[:max_length-200] + "...\n\n[تم اقتطاع النص لأنه طويل جدًا]"
            await update.message.reply_text(preview)
        else:
            await update.message.reply_text(extracted_text)
    else:
        await update.message.reply_text("🤔 لم أجد رابطًا صالحًا في رسالتك. من فضلك أرسل رابط URL.")

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    app = Application.builder().token(TOKEN).build()
    
    # إضافة handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # بدء البوت
    print("🤖 البوت يعمل الآن...")
    app.run_polling()

if __name__ == "__main__":
    main()

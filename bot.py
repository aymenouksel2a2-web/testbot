import os
import re
from requests_html import HTMLSession
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- إعدادات ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# --- دالة الاستخراج باستخدام requests-html (تدعم JavaScript) ---
def extract_text_with_requests_html(url: str) -> str:
    """
    تستخدم requests-html لتحميل الصفحة وتنفيذ JavaScript ثم استخراج النص.
    """
    session = HTMLSession()
    try:
        # جلب الصفحة مع انتظار تحميل JavaScript (timeout=20 ثانية)
        r = session.get(url)
        r.html.render(timeout=20, sleep=3)  # sleep=3 ينتظر 3 ثوانٍ بعد التحميل
        
        # استخراج النص الكامل
        all_text = r.html.text
        
        # البحث عن وقت المختبر بصيغة HH:MM:SS
        time_pattern = r'\b\d{1,2}:\d{2}:\d{2}\b'
        time_matches = re.findall(time_pattern, all_text)
        
        if time_matches:
            lab_time = time_matches[0]
            return f"⏱️ وقت المختبر: {lab_time}\n\n{all_text}"
        else:
            return f"⚠️ لم يتم العثور على وقت محدد.\n\n{all_text}"
    except Exception as e:
        return f"❌ فشل استخراج النص: {e}"
    finally:
        session.close()

# --- دوال تيليجرام ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 مرحبًا! أنا بوت استخراج النصوص.\n"
        "أرسل لي رابط (URL) لأي صفحة ويب، وسأقوم باستخراج النص الموجود فيها وإرساله لك.\n"
        "✍️ مثال: https://www.skills.google/focuses/19146"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    url_pattern = r'(https?://[^\s]+)'
    urls = re.findall(url_pattern, user_text)
    
    if urls:
        await update.message.reply_text("⏳ جاري استخراج النص من الرابط... قد يستغرق ذلك 20-30 ثانية.")
        
        url = urls[0]
        # تشغيل الدالة في thread منفصل لأنها دالة عادية (غير async)
        extracted_text = await asyncio.to_thread(extract_text_with_requests_html, url)
        
        max_length = 4096
        if len(extracted_text) > max_length:
            preview = extracted_text[:max_length-200] + "...\n\n[تم اقتطاع النص لأنه طويل جدًا]"
            await update.message.reply_text(preview)
        else:
            await update.message.reply_text(extracted_text)
    else:
        await update.message.reply_text("🤔 لم أجد رابطًا صالحًا في رسالتك. من فضلك أرسل رابط URL.")

def main():
    if not TOKEN:
        print("❌ خطأ: لم يتم تعيين TELEGRAM_BOT_TOKEN في متغيرات البيئة.")
        return
    
    # إضافة drop_pending_updates لتجنب خطأ التعارض "Conflict: terminated by other getUpdates request"
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🤖 البوت يعمل الآن...")
    app.run_polling(drop_pending_updates=True)  # <-- هذا يحل مشكلة التعارض

if __name__ == "__main__":
    import asyncio
    main()

import telebot
import os
import time
import threading
from playwright.sync_api import sync_playwright
import re

BOT_TOKEN = os.environ.get('BOT_TOKEN')

if not BOT_TOKEN:
    print("خطأ: لم يتم العثور على BOT_TOKEN.")
    exit()

bot = telebot.TeleBot(BOT_TOKEN)

# قاموس لتتبع العمليات النشطة
active_tasks = {}

# دالة للتحقق من صحة الرابط
def is_valid_url(url):
    regex = re.compile(
        r'^(?:http|ftp)s?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(regex, url) is not None

# الدالة المسؤولة عن فتح المتصفح واستخراج النصوص والصور
def extract_content(chat_id, url):
    active_tasks[chat_id] = True
    
    try:
        with sync_playwright() as p:
            # تشغيل المتصفح في وضع مخفي
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # تعيين أبعاد الشاشة
            page.set_viewport_size({"width": 1280, "height": 720})
            
            bot.send_message(chat_id, "⏳ جاري فتح الرابط واستخراج النصوص (قد يستغرق بضع ثواني لضمان تحميل كل شيء)...")
            
            # الدخول للرابط والانتظار حتى يكتمل تحميل كل السكريبتات الديناميكية (networkidle)
            page.goto(url, timeout=60000, wait_until="networkidle") 
            # زيادة وقت الانتظار قليلاً لضمان عمل العدادات وظهور الوقت المتبقي
            time.sleep(5) 
            
            # 1. التقاط لقطة شاشة واحدة
            screenshot_bytes = page.screenshot(full_page=False)
            bot.send_photo(chat_id, screenshot_bytes, caption="📸 لقطة شاشة للصفحة")
            
            # 2. استخراج جميع النصوص الموجودة في وسم Body
            text_content = page.locator("body").inner_text()
            
            # إرسال النصوص
            if not text_content or not text_content.strip():
                bot.send_message(chat_id, "⚠️ لم أتمكن من العثور على نصوص واضحة في هذه الصفحة.")
            else:
                bot.send_message(chat_id, "📄 **النصوص المستخرجة من الصفحة:**", parse_mode="Markdown")
                
                # تقسيم النص وإرساله على دفعات (تيليجرام يمنع الرسائل أطول من 4096 حرف)
                max_length = 4000
                for i in range(0, len(text_content), max_length):
                    chunk = text_content[i:i+max_length]
                    bot.send_message(chat_id, f"```\n{chunk}\n```", parse_mode="Markdown")
                    time.sleep(0.5) # فاصل زمني بسيط لتجنب حظر الإرسال من تيليجرام
            
            browser.close()
            bot.send_message(chat_id, "✅ اكتملت المهمة!")
                
    except Exception as e:
        # تقصير رسالة الخطأ
        error_msg = str(e)[:1000]
        bot.send_message(chat_id, f"❌ حدث خطأ أثناء فتح الرابط:\n{error_msg}")
    finally:
        if chat_id in active_tasks:
            del active_tasks[chat_id]

# الرد على أمر البدء
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "مرحباً! 🤖\n\n"
        "أرسل لي أي رابط (URL) وسأقوم بالدخول إليه واستخراج **جميع النصوص** الموجودة فيه مع لقطة شاشة.\n"
    )
    bot.reply_to(message, welcome_text)

# الرد على الروابط
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    text = message.text
    chat_id = message.chat.id
    
    if is_valid_url(text):
        if chat_id in active_tasks:
            bot.reply_to(message, "⚠️ أنا أقوم بمعالجة رابط حالياً، انتظر حتى أنتهي من فضلك.")
            return
        
        # تشغيل المتصفح في مسار منفصل (Thread)
        thread = threading.Thread(target=extract_content, args=(chat_id, text))
        thread.start()
    else:
        bot.reply_to(message, "عذراً، هذا ليس رابطاً صحيحاً. يرجى إرسال رابط يبدأ بـ http:// أو https://")

if __name__ == "__main__":
    print("جاري تشغيل البوت...")
    bot.infinity_polling()

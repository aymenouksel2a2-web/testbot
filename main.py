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

# قاموس لتتبع عمليات التصوير النشطة (لتتمكن من إيقافها)
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

# الدالة المسؤولة عن فتح المتصفح والتقاط الصور
def take_screenshots(chat_id, url):
    active_tasks[chat_id] = True
    
    try:
        with sync_playwright() as p:
            # تشغيل المتصفح في وضع مخفي
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # تعيين أبعاد الشاشة
            page.set_viewport_size({"width": 1280, "height": 720})
            
            bot.send_message(chat_id, "⏳ جاري فتح الرابط وبدء التقاط الشاشة كل 3 ثواني...\n(أرسل /stop في أي وقت للإيقاف)")
            page.goto(url, timeout=60000) # مهلة 60 ثانية لفتح الموقع
            
            # التقاط 20 صورة كحد أقصى (دقيقة كاملة) كإجراء أمان لمنع التحميل الزائد
            count = 0
            while active_tasks.get(chat_id) and count < 20:
                screenshot_bytes = page.screenshot(full_page=False)
                bot.send_photo(chat_id, screenshot_bytes, caption=f"لقطة شاشة رقم {count+1}")
                time.sleep(3) # الانتظار 3 ثواني
                count += 1
            
            browser.close()
            
            if count >= 20:
                bot.send_message(chat_id, "✅ تم التقاط 20 صورة (تم التوقف تلقائياً لحماية الخادم).")
            else:
                bot.send_message(chat_id, "🛑 تم إيقاف التقاط الشاشة بنجاح.")
                
    except Exception as e:
        # تقصير رسالة الخطأ لتجنب حظر الإرسال من تيليجرام (حد 4096 حرف)
        error_msg = str(e)[:1000]
        bot.send_message(chat_id, f"❌ حدث خطأ أثناء فتح الرابط:\n{error_msg}")
    finally:
        if chat_id in active_tasks:
            del active_tasks[chat_id]

# الرد على أمر البدء
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً! 📸\nأرسل لي أي رابط (URL) وسأقوم بالدخول إليه وإرسال لقطة شاشة كل 3 ثواني.\n\nلإيقاف العملية في أي وقت، أرسل الأمر /stop")

# أمر الإيقاف
@bot.message_handler(commands=['stop'])
def stop_screenshotting(message):
    chat_id = message.chat.id
    if chat_id in active_tasks:
        active_tasks[chat_id] = False
        bot.reply_to(message, "⏳ جاري إيقاف عملية التقاط الشاشة...")
    else:
        bot.reply_to(message, "لا توجد أي عملية التقاط شاشة نشطة حالياً.")

# الرد على الروابط
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    text = message.text
    chat_id = message.chat.id
    
    if is_valid_url(text):
        if chat_id in active_tasks:
            bot.reply_to(message, "⚠️ الرجاء إيقاف العملية الحالية أولاً باستخدام /stop قبل إرسال رابط جديد.")
            return
        
        # تشغيل المتصفح في مسار منفصل (Thread) حتى لا يتعطل البوت عن استقبال الأوامر
        thread = threading.Thread(target=take_screenshots, args=(chat_id, text))
        thread.start()
    else:
        bot.reply_to(message, "عذراً، هذا ليس رابطاً صحيحاً. يرجى إرسال رابط يبدأ بـ http:// أو https://")

if __name__ == "__main__":
    print("جاري تشغيل البوت...")
    bot.infinity_polling()

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

# قاموس لتتبع العمليات النشطة لمنع تداخل الطلبات
active_tasks = {}

# دالة للتحقق من صحة الرابط
def is_valid_url(url):
    regex = re.compile(r'^(?:http|ftp)s?://', re.IGNORECASE)
    return re.match(regex, url) is not None

# الدالة المسؤولة عن الدخول واستخراج وقت اللاب تحديداً
def extract_lab_time(chat_id, url):
    active_tasks[chat_id] = True
    
    try:
        with sync_playwright() as p:
            # تشغيل المتصفح في وضع مخفي
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # تعيين أبعاد الشاشة
            page.set_viewport_size({"width": 1280, "height": 720})
            
            bot.send_message(chat_id, "⏳ جاري الدخول للصفحة والبحث عن وقت اللاب الحقيقي...")
            
            # الدخول للرابط والانتظار حتى اكتمال تحميل الشبكة لتجنب أخذ نصوص غير مكتملة
            page.goto(url, timeout=60000, wait_until="networkidle") 
            time.sleep(3) # إعطاء مهلة إضافية ليظهر العداد الفعلي
            
            # نمط البحث (Regex): تم إجبار البحث على 6 أرقام بالضبط (مثال 03:00:00) 
            # هذا يمنع البوت من قراءة النص "1:15:00" الموجود كـ(مثال) داخل تعليمات اللاب.
            time_regex = r"\d{2}:\d{2}:\d{2}"
            
            lab_time = None
            
            # استخراج النص الكامل للصفحة للبحث فيه بدقة
            full_page_text = page.locator("body").inner_text()
            
            # 1. نبحث أولاً عن الوقت المرتبط مباشرة بكلمة Time limit لضمان الدقة القصوى
            match_exact = re.search(r"(\d{2}:\d{2}:\d{2})\s*(?:\n\s*)?(?:\?|Time limit)", full_page_text, re.IGNORECASE)
            
            if match_exact:
                lab_time = match_exact.group(1)
            else:
                # 2. في حال لم يجده بالشكل السابق، يبحث عن أي عداد بـ 6 أرقام (03:00:00)
                fallback_match = re.search(time_regex, full_page_text)
                if fallback_match:
                    lab_time = fallback_match.group(0)
            
            # التقاط صورة للصفحة كمرجع
            screenshot_bytes = page.screenshot(full_page=False)
            bot.send_photo(chat_id, screenshot_bytes, caption="📸 لقطة شاشة للصفحة")
            
            # إرسال النتيجة المستخرجة
            if lab_time:
                bot.send_message(chat_id, f"✅ **وقت اللاب المستخرج:**\n\n⏱️ `{lab_time}`", parse_mode="Markdown")
            else:
                bot.send_message(chat_id, "⚠️ لم أتمكن من العثور على وقت اللاب في الصفحة. تأكد من أن العداد يظهر دون الحاجة لتسجيل دخول إضافي.")
            
            browser.close()
                
    except Exception as e:
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
        "أرسل لي رابط اللاب (Google Skills) وسأقوم باستخراج **وقت اللاب المخصص (Time limit)** فقط وإرساله لك مع لقطة شاشة.\n"
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
        
        # تشغيل العملية في مسار منفصل (Thread)
        thread = threading.Thread(target=extract_lab_time, args=(chat_id, text))
        thread.start()
    else:
        bot.reply_to(message, "عذراً، هذا ليس رابطاً صحيحاً. يرجى إرسال رابط يبدأ بـ http:// أو https://")

if __name__ == "__main__":
    print("جاري تشغيل البوت...")
    bot.infinity_polling()

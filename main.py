import telebot
import os
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
            
            bot.send_message(chat_id, "⏳ جاري الدخول للصفحة والبحث عن وقت اللاب (Time limit)...")
            
            # الدخول للرابط
            page.goto(url, timeout=60000) 
            
            # نمط البحث (Regex) الذي يمثل شكل العداد، مثلاً: 03:00:00 أو 1:30:00
            time_regex = r"\d{1,2}:\d{2}:\d{2}"
            
            # توجيه Playwright للبحث عن أي عنصر في الصفحة يطابق شكل الوقت
            # هذه الطريقة تخترق الـ Shadow DOM الذي تستخدمه منصة Google Skills
            time_locator = page.locator(f"text=/{time_regex}/").first
            
            lab_time = None
            try:
                # انتظار ظهور الوقت على الشاشة لمدة أقصاها 20 ثانية لضمان اكتمال السكريبتات
                time_locator.wait_for(timeout=20000)
                # سحب النص المكتشف
                full_text = time_locator.inner_text()
                # استخلاص الأرقام (الوقت) بدقة من النص المكتشف
                match = re.search(time_regex, full_text)
                if match:
                    lab_time = match.group(0)
            except Exception as wait_err:
                pass # في حال لم يجده خلال 20 ثانية
            
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

import os
import telebot
import re
from playwright.sync_api import sync_playwright

# جلب التوكن والتأكد من وجوده
BOT_TOKEN = os.environ.get('BOT_TOKEN')

if not BOT_TOKEN:
    print("❌ خطأ قاتل: لم يتم العثور على التوكن (BOT_TOKEN) في متغيرات البيئة!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً بك! أنا أعمل بنجاح 🚀\nأرسل أمر /get_time لجلب الوقت من Google Skills.")

@bot.message_handler(commands=['get_time'])
def fetch_lab_time(message):
    url = "https://www.skills.google/focuses/19146?parent=catalog"
    
    msg = bot.reply_to(message, "⏳ جاري الاتصال بالموقع واستخراج الوقت...")
    
    try:
        with sync_playwright() as p:
            # تشغيل المتصفح بأقصى إعدادات توفير الذاكرة لمنع الانهيار
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process"
                ]
            )
            
            context = browser.new_context()
            
            # حظر تحميل الصور والتصميمات لتسريع البوت جداً
            context.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] else route.continue_())
            
            page = context.new_page()
            page.set_default_timeout(30000) # أقصى مدة للانتظار 30 ثانية
            
            # تحميل الصفحة والانتظار 3 ثوانٍ
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000) 
            
            # استخراج النص والبحث عن الوقت
            page_text = page.inner_text("body")
            time_pattern = re.search(r'\d{2}:\d{2}:\d{2}', page_text)
            
            if time_pattern:
                extracted_time = time_pattern.group(0)
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                      text=f"✅ تم العثور على الوقت بنجاح!\n\n⏱️ الوقت المخصص: **{extracted_time}**", parse_mode="Markdown")
            else:
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                      text="❌ لم يتم العثور على الوقت في الصفحة. (قد يكون الموقع غير تصميمه)")
            
            browser.close()
            
    except Exception as e:
        print(f"Error occurred: {str(e)}") # طباعة الخطأ في سجلات Railway
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                              text=f"⚠️ حدث خطأ أثناء تشغيل المتصفح: {str(e)}")

print("✅ Bot is successfully running with Docker! Waiting for messages...")
bot.infinity_polling()

import os
import telebot
import re
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً! أرسل أمر /get_time لجلب الوقت من موقع Google Skills.")

@bot.message_handler(commands=['get_time'])
def fetch_lab_time(message):
    url = "https://www.skills.google/focuses/19146?parent=catalog"
    
    # إشعار المستخدم بأن العملية قد تستغرق وقتاً قليلاً
    msg = bot.reply_to(message, "⏳ جاري تشغيل المتصفح وتحليل الصفحة (قد يستغرق بضع ثوانٍ)...")
    
    try:
        # تشغيل Playwright لفتح المتصفح
        with sync_playwright() as p:
            # إطلاق متصفح Chromium في وضع الخلفية (headless=True)
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # الدخول للرابط والانتظار حتى يتم تحميل كافة سكريبتات الصفحة (networkidle)
            page.goto(url, wait_until="networkidle")
            
            # سحب النص الظاهر للمستخدم بعد اكتمال التحميل
            page_text = page.inner_text("body")
            
            # البحث عن نمط الوقت باستخدام Regex
            time_pattern = re.search(r'\d{2}:\d{2}:\d{2}', page_text)
            
            if time_pattern:
                extracted_time = time_pattern.group(0)
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                      text=f"✅ تم العثور على الوقت بنجاح!\n\n⏱️ الوقت المخصص للمختبر هو: **{extracted_time}**", parse_mode="Markdown")
            else:
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                      text="❌ لم أتمكن من العثور على الوقت. قد يكون الموقع قام بتغيير هيكله.")
            
            # إغلاق المتصفح لتحرير الموارد
            browser.close()
            
    except Exception as e:
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                              text=f"⚠️ حدث خطأ أثناء تشغيل المتصفح: {e}")

print("Bot is running with Playwright...")
bot.infinity_polling()

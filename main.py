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
    
    msg = bot.reply_to(message, "⏳ جاري تشغيل المتصفح وتحليل الصفحة (قد يستغرق بضع ثوانٍ)...")
    
    try:
        with sync_playwright() as p:
            # إضافة إعدادات ضرورية جداً لمنع انهيار المتصفح في خوادم Railway
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )
            page = browser.new_page()
            
            # تغيير طريقة الانتظار لكي لا يعلق البوت بسبب سكريبتات جوجل
            # مع وضع حد أقصى للوقت (60 ثانية)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # إجبار المتصفح على الانتظار 3 ثوانٍ إضافية لضمان عمل جافا سكريبت وظهور الوقت
            page.wait_for_timeout(3000)
            
            # سحب النص الظاهر للمستخدم
            page_text = page.inner_text("body")
            
            # البحث عن نمط الوقت باستخدام Regex
            time_pattern = re.search(r'\d{2}:\d{2}:\d{2}', page_text)
            
            if time_pattern:
                extracted_time = time_pattern.group(0)
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                      text=f"✅ تم العثور على الوقت بنجاح!\n\n⏱️ الوقت المخصص للمختبر هو: **{extracted_time}**", parse_mode="Markdown")
            else:
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                      text="❌ لم أتمكن من العثور على الوقت. قد يكون الموقع قام بتغيير هيكله أو يتطلب تسجيل دخول.")
            
            browser.close()
            
    except Exception as e:
        # الآن إذا حدث خطأ سيتم طباعته بدلاً من التعليق
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                              text=f"⚠️ حدث خطأ أثناء تشغيل المتصفح: {e}")

print("Bot is running with Playwright (Optimized for Railway)...")
bot.infinity_polling()

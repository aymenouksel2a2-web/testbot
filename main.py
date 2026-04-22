import os
import telebot
import re
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً! أرسل أمر /get_time لجلب الوقت.")

@bot.message_handler(commands=['get_time'])
def fetch_lab_time(message):
    url = "https://www.skills.google/focuses/19146?parent=catalog"
    
    msg = bot.reply_to(message, "⏳ جاري الاتصال بالموقع (بالنسخة السريعة)...")
    
    try:
        with sync_playwright() as p:
            # تشغيل المتصفح بأقصى إعدادات توفير الذاكرة
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
            
            # منع تحميل الصور والملفات الثقيلة لتسريع العملية
            context.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] else route.continue_())
            
            page = context.new_page()
            
            # أقصى مدة للانتظار 30 ثانية حتى لا يعلق البوت للأبد
            page.set_default_timeout(30000) 
            
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500) # انتظار 2.5 ثانية ليظهر الوقت عبر جافا سكريبت
            
            page_text = page.inner_text("body")
            time_pattern = re.search(r'\d{2}:\d{2}:\d{2}', page_text)
            
            if time_pattern:
                extracted_time = time_pattern.group(0)
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                      text=f"✅ تم العثور على الوقت بنجاح!\n\n⏱️ الوقت: **{extracted_time}**", parse_mode="Markdown")
            else:
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                      text="❌ اكتمل التحميل ولكن لم يتم العثور على الوقت في الصفحة.")
            
            browser.close()
            
    except Exception as e:
        # الآن إذا حدث أي خطأ سيظهر لك في رسالة واضحة
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                              text=f"⚠️ حدث خطأ: {str(e)}")

print("Bot is running securely with Docker...")
bot.infinity_polling()

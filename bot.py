import os
import telebot
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# نقرأ التوكن من متغيرات البيئة
TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    print("خطأ: لم يتم العثور على BOT_TOKEN!")
    exit()

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً! 🤖 أرسل الأمر /screen لألتقط لك صورة من رابط Google Skills.")

@bot.message_handler(commands=['screen'])
def take_screenshot(message):
    # إرسال رسالة للمستخدم بأن البوت يعمل
    msg = bot.reply_to(message, "⏳ جاري فتح المتصفح والتقاط الصورة، انتظر قليلاً...")
    
    try:
        # إعدادات المتصفح الخفي (Headless)
        chrome_options = Options()
        chrome_options.add_argument("--headless") # تشغيل بدون واجهة رسومية
        chrome_options.add_argument("--no-sandbox") # مهم جداً لبيئة السيرفرات
        chrome_options.add_argument("--disable-dev-shm-usage") # لتجنب مشاكل الذاكرة
        chrome_options.add_argument("--window-size=1920,1080")
        
        # تحديد مسار المتصفح الذي سيثبته Dockerfile
        chrome_options.binary_location = "/usr/bin/chromium"
        
        # تشغيل المتصفح
        driver = webdriver.Chrome(options=chrome_options)
        
        # الرابط المطلوب
        url = "https://www.skills.google/focuses/19146?parent=catalog"
        driver.get(url)
        
        # الانتظار 5 ثواني لتحميل الصفحة بالكامل
        time.sleep(5)
        
        # حفظ الصورة
        file_path = "screenshot.png"
        driver.save_screenshot(file_path)
        
        # إرسال الصورة للمستخدم
        with open(file_path, 'rb') as photo:
            bot.send_photo(message.chat.id, photo, caption="📸 هذه هي الصفحة حالياً:")
        
        # إغلاق المتصفح وحذف الصورة من السيرفر
        driver.quit()
        os.remove(file_path)
        bot.edit_message_text("✅ تم الالتقاط بنجاح!", msg.chat.id, msg.message_id)
        
    except Exception as e:
        error_message = f"❌ حدث خطأ: {str(e)}"
        bot.edit_message_text(error_message, msg.chat.id, msg.message_id)
        if 'driver' in locals():
            driver.quit()

if __name__ == '__main__':
    print("البوت يعمل الآن...")
    bot.infinity_polling()

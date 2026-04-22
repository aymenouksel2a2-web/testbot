import telebot
import os

# جلب توكن البوت من المتغيرات البيئية (سنقوم بإعدادها في Railway)
# لا تقم بوضع التوكن هنا مباشرة لحماية البوت الخاص بك
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# التحقق من وجود التوكن
if not BOT_TOKEN:
    print("خطأ: لم يتم العثور على BOT_TOKEN. تأكد من إضافته في المتغيرات البيئية.")
    exit()

# تهيئة البوت
bot = telebot.TeleBot(BOT_TOKEN)

# دالة للرد على أمر /start أو /help
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً بك! 👋\nأنا بوت تيليجرام الخاص بك، وأنا أعمل الآن بنجاح على خوادم Railway! 🚀")

# دالة للرد على أي رسالة نصية أخرى
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, f"لقد قلت لي: {message.text}")

# تشغيل البوت بشكل مستمر
if __name__ == "__main__":
    print("جاري تشغيل البوت...")
    bot.infinity_polling()

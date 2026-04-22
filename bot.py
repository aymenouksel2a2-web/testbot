import os
import telebot

# نقرأ التوكن من متغيرات البيئة (لأمان أكبر)
TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    print("خطأ: لم يتم العثور على BOT_TOKEN في متغيرات البيئة!")
    exit()

bot = telebot.TeleBot(TOKEN)

# أمر /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً بك! 🤖 أنا بوت يعمل على Railway و GitHub.")

# الرد على أي رسالة نصية
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, f"لقد قلت: {message.text}")

# تشغيل البوت
if __name__ == '__main__':
    print("البوت يعمل الآن...")
    bot.infinity_polling()

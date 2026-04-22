import os
import telebot

# جلب التوكن من متغيرات البيئة (سنقوم بإعدادها في Railway لاحقاً)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

# الرد على أمر /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً بك! أنا بوت يعمل بنجاح ومستضاف على Railway 🚂")

# الرد على أي رسالة نصية أخرى بنفس النص
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, f"لقد قلت: {message.text}")

# إبقاء البوت قيد التشغيل
print("Bot is running...")
bot.infinity_polling()

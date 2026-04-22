import os
import telebot
import requests
from bs4 import BeautifulSoup
import re

# جلب التوكن من Railway
BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً! أرسل أمر /get_time لجلب الوقت من موقع Google Skills.")

@bot.message_handler(commands=['get_time'])
def fetch_lab_time(message):
    url = "https://www.skills.google/focuses/19146?parent=catalog"
    
    # إرسال رسالة للمستخدم بأن البوت يعمل على جلب البيانات
    msg = bot.reply_to(message, "⏳ جاري الاتصال بالموقع واستخراج الوقت...")
    
    try:
        # إضافة User-Agent لكي لا يظن الموقع أننا روبوت خبيث
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
        }
        
        # جلب الصفحة
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            # تحليل محتوى الصفحة
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # استخراج كل النص الموجود في الصفحة
            page_text = soup.get_text()
            
            # البحث عن نمط الوقت (رقمين:رقمين:رقمين) مثل 03:00:00
            # \d{2} تعني رقمين، و : هو الفاصل
            time_pattern = re.search(r'\d{2}:\d{2}:\d{2}', page_text)
            
            if time_pattern:
                extracted_time = time_pattern.group(0)
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                      text=f"✅ تم العثور على الوقت بنجاح!\n\n⏱️ الوقت المخصص للمختبر هو: **{extracted_time}**", parse_mode="Markdown")
            else:
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                      text="❌ لم أتمكن من العثور على الوقت. قد يكون الموقع يعتمد على JavaScript لعرض الوقت (Dynamic Content).")
        else:
            bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                                  text=f"⚠️ حدث خطأ في الوصول للموقع. كود الخطأ: {response.status_code}")
            
    except Exception as e:
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, 
                              text=f"حدث خطأ غير متوقع: {e}")

# إبقاء البوت قيد التشغيل
print("Bot is running...")
bot.infinity_polling()

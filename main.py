import telebot
import os
import time
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

# الدالة المسؤولة عن الفحص المتسلسل
def scan_labs(chat_id, start_id, end_id):
    active_tasks[chat_id] = True
    filename = f"results_{chat_id}.txt"
    
    # إنشاء ملف جديد ومسح محتواه القديم إن وجد
    open(filename, 'w').close()
    
    found_count = 0
    
    try:
        with sync_playwright() as p:
            # تشغيل المتصفح في وضع مخفي
            browser = p.chromium.launch(headless=True)
            # إعادة استخدام نفس الصفحة (Page) لتسريع العملية بدلاً من فتح متصفح جديد كل مرة
            page = browser.new_page()
            
            bot.send_message(chat_id, f"🚀 بدأ الفحص الشامل من ID `{start_id}` إلى `{end_id}`...\n\n(لإيقاف الفحص واستلام الملف، أرسل /stop في أي وقت)")
            
            for lab_id in range(start_id, end_id + 1):
                # التحقق مما إذا كان المستخدم قد طلب إيقاف الفحص
                if not active_tasks.get(chat_id):
                    break 
                
                url = f"[https://www.skills.google/focuses/](https://www.skills.google/focuses/){lab_id}?parent=catalog"
                
                try:
                    # الدخول للرابط. قللنا وقت المهلة لتسريع تخطي الروابط الخاطئة (404)
                    page.goto(url, timeout=20000, wait_until="networkidle") 
                    time.sleep(3) # مهلة لضمان استقرار الصفحة بالكامل
                    
                    # الحل الجذري: حقن كود جافاسكريبت داخل المتصفح لسحب الوقت بالقوة
                    js_code = """
                    () => {
                        let allText = document.documentElement.innerText || document.body.textContent;
                        let match = allText.match(/\\b\\d{2}:\\d{2}:\\d{2}\\b/);
                        return match ? match[0] : null;
                    }
                    """
                    
                    lab_time = page.evaluate(js_code)
                    
                    # إذا وجد الوقت، نقوم بحفظه في الملف
                    if lab_time:
                        with open(filename, 'a', encoding='utf-8') as f:
                            f.write(f"ID: {lab_id} | Time: {lab_time} | URL: {url}\n")
                        found_count += 1
                        
                        # إرسال رسالة تحديث كل 5 لابات يجدها لكي لا يزعجك ويحظر من تيليجرام
                        if found_count % 5 == 0:
                            bot.send_message(chat_id, f"⏳ تحديث: تم العثور على {found_count} لابات صالحة حتى الآن... (آخر ID: {lab_id})")
                            
                except Exception as e:
                    # إذا فشل الدخول للرابط (صفحة غير موجودة)، يتجاهلها وينتقل للرقم التالي فوراً
                    pass
            
            browser.close()
            
    except Exception as e:
        error_msg = str(e)[:500]
        bot.send_message(chat_id, f"❌ حدث خطأ في النظام:\n{error_msg}")
    finally:
        active_tasks[chat_id] = False
        bot.send_message(chat_id, "🛑 انتهت عملية الفحص! جاري رفع ملف النتائج...")
        
        # إرسال الملف النصي الذي يحتوي على كل الروابط والأوقات
        if os.path.exists(filename) and os.path.getsize(filename) > 0:
            with open(filename, 'rb') as doc:
                bot.send_document(chat_id, doc, caption=f"📄 تم إيجاد {found_count} لابات خلال هذا الفحص.")
        else:
            bot.send_message(chat_id, "لم يتم العثور على أي أوقات خلال نطاق الفحص المحدد.")

# الرد على أمر البدء والتعليمات
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "مرحباً بك في البوت الماسح! 🤖\n\n"
        "أرسل الأمر `/scan` لفحص جميع الروابط من 0 إلى 99999 دفعة واحدة.\n\n"
        "أو يمكنك تحديد نطاق مخصص للفحص عبر إرسال:\n"
        "`/scan 19000 19200`\n\n"
        "لإيقاف الفحص وسحب النتائج فوراً، أرسل الأمر `/stop`."
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

# أمر الإيقاف
@bot.message_handler(commands=['stop'])
def stop_scan(message):
    chat_id = message.chat.id
    if active_tasks.get(chat_id):
        active_tasks[chat_id] = False # هذا سيوقف الحلقة (Loop) داخل عملية الفحص
        bot.reply_to(message, "⏳ جاري إيقاف الفحص وتجهيز الملف...")
    else:
        bot.reply_to(message, "لا يوجد فحص نشط حالياً.")

# أمر بدء الفحص
@bot.message_handler(commands=['scan'])
def handle_scan(message):
    chat_id = message.chat.id
    
    if active_tasks.get(chat_id):
        bot.reply_to(message, "⚠️ هناك فحص نشط حالياً. أرسل /stop لإيقافه أولاً.")
        return
        
    parts = message.text.split()
    start_id = 0
    end_id = 99999
    
    # التحقق مما إذا كان المستخدم أدخل نطاقاً مخصصاً (مثال: /scan 100 200)
    if len(parts) == 3:
        try:
            start_id = int(parts[1])
            end_id = int(parts[2])
        except ValueError:
            bot.reply_to(message, "⚠️ يرجى إدخال أرقام صحيحة، مثال: `/scan 19100 19200`", parse_mode="Markdown")
            return
            
    # تشغيل العملية في مسار منفصل (Thread) لعدم تجميد البوت
    thread = threading.Thread(target=scan_labs, args=(chat_id, start_id, end_id))
    thread.start()

if __name__ == "__main__":
    print("جاري تشغيل البوت...")
    bot.infinity_polling()

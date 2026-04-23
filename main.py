import telebot
import os
import time
import threading
from playwright.sync_api import sync_playwright
import re
from datetime import timedelta

BOT_TOKEN = os.environ.get('BOT_TOKEN')

if not BOT_TOKEN:
    print("خطأ: لم يتم العثور على BOT_TOKEN.")
    exit()

bot = telebot.TeleBot(BOT_TOKEN)

# قاموس لتتبع العمليات النشطة لمنع تداخل الطلبات
active_tasks = {}

# دالة لإنشاء شريط التقدم المرئي
def create_progress_bar(percentage):
    filled = int(percentage / 10)
    bar = '█' * filled + '░' * (10 - filled)
    return bar

# الدالة المسؤولة عن الفحص المتسلسل (الصيد)
def hunt_labs(chat_id, start_id, end_id):
    active_tasks[chat_id] = True
    total_to_scan = (end_id - start_id) + 1
    found_count = 0
    scanned_count = 0
    start_time = time.time()
    last_dashboard_update = time.time() # تتبع آخر تحديث للوحة القيادة
    
    # رسالة لوحة التحكم المبدئية
    dashboard_msg = bot.send_message(chat_id, "⏳ جاري تهيئة لوحة تحكم الصيد...")
    
    try:
        with sync_playwright() as p:
            # تشغيل المتصفح في وضع مخفي
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            for lab_id in range(start_id, end_id + 1):
                if not active_tasks.get(chat_id):
                    break 
                
                # ------ حماية الذاكرة (Memory Flush) ------
                # كل 100 رابط، قم بإغلاق الصفحة وفتحها من جديد لمنع امتلاء الـ RAM وانهيار السيرفر
                if scanned_count > 0 and scanned_count % 100 == 0:
                    try:
                        page.close()
                    except:
                        pass
                    page = browser.new_page()
                # ----------------------------------------
                
                # تحويل الرقم إلى صيغة 5 أرقام (مثال: 00057)
                lab_id_str = f"{lab_id:05d}"
                url = f"[https://www.skills.google/focuses/](https://www.skills.google/focuses/){lab_id_str}?parent=catalog"
                
                scanned_count += 1
                
                try:
                    # الدخول للرابط. وقت سريع لتخطي الصفحات غير الموجودة
                    page.goto(url, timeout=15000, wait_until="domcontentloaded") 
                    time.sleep(2) # مهلة بسيطة لضمان ظهور النصوص
                    
                    # استخراج العنوان والوقت معاً
                    js_code = """
                    () => {
                        let titleEl = document.querySelector('h1');
                        let title = titleEl ? titleEl.innerText.trim() : "بدون عنوان";
                        let allText = document.documentElement.innerText || document.body.textContent;
                        let match = allText.match(/\\b\\d{2}:\\d{2}:\\d{2}\\b/);
                        if (match) {
                            return { time: match[0], title: title };
                        }
                        return null;
                    }
                    """
                    
                    result = page.evaluate(js_code)
                    
                    if result and result.get('time'):
                        found_count += 1
                        lab_time = result['time']
                        lab_title = result['title']
                        
                        # إرسال رسالة اللاب المكتشف بالتنسيق المطلوب
                        found_msg = (
                            f"🎯 مختبر جديد!\n\n"
                            f"📌 الرقم: `{lab_id_str}`\n"
                            f"🏷️ العنوان: {lab_title}\n"
                            f"⏳ الوقت: `{lab_time}`\n"
                            f"🔗 الرابط: [اضغط هنا للدخول]({url})"
                        )
                        bot.send_message(chat_id, found_msg, parse_mode="Markdown", disable_web_page_preview=True)
                        
                except Exception as e:
                    # تجاهل الروابط المعطوبة أو انهيارات المتصفح اللحظية وإكمال الحلقة
                    pass
                
                # ------ التحديث الآمن للوحة التحكم ------
                # التحديث يتم كل 15 ثانية كحد أدنى بدلاً من كل عدد معين من الروابط، لتجنب حظر Telegram API
                current_time = time.time()
                if (current_time - last_dashboard_update > 15) or scanned_count == total_to_scan:
                    percentage = (scanned_count / total_to_scan) * 100
                    elapsed_time = current_time - start_time
                    avg_time_per_scan = elapsed_time / scanned_count
                    remaining_scans = total_to_scan - scanned_count
                    eta_seconds = remaining_scans * avg_time_per_scan
                    
                    # تنسيق الوقت المتبقي (HH:MM:SS)
                    eta_str = str(timedelta(seconds=int(eta_seconds)))
                    progress_bar = create_progress_bar(percentage)
                    
                    dashboard_text = (
                        f"📊 لوحة تحكم الصيد\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🔄 التقدم: |{progress_bar}| {percentage:.1f}%\n"
                        f"⚡ فحص: {scanned_count}/{total_to_scan}\n"
                        f"🎯 تم العثور على: {found_count} مختبر\n"
                        f"⏳ المتبقي: {eta_str}\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📡 الحالة: يعمل بأمان | 👤 كحساب مسجل"
                    )
                    
                    try:
                        bot.edit_message_text(chat_id=chat_id, message_id=dashboard_msg.message_id, text=dashboard_text)
                        last_dashboard_update = current_time
                    except:
                        pass # تجاهل الأخطاء إذا كان النص لم يتغير أو حصل خطأ بالاتصال
            
            browser.close()
            
    except Exception as e:
        bot.send_message(chat_id, f"❌ حدث خطأ في النظام.")
    finally:
        active_tasks[chat_id] = False
        bot.send_message(chat_id, "🛑 انتهت عملية الصيد بنجاح!")

# الرد على أمر البدء والتعليمات
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "مرحباً بك في بوت 🕵️‍♂️ Super Links Hunter!\n\n"
        "وضعية البحث الحالية: متصل بحسابك 👤\n\n"
        "للأوامر المتاحة:\n"
        "🔹 `/hunt 00000 99999` : للبحث عن المختبرات من وإلى.\n"
        "🔹 `/stop` : لإيقاف البحث."
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

# أمر الإيقاف
@bot.message_handler(commands=['stop'])
def stop_scan(message):
    chat_id = message.chat.id
    if active_tasks.get(chat_id):
        active_tasks[chat_id] = False 
        bot.reply_to(message, "⏳ جاري إيقاف عملية الصيد...")
    else:
        bot.reply_to(message, "لا توجد عملية صيد نشطة حالياً.")

# أمر بدء الفحص (/hunt)
@bot.message_handler(commands=['hunt'])
def handle_hunt(message):
    chat_id = message.chat.id
    
    if active_tasks.get(chat_id):
        bot.reply_to(message, "⚠️ هناك عملية صيد نشطة حالياً. أرسل /stop لإيقافها أولاً.")
        return
        
    parts = message.text.split()
    
    # افتراضي إذا كتب المستخدم /hunt فقط
    start_id = 0
    end_id = 99999
    
    if len(parts) == 3:
        try:
            start_id = int(parts[1])
            end_id = int(parts[2])
        except ValueError:
            bot.reply_to(message, "⚠️ يرجى إدخال أرقام صحيحة، مثال: `/hunt 19150 19160`", parse_mode="Markdown")
            return
    elif len(parts) != 1:
        bot.reply_to(message, "⚠️ استخدام خاطئ. أرسل `/hunt 00000 99999`", parse_mode="Markdown")
        return
            
    # تشغيل العملية في مسار منفصل (Thread)
    thread = threading.Thread(target=hunt_labs, args=(chat_id, start_id, end_id))
    thread.start()

if __name__ == "__main__":
    print("جاري تشغيل البوت...")
    bot.infinity_polling()

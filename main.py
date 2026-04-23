import telebot
import os
import time
import threading
from playwright.sync_api import sync_playwright
import re
from datetime import timedelta

BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

active_tasks = {}

def create_progress_bar(percentage):
    filled = int(percentage / 10)
    if filled > 10: filled = 10
    return '█' * filled + '░' * (10 - filled)

def hunt_labs(chat_id, start_id, end_id):
    active_tasks[chat_id] = True
    total_to_scan = (end_id - start_id) + 1
    found_count = 0
    scanned_count = 0
    start_time = time.time()
    last_dashboard_update = time.time()
    
    dashboard_msg = bot.send_message(chat_id, "⏳ جاري بدء عملية الصيد الذكي...")
    
    try:
        with sync_playwright() as p:
            # تشغيل المتصفح مع إعدادات تضمن الاستقرار
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            for lab_id in range(start_id, end_id + 1):
                if not active_tasks.get(chat_id): break 
                
                # تنظيف الذاكرة كل 50 رابط لضمان عدم توقف السيرفر
                if scanned_count > 0 and scanned_count % 50 == 0:
                    page.close()
                    page = browser.new_page()
                
                lab_id_str = f"{lab_id:05d}"
                
                # --- الرابط مصلح هنا (نص خالص بدون أي تنسيق ماركداون) ---
                base_url = "[https://www.skills.google/focuses/](https://www.skills.google/focuses/)"
                url = f"{base_url}{lab_id_str}?parent=catalog"
                
                scanned_count += 1
                
                try:
                    # الدخول للرابط
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    # الانتظار للتأكد من حدوث التحويل (Redirect) أو تحميل الصفحة
                    page.wait_for_timeout(2000)
                    
                    current_url = page.url
                    # إذا تم تحويلنا للرابط الرئيسي، فهذا يعني أن المختبر غير موجود
                    if "/focuses/" not in current_url or lab_id_str not in current_url:
                        pass 
                    else:
                        # استخراج البيانات إذا كان المختبر حقيقياً
                        js_code = """
                        () => {
                            let titleEl = document.querySelector('h1');
                            let title = titleEl ? titleEl.innerText.trim() : "بدون عنوان";
                            let allText = document.documentElement.innerText || document.body.textContent;
                            let match = allText.match(/\\b\\d{2}:\\d{2}:\\d{2}\\b/);
                            return { 
                                time: match ? match[0] : null, 
                                title: title 
                            };
                        }
                        """
                        result = page.evaluate(js_code)
                        
                        if result and result.get('time'):
                            found_count += 1
                            found_msg = (
                                f"🎯 مختبر جديد!\n\n"
                                f"📌 الرقم: `{lab_id_str}`\n"
                                f"🏷️ العنوان: {result['title']}\n"
                                f"⏳ الوقت: `{result['time']}`\n"
                                f"🔗 الرابط: {url}"
                            )
                            bot.send_message(chat_id, found_msg, disable_web_page_preview=True)
                            
                except Exception:
                    continue
                
                # تحديث لوحة التحكم كل 15 ثانية
                current_time = time.time()
                if (current_time - last_dashboard_update > 15) or scanned_count == total_to_scan:
                    percentage = (scanned_count / total_to_scan) * 100
                    elapsed_time = current_time - start_time
                    avg_time_per_scan = elapsed_time / scanned_count
                    remaining_scans = total_to_scan - scanned_count
                    eta_seconds = max(0, remaining_scans * avg_time_per_scan)
                    
                    dashboard_text = (
                        f"📊 لوحة تحكم الصيد\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🔄 التقدم: |{create_progress_bar(percentage)}| {percentage:.1f}%\n"
                        f"⚡ فحص: {scanned_count}/{total_to_scan}\n"
                        f"🎯 تم العثور على: {found_count} مختبر\n"
                        f"⏳ المتبقي: {str(timedelta(seconds=int(eta_seconds)))}\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📡 الحالة: يعمل بأمان | 👤 كحساب مسجل"
                    )
                    try:
                        bot.edit_message_text(chat_id=chat_id, message_id=dashboard_msg.message_id, text=dashboard_text)
                        last_dashboard_update = current_time
                    except: pass
            
            browser.close()
    except Exception as e:
        print(f"Error: {e}")
    finally:
        active_tasks[chat_id] = False
        bot.send_message(chat_id, "🛑 انتهت العملية.")

@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, "أرسل `/hunt 00000 99999` للبدء.", parse_mode="Markdown")

@bot.message_handler(commands=['stop'])
def stop(m):
    active_tasks[m.chat.id] = False
    bot.reply_to(m, "جاري الإيقاف...")

@bot.message_handler(commands=['hunt'])
def handle_hunt(m):
    p = m.text.split()
    if len(p) == 3:
        threading.Thread(target=hunt_labs, args=(m.chat.id, int(p[1]), int(p[2]))).start()
    else:
        bot.reply_to(m, "استخدم الصيغة: `/hunt 19140 19150`", parse_mode="Markdown")

bot.infinity_polling()

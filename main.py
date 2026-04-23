import telebot
import os
import time
import threading
from playwright.sync_api import sync_playwright
from datetime import timedelta

BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

active_tasks = {}
tasks_lock = threading.Lock()  # FIX 5: thread-safe access


def create_progress_bar(percentage):
    filled = int(percentage / 10)
    if filled > 10:
        filled = 10
    return '█' * filled + '░' * (10 - filled)


def hunt_labs(chat_id, start_id, end_id):
    with tasks_lock:
        active_tasks[chat_id] = True

    total_to_scan = (end_id - start_id) + 1
    found_count = 0
    scanned_count = 0
    start_time = time.time()
    last_dashboard_update = time.time()

    dashboard_msg = bot.send_message(chat_id, "⏳ جاري بدء عملية الصيد الذكي...")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:  # FIX 3: ensure browser closes even on error
                for lab_id in range(start_id, end_id + 1):
                    with tasks_lock:
                        should_continue = active_tasks.get(chat_id, False)
                    if not should_continue:
                        break

                    # تنظيف الذاكرة كل 50 رابط
                    if scanned_count > 0 and scanned_count % 50 == 0:
                        page.close()
                        page = browser.new_page()

                    lab_id_str = f"{lab_id:05d}"

                    # FIX 1: الرابط الصحيح بدون تنسيق Markdown
                    base_url = "https://www.skills.google/focuses/"
                    url = f"{base_url}{lab_id_str}?parent=catalog"

                    scanned_count += 1

                    try:
                        page.goto(url, timeout=30000, wait_until="domcontentloaded")
                        page.wait_for_timeout(2000)

                        current_url = page.url

                        if "/focuses/" not in current_url or lab_id_str not in current_url:
                            pass
                        else:
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
                                    f"🎯 مختبر جديد\\!\n\n"
                                    f"📌 الرقم: `{lab_id_str}`\n"
                                    f"🏷️ العنوان: {result['title']}\n"
                                    f"⏳ الوقت: `{result['time']}`\n"
                                    f"🔗 الرابط: {url}"
                                )
                                # FIX 2: إضافة parse_mode لعرض التنسيق
                                bot.send_message(
                                    chat_id,
                                    found_msg,
                                    parse_mode="MarkdownV2",
                                    disable_web_page_preview=True
                                )

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
                            f"📡 الحالة: يعمل بأمان"
                        )
                        try:
                            bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=dashboard_msg.message_id,
                                text=dashboard_text
                            )
                            last_dashboard_update = current_time
                        except Exception:
                            pass

            finally:
                page.close()
                browser.close()  # FIX 3: يُغلق دائماً حتى عند الخطأ

    except Exception as e:
        print(f"Error in hunt_labs: {e}")
        bot.send_message(chat_id, f"❌ حدث خطأ: {e}")
    finally:
        with tasks_lock:
            active_tasks[chat_id] = False
        bot.send_message(chat_id, "🛑 انتهت العملية.")


@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, "أرسل `/hunt 00000 99999` للبدء.", parse_mode="Markdown")


@bot.message_handler(commands=['stop'])
def stop(m):
    with tasks_lock:
        active_tasks[m.chat.id] = False
    bot.reply_to(m, "⏹️ جاري الإيقاف...")


@bot.message_handler(commands=['hunt'])
def handle_hunt(m):
    p = m.text.split()
    if len(p) == 3:
        try:
            start_id = int(p[1])
            end_id = int(p[2])
            if start_id > end_id:
                bot.reply_to(m, "❌ رقم البداية يجب أن يكون أصغر من رقم النهاية.")
                return
            threading.Thread(
                target=hunt_labs,
                args=(m.chat.id, start_id, end_id),
                daemon=True
            ).start()
            bot.reply_to(m, f"🚀 بدأ الفحص من `{p[1]}` إلى `{p[2]}`", parse_mode="Markdown")
        except ValueError:
            bot.reply_to(m, "❌ الأرقام غير صالحة.")
    else:
        bot.reply_to(m, "استخدم الصيغة: `/hunt 19140 19150`", parse_mode="Markdown")


# FIX 4: إعادة التشغيل التلقائي عند انقطاع الاتصال
while True:
    try:
        print("Bot started...")
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        print(f"Polling error: {e}")
        time.sleep(5)

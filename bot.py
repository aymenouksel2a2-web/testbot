import os
import re
import time
import json
import io
import telebot
import pytesseract
from PIL import Image, ImageOps
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- إعدادات ---
TOKEN = os.environ.get("BOT_TOKEN")
COOKIES_STR = os.environ.get("COOKIES_JSON")

# تحديد مسار tesseract صراحة ليعمل في Docker
pytesseract.pytesseract.tesseract_cmd = r'/usr/bin/tesseract'

bot = telebot.TeleBot(TOKEN)

def get_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-US")

    # إخفاء علامات الأتمتة ليبدو كإنسان
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(options=options)
    # إخفاء خاصية webdriver في JavaScript
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        '''
    })
    return driver

@bot.message_handler(commands=['time'])
def get_lab_time(message):
    status_msg = bot.reply_to(message, "🔄 جاري الاتصال... انتظر (15 ثانية)")
    driver = None

    try:
        driver = get_driver()

        # --- الخطوة 1: الدخول بكوكيزك ---
        print("[1] جاري تحميل الكوكيز...")
        driver.get("https://www.google.com")
        time.sleep(4) # انتظار طويل

        cookies_loaded = 0
        if COOKIES_STR:
            try:
                cookies_list = json.loads(COOKIES_STR)
                for c in cookies_list:
                    try:
                        # تنظيف البيانات المزعجة
                        c.pop('sameSite', None)
                        c.pop('httpOnly', None)
                        c.pop('hostOnly', None)
                        driver.add_cookie(c)
                        cookies_loaded += 1
                    except Exception as e:
                        continue
                print(f"[OK] تم تحميل {cookies_loaded} كوكيز")
            except Exception as e:
                print(f"[ERR] فشل قراءة JSON: {e}")

        # --- الخطوة 2: فتح الصفحة المستهدفة ---
        target_url = "https://www.skills.google/focuses/19146?parent=catalog"
        print("[2] فتح الرابط...")
        driver.get(target_url)

        # الانتظار الطويل جداً (جوجل تحتاج وقت)
        print("[3] انتظار تحميل الصفحة...")
        time.sleep(15) 

        # محاولة إغلاق نوافذ الـ Popups (OK / Accept)
        try:
            wait = WebDriverWait(driver, 5)
            popup_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Got it') or contains(text(), 'OK')]")))
            popup_btn.click()
            time.sleep(2)
        except:
            pass

        # --- الخطوة 3: استخراج الوقت بالطرق المتعددة ---
        extracted_time = "NULL"
        method_used = "None"

        # الطريقة A: JavaScript (الأقوى - يقرأ ما يراه المتصفح فعلاً)
        try:
            js_code = """
                // البحث في كل نصوص الصفحة عن نمط HH:MM:SS
                const bodyText = document.body.innerText;
                const matches = bodyText.match(/\\b\\d{2}:\\d{2}:\\d{2}\\b/g);
                // نعيد أول تطابق يكون من منطقة المحتوى وليس الهيدر/الفوتر غالباً
                return matches ? matches[0] : 'NOT_FOUND';
            """
            result_js = driver.execute_script(js_code)
            if result_js != 'NOT_FOUND' and len(result_js) == 8:
                extracted_time = result_js
                method_used = "JS_Execute_Script"
                print(f"[FOUND] بالـ JavaScript: {extracted_time}")
        except Exception as e:
            print(f"[ERR] JS Failed: {e}")

        # الطريقة B: البحث في Source Code (Regex)
        if extracted_time == "NULL":
            try:
                source = driver.page_source
                source_match = re.search(r'>\s*(\d{2}:\d{2}:\d{2})\s*<', source)
                if source_match:
                    extracted_time = source_match.group(1)
                    method_used = "HTML_Source_Regex"
            except:
                pass

        # الطريقة C: OCR على صورة كاملة (بعد تحسينها بالأسود والأبيض)
        if extracted_time == "NULL":
            print("[4] اللجوء للـ OCR...")
            ss_path = "debug_screenshot.png"
            driver.save_screenshot(ss_path)

            img = Image.open(ss_path)

            # تحويل لرمادي ثم ثنائي (Black & White) لتسهيل القراءة
            img_gray = img.convert('L')
            # زيادة الحدة
            img_bw = img_gray.point(lambda x: 0 if x < 150 else 255, '1')

            config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789:'
            text = pytesseract.image_to_string(img_bw, config=config).strip()

            print(f"[OCR] النص المقروء: '{text}'")

            ocr_match = re.search(r'\b(\d{2}:\d{2}:\d{2})\b', text)
            if ocr_match:
                extracted_time = ocr_match.group(1)
                method_used = "OCR_Image"

            os.remove(ss_path)

        # --- الخطوة 4: إرسال النتيجة والتصحيح ---
        final_ss = "result.png"
        driver.save_screenshot(final_ss)

        # بناء رسالة التصحيح (Debug Info) لتعرف أين الخلل
        current_url = driver.current_url
        title = driver.title

        # استخدام HTML بدلاً من Markdown لتجنب أخطاء الإرسال الصامتة
        debug_info = (
            f"🔍 <b>معلومات التصحيح:</b>\n"
            f"• <b>الرابط الحالي:</b> <code>{current_url[:50]}...</code>\n"
            f"• <b>عنوان الصفحة:</b> {title}\n"
            f"• <b>طريقة الاستخراج:</b> <code>{method_used}</code>\n"
            f"• <b>الوقت:</b> <code>{extracted_time}</code>\n"
            f"• <b>حالة الكوكيز:</b> تم تحميل {cookies_loaded} عنصر\n\n"
        )

        if extracted_time != "NULL":
            msg_text = f"⏱️ <b>الوقت المتبقي في الLab:</b>\n🕐 <b><code>{extracted_time}</code></b>\n\n" + debug_info
        else:
            msg_text = f"❌ <b>لم أجد الوقت!</b>\n\n" + debug_info + "📸 <b>أنظر للصورة لترى ما ظهر: </b>\n<i>ملاحظة:</i> هل ترى شاشة تسجيل دخول؟ هذا يعني الكوكيز غير صحيحة."

        with open(final_ss, 'rb') as photo:
            bot.send_photo(
                message.chat.id,
                photo,
                caption=msg_text,
                parse_mode='HTML' # التغيير لـ HTML
            )

        os.remove(final_ss)
        bot.edit_message_text("✅ تم الانتهاء!", status_msg.chat.id, status_msg.message_id)

    except Exception as e:
        # استخدام HTML لضمان إرسال رسالة الخطأ للمستخدم
        error_report = f"💥 <b>خطأ برمجي:</b>\n<code>{str(e)}</code>\n\nيرجى مراجعة سجل (Logs) الخادم."
        try:
            bot.send_message(message.chat.id, error_report, parse_mode='HTML')
            bot.edit_message_text("❌ فشل الطلب.", status_msg.chat.id, status_msg.message_id)
        except Exception:
            pass # تجاهل في حال فشل إرسال الخطأ أيضاً
    finally:
        if driver:
            driver.quit()

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "مرحباً! أرسل /time وانتظر.")

if __name__ == '__main__':
    print("Bot V3.1 - HTML Parse Mode & Fixes")
    bot.infinity_polling()

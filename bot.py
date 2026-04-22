import os
import re
import time
import json
import telebot
import pytesseract
from PIL import Image, ImageFilter
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

TOKEN = os.environ.get("BOT_TOKEN")
COOKIES_STR = os.environ.get("COOKIES_JSON")

bot = telebot.TeleBot(TOKEN)

def setup_driver():
    options = Options()
    options.add_argument("--headless=new") # وضع جديد أفضل
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-US")
    # إخفاء الأتمتة
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    
    driver = webdriver.Chrome(options=options)
    return driver

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👋 أهلاً! أرسل /time وسألتقط لك الوقت الحقيقي.")

@bot.message_handler(commands=['time'])
def get_time(message):
    msg = bot.reply_to(message, "⏳ جاري الاتصال... قد تستغرق العملية 15 ثانية...")
    driver = None
    
    try:
        driver = setup_driver()
        
        # 1. تحميل الكوكيز
        driver.get("https://www.google.com")
        time.sleep(3)
        
        if COOKIES_STR:
            try:
                cookies = json.loads(COOKIES_STR)
                for c in cookies:
                    try:
                        c.pop('sameSite', None)
                        c.pop('httpOnly', None)
                        c.pop('hostOnly', None)
                        driver.add_cookie(c)
                    except:
                        pass
            except Exception as e:
                print(f"Cookie Error: {e}")

        # 2. الدخول للصفحة المستهدفة
        target_url = "https://www.skills.google/focuses/19146?parent=catalog"
        driver.get(target_url)
        
        # 3. الانتظار الطويل (لأن جوجل بطيء في تحميل الـ JS الخاص بالمؤقت)
        print("جاري تحميل الصفحة...")
        time.sleep(12) 
        
        # محاولة إزالة نوافذ المنبثقة (Popups) إن وجدت
        try:
            # إغلاق نافذة Cookies إن ظهرت
            close_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'OK') or contains(text(), 'Got it') or contains(text(), 'Accept')]")
            for btn in close_btns[:2]:
                try:
                    btn.click()
                    time.sleep(1)
                except:
                    pass
        except:
            pass
        
        # ---------------------------------------------------
        # الطريقة الأولى: البحث في كود HTML (Regex) - سريعة
        # ---------------------------------------------------
        found_time = None
        try:
            html = driver.page_source
            # نمط البحث: XX:XX:XX حيث X أرقام
            match = re.search(r'(\d{2}:\d{2}:\d{2})', html)
            if match:
                found_time = match.group(1)
                print(f"وجدت وقت في HTML: {found_time}")
        except:
            pass
            
        # ---------------------------------------------------
        # الطريقة الثانية: OCR (قراءة الصورة) - ضمان 100%
        # ---------------------------------------------------
        if not found_time or found_time == "00:00:00":
            print("لم أجد وقت في HTML، سأستخدم OCR على الصورة...")
            
            # التقاط الصورة بدقة عالية
            screenshot_path = "ss_raw.png"
            crop_path = "ss_crop.png"
            driver.save_screenshot(screenshot_path)
            
            # فتح الصورة وتحويلها للأفضلية
            img = Image.open(screenshot_path)
            
            # نقوم بقص منطقة الزاوية العلوية اليسرى حيث يظهر الوقت عادة (03:00:00)
            # نسبة القص تقريبية بناء على الصورة المعروضة
            width, height = img.size
            # قص المنطقة المحتوية على "Start Lab" والوقت (يسار - أعلى)
            left = int(width * 0.05)
            top = int(height * 0.25)
            right = int(width * 0.30) 
            bottom = int(height * 0.45)
            
            cropped_img = img.crop((left, top, right, bottom))
            cropped_img.save(crop_path)
            
            # تحويل للرمادي وتحسين الحدة للقراءة
            img_gray = cropped_img.convert('L')
            img_filtered = img_gray.filter(ImageFilter.SHARPEN)
            
            # استخراج النص
            custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789:'
            text = pytesseract.image_to_string(img_filtered, config=custom_config)
            
            print(f"النص المستخرج بالـ OCR: '{text}'")
            
            # البحث عن نمط الوقت في النص المستخرج
            ocr_match = re.search(r'(\d{2}:\d{2}:\d{2})', text)
            if ocr_match:
                found_time = ocr_match.group(1)
            
            # تنظيف الملفات المؤقتة
            os.remove(crop_path)

        # ---------------------------------------------------
        # إرسال النتيجة النهائية
        # ---------------------------------------------------
        final_img = "final_ss.png"
        driver.save_screenshot(final_img)
        
        if found_time and found_time != "00:00:00":
            response_msg = f"✅ **تم العثور على الوقت المتبقي:**\n🕐 `{found_time}`\n\n📸 التقطت الصورة للتأكيد:"
        else:
            response_msg = f"⚠️ **لم أستطع قراءة الوقت تلقائياً**\n\n👇 انظر إلى الصورة أسفل الرسالة واقرأ الوقت يدوياً في الزاوية:"
            
        with open(final_img, 'rb') as photo:
            bot.send_photo(
                message.chat.id,
                photo,
                caption=response_msg,
                parse_mode='Markdown'
            )
            
        os.remove(final_img)
        if os.path.exists("ss_raw.png"):
            os.remove("ss_raw.png")
            
        bot.edit_message_text("✅ تم الفحص!", msg.chat.id, msg.message_id)

    except Exception as e:
        error_text = f"❌ خطأ: {str(e)}"
        try:
            bot.edit_message_text(error_text, msg.chat.id, msg.message_id)
        except:
            pass
    finally:
        if driver:
            driver.quit()

if __name__ == '__main__':
    print("Bot is running with OCR capabilities...")
    bot.infinity_polling()

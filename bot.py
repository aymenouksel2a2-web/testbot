import os
import telebot
import time
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

TOKEN = os.environ.get("BOT_TOKEN")
COOKIES_STR = os.environ.get("COOKIES_JSON") # هنا نضع الـ Cookies المستخرجة

bot = telebot.TeleBot(TOKEN)

def setup_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # بعض المواقع تحدد متصفحات الأتمتة، نخفيها
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined})
        '''
    })
    return driver

def load_cookies(driver, url):
    """تحميل ملفات الارتباط (Cookies) للدخول كحسابك الشخصي"""
    try:
        # أولاً نذهب للموقع لتعيين المجال domain
        driver.get("https://www.google.com")
        time.sleep(2)
        
        # تحويل النص JSON إلى قائمة
        cookies = json.loads(COOKIES_STR)
        
        for cookie in cookies:
            # إزالة الحقول التي قد تسبب خطأ في selenium
            cookie.pop('sameSite', None)
            cookie.pop('httpOnly', None)
            cookie.pop('hostOnly', None)
            
            try:
                # يجب أن يكون اسم النطاق صحيحاً
                if 'skills.google' in cookie.get('domain', '') or 'google.com' in cookie.get('domain', ''):
                    driver.add_cookie(cookie)
            except Exception as e:
                print(f"خطاء اضافة كوكيز: {e}")
                
        return True
    except Exception as e:
        print(f"خطاء عام في الكوكيز: {e}")
        return False

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👋 مرحباً!\nأرسل /time لمعرفة وقت الـ Lab المتبقي.")

@bot.message_handler(commands=['time'])
def get_time(message):
    msg = bot.reply_to(message, "⏳ جاري فتح الصفحة واستخراج الوقت...")
    driver = None
    
    try:
        driver = setup_driver()
        
        # 1. تحميل الكوكيز والذهاب للصفحة
        load_cookies(driver, "")
        target_url = "https://www.skills.google/focuses/19146?parent=catalog"
        driver.get(target_url)
        
        # الانتظار حتى تحميل الصفحة
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(8) # انتظار إضافي لتحميل الـ JS
        
        # ---------------------------------------------------
        # طريقة استخراج الوقت (جرب عدة محاولات)
        # ---------------------------------------------------
        time_text = "⚠️ لم يتم العثور على الوقت"
        
        # المحاولة 1: البحث عن العناصر التي تحتوي على نمط الوقت (HH:MM:SS)
        # عادة ما يكون هذا الوقت داخل عنصر معين أو بالقرب من كلمة Time limit
        
        # محاولة البحث بـ XPath (أكثر فاعلية لهيكليات Google المعقدة)
        try:
            # البحث عن أي عنصر يحتوي على النقطتين ":" ويمثل وقتاً
            # في صفحة Google Skills عادة ما يكون هنالك مؤقت داخل Div خاص
            time_elements = driver.find_elements(By.XPATH, "//*[contains(text(), ':') and string-length(text()) < 9]")
            
            found_time = False
            for elem in time_elements:
                txt = elem.text.strip()
                # التحقق من أنه تنسيق وقت (مثلاً 03:00:00 أو 01:23:45)
                if len(txt) == 8 and txt.count(':') == 2:
                    # نتأكد أنه ليس جزءاً من تاريخ أو شيء آخر
                    try:
                        h, m, s = map(int, txt.split(':'))
                        if 0 <= h <= 99 and 0 <= m <= 59 and 0 <= s <= 59:
                            time_text = f"⏱️ **الوقت المتبقي:** `{txt}`"
                            found_time = True
                            break
                    except:
                        continue
            
            if not found_time:
                # المحاولة 2: البحث بناءً على هيكلية الصفحة الشائعة
                # غالباً يكون المؤقت بجانب "Start Lab" أو "Time limit"
                timer_divs = driver.find_elements(By.XPATH, "//div[contains(@class, 'timer') or contains(@class, 'time')] | //span[contains(@class, 'clock')]")
                for div in timer_divs:
                    if ':' in div.text:
                        time_text = f"🕐 **الحالة:** {div.text}"
                        break
                        
        except Exception as xpath_err:
            print(f"خطأ في XPath: {xpath_err}")

        # ---------------------------------------------------
        # إرسال النتيجة
        # ---------------------------------------------------
        # نلتقط صورة احتياطية للتأكد
        screenshot_path = "check.png"
        driver.save_screenshot(screenshot_path)
        
        # إرسال الرسالة
        with open(screenshot_path, 'rb') as photo:
            bot.send_photo(
                message.chat.id, 
                photo, 
                caption=time_text + "\n\n📸 صورة احتياطية للصفحة:",
                parse_mode='Markdown'
            )
            
        os.remove(screenshot_path)
        bot.edit_message_text("✅ تم!", msg.chat.id, msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ حدث خطأ تقني:\n`{str(e)}`", msg.chat.id, msg.message_id, parse_mode='Markdown')
    finally:
        if driver:
            driver.quit()

if __name__ == '__main__':
    print("البوت يعمل ويعرف كيف يستخرج الوقت!")
    bot.infinity_polling()

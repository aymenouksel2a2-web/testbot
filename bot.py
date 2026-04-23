import os
import re
import time
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- الثوابت ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# --- دالة الاستخراج باستخدام Selenium ---
def extract_text_with_selenium(url: str) -> str:
    """
    تستخدم Selenium لتحميل الصفحة واستخراج النص، بما في ذلك المحتوى الديناميكي.
    """
    driver = None
    try:
        # إعداد خيارات Chrome ليعمل بدون واجهة (headless)
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        # إعداد خدمة ChromeDriver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # تحميل الصفحة
        driver.get(url)
        
        # انتظار تحميل المحتوى الأساسي (يمكن تعديل العنصر حسب الصفحة)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(2)  # انتظار إضافي للتحميل الكامل
        except Exception:
            pass
        
        # الحصول على HTML الكامل للصفحة بعد تحميل JavaScript
        page_html = driver.page_source
        
        # تحليل HTML باستخدام BeautifulSoup
        soup = BeautifulSoup(page_html, 'html.parser')
        
        # البحث عن النص الذي يحتوي على وقت المختبر
        time_pattern = r'\b\d{1,2}:\d{2}:\d{2}\b'
        all_text = soup.get_text(separator='\n', strip=True)
        
        # البحث عن الوقت في النص
        time_matches = re.findall(time_pattern, all_text)
        
        if time_matches:
            # إرجاع الوقت الأول الذي تم العثور عليه
            lab_time = time_matches[0]
            return f"⏱️ وقت المختبر: {lab_time}\n\n{all_text}"
        else:
            return f"⚠️ لم يتم العثور على وقت محدد.\n\n{all_text}"
            
    except Exception as e:
        return f"❌ فشل استخراج النص: {e}"
    finally:
        if driver:
            driver.quit()

# --- دوال التعامل مع تيليجرام ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرد على أمر /start"""
    await update.message.reply_text(
        "👋 مرحبًا! أنا بوت استخراج النصوص.\n"
        "أرسل لي رابط (URL) لأي صفحة ويب، وسأقوم باستخراج النص الموجود فيها وإرساله لك.\n"
        "✍️ مثال: https://www.skills.google/focuses/19146"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل النصية الواردة من المستخدم"""
    user_text = update.message.text
    
    # التعبير النمطي للبحث عن روابط URL في النص
    url_pattern = r'(https?://[^\s]+)'
    urls = re.findall(url_pattern, user_text)
    
    if urls:
        # إرسال رسالة انتظار
        await update.message.reply_text("⏳ جاري استخراج النص من الرابط باستخدام Selenium، قد يستغرق ذلك دقيقة...")
        
        url = urls[0]
        extracted_text = extract_text_with_selenium(url)
        
        # تيليجرام لديه حد أقصى لطول الرسالة (4096 حرفًا)
        max_length = 4096
        if len(extracted_text) > max_length:
            preview = extracted_text[:max_length-200] + "...\n\n[تم اقتطاع النص لأنه طويل جدًا]"
            await update.message.reply_text(preview)
        else:
            await update.message.reply_text(extracted_text)
    else:
        await update.message.reply_text("🤔 لم أجد رابطًا صالحًا في رسالتك. من فضلك أرسل رابط URL.")

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    app = Application.builder().token(TOKEN).build()
    
    # إضافة handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # بدء البوت
    print("🤖 البوت يعمل الآن...")
    app.run_polling()

if __name__ == "__main__":
    main()

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

# --- إعدادات ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# --- دالة الاستخراج باستخدام Selenium (متزامنة) ---
def extract_text_with_selenium(url: str) -> str:
    """
    تستخدم Selenium لتحميل الصفحة واستخراج النص الكامل، بما في ذلك المحتوى الديناميكي.
    تعمل بشكل متزامن (synchronous) لتجنب مشاكل asyncio.
    """
    driver = None
    try:
        # إعداد خيارات Chrome
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # استخدام ChromeDriver المثبت تلقائياً عبر webdriver-manager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # تحميل الصفحة
        driver.get(url)
        
        # انتظار تحميل المحتوى الأساسي (حتى يظهر body)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        # انتظار إضافي للـ JavaScript
        time.sleep(3)
        
        # الحصول على HTML الكامل بعد التحميل
        page_html = driver.page_source
        
        # تحليل HTML باستخدام BeautifulSoup
        soup = BeautifulSoup(page_html, 'html.parser')
        all_text = soup.get_text(separator='\n', strip=True)
        
        # البحث عن وقت المختبر بصيغة HH:MM:SS
        time_pattern = r'\b\d{1,2}:\d{2}:\d{2}\b'
        time_matches = re.findall(time_pattern, all_text)
        
        if time_matches:
            lab_time = time_matches[0]
            return f"⏱️ وقت المختبر: {lab_time}\n\n{all_text}"
        else:
            return f"⚠️ لم يتم العثور على وقت محدد.\n\n{all_text}"
    except Exception as e:
        return f"❌ فشل استخراج النص: {e}"
    finally:
        if driver:
            driver.quit()

# --- دوال تيليجرام ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 مرحبًا! أنا بوت استخراج النصوص.\n"
        "أرسل لي رابط (URL) لأي صفحة ويب، وسأقوم باستخراج النص الموجود فيها وإرساله لك.\n"
        "✍️ مثال: https://www.skills.google/focuses/19146"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    url_pattern = r'(https?://[^\s]+)'
    urls = re.findall(url_pattern, user_text)
    
    if urls:
        await update.message.reply_text("⏳ جاري استخراج النص من الرابط... قد يستغرق ذلك 20-30 ثانية.")
        
        url = urls[0]
        # تشغيل دالة Selenium المتزامنة في thread منفصل بدون مشاكل event loop
        import asyncio
        loop = asyncio.get_event_loop()
        extracted_text = await loop.run_in_executor(None, extract_text_with_selenium, url)
        
        max_length = 4096
        if len(extracted_text) > max_length:
            preview = extracted_text[:max_length-200] + "...\n\n[تم اقتطاع النص لأنه طويل جدًا]"
            await update.message.reply_text(preview)
        else:
            await update.message.reply_text(extracted_text)
    else:
        await update.message.reply_text("🤔 لم أجد رابطًا صالحًا في رسالتك. من فضلك أرسل رابط URL.")

def main():
    if not TOKEN:
        print("❌ خطأ: لم يتم تعيين TELEGRAM_BOT_TOKEN في متغيرات البيئة.")
        return
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🤖 البوت يعمل الآن...")
    # drop_pending_updates=True لحل مشكلة تعارض الجلسات
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

import os
import asyncio
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image
import time

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("يا معفن حط التوكن أولاً!")

# ==================== دالة التصوير ====================
async def take_screenshot():
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-gpu")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        logger.info("جاري فتح الموقع...")
        driver.get("https://gratisfy.xyz/chat")
        
        # ننتظر الموقع يتحمل (مهم جداً)
        await asyncio.sleep(8)
        
        screenshot_path = "screenshot.png"
        driver.save_screenshot(screenshot_path)
        driver.quit()
        
        # تصغير الصورة شوية عشان ما تكونش كبيرة أوي
        img = Image.open(screenshot_path)
        img.save(screenshot_path, optimize=True, quality=85)
        
        logger.info("تم أخذ السكرين شوت بنجاح")
        return screenshot_path

    except Exception as e:
        logger.error(f"خطأ في التصوير: {e}")
        return None


# ==================== الأوامر ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ البوت شغال يا قحبة\nاستخدم الأمر: /screenshot")

async def screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ جاري الدخول للموقع وأخذ سكرين شوت...")

    screenshot_path = await take_screenshot()

    if screenshot_path and os.path.exists(screenshot_path):
        await msg.edit_text("✅ تم أخذ السكرين شوت، يتم الإرسال...")
        await update.message.reply_photo(
            photo=open(screenshot_path, 'rb'),
            caption="📸 هذا ما يظهر حالياً في الموقع:\nhttps://gratisfy.xyz/chat"
        )
        os.remove(screenshot_path)  # حذف الصورة بعد الإرسال
    else:
        await msg.edit_text("❌ فشل في أخذ السكرين شوت يا منيوك")

# ==================== تشغيل البوت ====================
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("screenshot", screenshot))

    print("🤖 البوت اشتغل بنجاح - وضع السكرين شوت مفعل")
    app.run_polling()

if __name__ == "__main__":
    main()

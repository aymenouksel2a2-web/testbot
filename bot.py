import os
import re
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- إعداد مسار متصفحات Playwright ---
# هذا يضمن استخدام المسار الذي سنثبته في nixpacks.toml
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/app/.playwright-browsers")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# --- دالة استخراج النص باستخدام Playwright ---
async def extract_text_with_playwright(url: str) -> str:
    """
    تستخدم Playwright لتحميل الصفحة واستخراج النص الكامل، بما في ذلك المحتوى الديناميكي.
    """
    try:
        async with async_playwright() as p:
            # تشغيل المتصفح بدون واجهة مع وسائط لتقليل استهلاك الذاكرة
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--single-process',           # يقلل استخدام الذاكرة
                    '--disable-software-rasterizer'
                ]
            )
            page = await browser.new_page()
            
            # الانتقال إلى الرابط وانتظار تحميل الشبكة
            await page.goto(url, wait_until='networkidle')
            
            # انتظار إضافي لتحميل أي محتوى ديناميكي (3 ثوانٍ)
            await page.wait_for_timeout(3000)
            
            # الحصول على HTML الكامل بعد التحميل
            html_content = await page.content()
            await browser.close()
        
        # تحليل HTML باستخدام BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
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
        await update.message.reply_text("⏳ جاري استخراج النص من الرابط باستخدام Playwright، قد يستغرق ذلك دقيقة...")
        
        url = urls[0]
        extracted_text = await extract_text_with_playwright(url)
        
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
    app.run_polling()

if __name__ == "__main__":
    main()

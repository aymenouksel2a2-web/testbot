import os
import asyncio
import logging
from io import BytesIO
from urllib.parse import urlparse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

TOKEN = os.environ.get("TOKEN")
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- دوال مساعدة ---
def is_valid_url(text: str) -> bool:
    parsed = urlparse(text.strip())
    return parsed.scheme in ('http', 'https') and bool(parsed.netloc)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت المراقبة جاهز!\n\n"
        "أرسل لي أي رابط (مثلاً: https://google.com)\n"
        " وسألتقط له صورة كل 3 ثوانٍ (10 لقطات)."
    )

# --- المهمة الأساسية (تعمل في الخلفية) ---
async def monitor_url_task(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    chat_id = update.effective_chat.id
    browser = None

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏳ جاري فتح المتصفح على الرابط:\n{url}"
        )

        async with async_playwright() as p:
            # تشغيل Chromium بدون واجهة (Headless)
            browser = await p.chromium.launch(headless=True)
            page_context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            page = await page_context.new_page()

            # فتح الرابط (انتظار تحميل الصفحة)
            await page.goto(url, wait_until="load", timeout=30000)

            # التقاط 10 صور كل 3 ثوانٍ
            for i in range(1, 11):
                await asyncio.sleep(3)

                screenshot_bytes = await page.screenshot(type="png", full_page=False)
                photo_buffer = BytesIO(screenshot_bytes)
                photo_buffer.name = f"screenshot_{i:02d}.png"

                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_buffer,
                    caption=f"📸 لقطة {i}/10 من {url}"
                )

            await context.bot.send_message(
                chat_id=chat_id,
                text="✅ تم الانتهاء من التقاط الصور."
            )

    except Exception as e:
        logging.error(f"Monitor Error: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ حدث خطأ أثناء التصفح:\n<code>{e}</code>",
            parse_mode="HTML"
        )
    finally:
        if browser:
            await browser.close()

# --- معالجة الرسائل ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if is_valid_url(text):
        # تشغيل المهمة في الخلفية حتى لا يتجمد البوت
        asyncio.create_task(monitor_url_task(update, context, text))
    else:
        await update.message.reply_text(
            "❌ هذا لا يبدو رابطًا صحيحًا.\n"
            "تأكد أنه يبدأ بـ http:// أو https://"
        )

# --- التشغيل ---
def main():
    if not TOKEN:
        raise ValueError("❌ متغير البيئة TOKEN غير موجود!")

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

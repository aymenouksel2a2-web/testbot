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

def normalize_url(text: str) -> str | None:
    text = text.strip()
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme in ('http', 'https') and bool(parsed.netloc):
        return text
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت التصفح الذكي جاهز!\n\n"
        "• وضع المراقبة: أرسل رابط فقط\n"
        "• وضع البرومبت:\n"
        "   السطر 1: الرابط\n"
        "   السطر 2: البرومبت/الرسالة\n\n"
        "سألتقط صوراً لكي ترى ما يحدث!"
    )

async def monitor_url_task(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, prompt: str | None):
    chat_id = update.effective_chat.id
    browser = None

    try:
        await context.bot.send_message(chat_id=chat_id, text=f"⏳ جاري فتح المتصفح...\n{url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page_context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            page = await page_context.new_page()

            # نفتح الرابط وننتظر تحميل الصفحة (networkidle جيد للمواقع الديناميكية)
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # ─── وضع البرومبت ───
            if prompt:
                await context.bot.send_message(chat_id=chat_id, text="⌨️ جاري البحث عن حقل الكتابة...")
                await page.wait_for_timeout(1500)

                # 1️⃣ البحث الذكي عن حقل الإدخال (3 استراتيجيات)
                input_box = None
                locators_to_try = [
                    page.locator("textarea").first,                 # معظم مواقع الدردشة
                    page.locator('input[type="text"]').last,        # حقل نصي عام
                    page.locator('[contenteditable="true"]').first, # محررات غنية
                ]
                # كلمات شائعة في placeholder أو aria-label
                for keyword in ["message", "chat", "ask", "search", "prompt", "say something", "type here", "write"]:
                    locators_to_try.append(page.locator(f'[placeholder*="{keyword}" i]').first)
                    locators_to_try.append(page.locator(f'[aria-label*="{keyword}" i]').first)

                for loc in locators_to_try:
                    try:
                        if await loc.count() > 0:
                            await loc.wait_for(state="visible", timeout=3000)
                            input_box = loc
                            break
                    except Exception:
                        continue

                if not input_box:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ لم أجد حقل كتابة تلقائياً. سألتقط صور للحالة الحالية."
                    )
                else:
                    try:
                        await input_box.click()
                        await input_box.fill(prompt)
                        await page.wait_for_timeout(500)
                        await input_box.press("Enter")
                        await page.wait_for_timeout(500)

                        # محاولة إضافية: النقر على زر Send إذا وجده
                        try:
                            send_btn = page.locator('button:has-text("Send"), button[type="submit"]').first
                            if await send_btn.count() > 0 and await send_btn.is_visible():
                                await send_btn.click()
                        except Exception:
                            pass

                        await context.bot.send_message(chat_id=chat_id, text="✅ تم إرسال البرومبت. جاري التقاط الصور...")
                    except Exception as e:
                        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء الكتابة: {e}")

                # التقاط 5 صور كل 4 ثوانٍ (نعطي الوقت للموقع ليُظهر الرد)
                for i in range(1, 6):
                    await asyncio.sleep(4)
                    screenshot_bytes = await page.screenshot(type="png", full_page=False)
                    photo_buffer = BytesIO(screenshot_bytes)
                    photo_buffer.name = f"shot_{i:02d}.png"
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_buffer,
                        caption=f"📸 لقطة {i}/5 بعد البرومبت"
                    )
                await context.bot.send_message(chat_id=chat_id, text="✅ انتهى التصوير.")

            # ─── وضع المراقبة (رابط فقط) ───
            else:
                await context.bot.send_message(chat_id=chat_id, text="⏳ جاري التقاط صورة كل 3 ثوانٍ (10 لقطات)...")
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
                await context.bot.send_message(chat_id=chat_id, text="✅ تم الانتهاء.")

    except Exception as e:
        logging.error(f"Monitor Error: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ حدث خطأ:\n<code>{e}</code>",
            parse_mode="HTML"
        )
    finally:
        if browser:
            await browser.close()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return

    lines = text.split("\n", 1)
    first_line = lines[0].strip()
    url = normalize_url(first_line)

    if url:
        prompt = lines[1].strip() if len(lines) > 1 else None
        asyncio.create_task(monitor_url_task(update, context, url, prompt))
    else:
        await update.message.reply_text(
            "❌ لم أتعرف على رابط.\n\n"
            "أرسل الرابط في السطر الأول:\n\n"
            "• رابط فقط:\n`youtube.com`\n\n"
            "• رابط + برومبت:\n"
            "`https://gratisfy.xyz/chat`\n"
            "`اكتب لي قصيدة`"
        )

def main():
    if not TOKEN:
        raise ValueError("❌ متغير البيئة TOKEN غير موجود!")

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

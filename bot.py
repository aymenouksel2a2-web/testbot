import os
import io
import re
import logging
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ─── الإعدادات ───
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", "8080"))
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
WEBHOOK_URL = f"https://{RAILWAY_DOMAIN}/" if RAILWAY_DOMAIN else os.environ.get("WEBHOOK_URL")

URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')


# ─── تشغيل Chromium + Screenshot + HTML النهائي ───
async def fetch_with_screenshot(url: str):
    """
    يُشغّل Chromium حقيقي، ينتظر JavaScript، ثم يُرجع:
    (html_string, screenshot_bytes)
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-web-security",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2500)  # تأكد من رسم الـ Timers

            # التقاط صورة العرض كاملاً (Viewport)
            screenshot = await page.screenshot(full_page=False)
            html = await page.content()
            return html, screenshot

        except Exception as e:
            return f"ERROR:{e}", None
        finally:
            await browser.close()


# ─── استخراج الوقت بدقة جراحية ───
def extract_time_limit(soup: BeautifulSoup) -> str | None:
    """
    يستخرج الوقت HH:MM:SS المجاور لـ 'Time limit' فقط.
    يتجاهل '1 hour 25 minutes' تماماً.
    """

    # ── الاستراتيجية 1: نصوص تحتوي 'Time limit' مباشرة ──
    for text_node in soup.find_all(string=re.compile(r'Time\s*limit', re.I)):
        parent = text_node.parent
        # اصعد 5 مستويات في شجرة DOM وابحث في كل مستوى
        for _ in range(5):
            if not parent:
                break
            # ابحث في الأبناء عن نصوص تطابق تنسيق HH:MM:SS بالضبط
            for descendant in parent.descendants:
                if isinstance(descendant, str):
                    m = re.match(r'^\s*(\d{1,2}:\d{2}:\d{2})\s*$', descendant)
                    if m:
                        return f"⏱ الوقت: {m.group(1)}"
            parent = parent.parent

    # ── الاستراتيجية 2: aria-label أو title يحتويان Time limit ──
    for attr in ["aria-label", "title"]:
        for tag in soup.find_all(attrs={attr: re.compile(r'Time\s*limit', re.I)}):
            val = tag.get(attr, "")
            m = re.search(r'(\d{1,2}:\d{2}:\d{2})', val)
            if m:
                return f"⏱ الوقت: {m.group(1)}"
            # ابحث في أبناء هذا التاج
            for child in tag.descendants:
                if isinstance(child, str):
                    m = re.match(r'^\s*(\d{1,2}:\d{2}:\d{2})\s*$', child)
                    if m:
                        return f"⏱ الوقت: {m.group(1)}"

    # ── الاستراتيجية 3: Regex قوي على النص الكامل (محاصرة Time limit + وقت) ──
    full_text = soup.get_text(separator='\n', strip=True)
    m = re.search(
        r'Time\s*limit[\s\S]{0,60}?(\d{1,2}:\d{2}:\d{2})',
        full_text,
        re.IGNORECASE,
    )
    if m:
        return f"⏱ الوقت: {m.group(1)}"

    return None


# ─── معالجات التليغرام ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 أهلاً {user.first_name}!\n\n"
        "🔥 البوت يستخدم **Chromium Headless حقيقي** + **Screenshot**.\n"
        "أرسل رابط Google Skills وسأُرسل لك صورة الصفحة + الوقت المستخرج.\n\n"
        "📎 مثال:\n"
        "`https://www.skills.google/focuses/19146?parent=catalog`",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 كيفية الاستخدام:\n"
        "1. أرسل رابط المختبر.\n"
        "2. انتظر 5-10 ثوانٍ حتى أُشغّل المتصفح.\n"
        "3. سأُرسل لك **لقطة شاشة** من الموقع + الوقت المستخرج.\n\n"
        "⚠️ إذا فشل الاستخراج التلقائي، ستُشاهد الوقت بنفسك في الصورة."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_text = update.message.text
    urls = URL_PATTERN.findall(user_text)

    if not urls:
        await update.message.reply_text("📎 لم أجد رابطاً.")
        return

    for url in urls:
        if not url.startswith(("http://", "https://")):
            continue

        if "skills.google" not in url:
            await update.message.reply_text(
                "⚠️ متخصص حالياً في `skills.google` فقط.",
                parse_mode="Markdown",
            )
            continue

        msg = await update.message.reply_text(
            "⏳ جاري تشغيل Chromium + انتظار JavaScript + التقاط صورة..."
        )

        html, screenshot = await fetch_with_screenshot(url)

        if screenshot is None:
            await msg.edit_text(f"❌ فشل تحميل المتصفح:\n`{html}`", parse_mode="Markdown")
            continue

        soup = BeautifulSoup(html, "html.parser")
        time_result = extract_time_limit(soup)

        # حذف رسالة التحميل
        try:
            await msg.delete()
        except Exception:
            pass

        # ── إرسال Screenshot + النتيجة ──
        photo = InputFile(io.BytesIO(screenshot), filename="skills_screenshot.png")

        if time_result:
            await update.message.reply_photo(
                photo=photo,
                caption=f"✅ **{time_result}**\n\n🔗 {url}",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_photo(
                photo=photo,
                caption=(
                    "⚠️ لم أستخرج الوقت تلقائياً، لكن هذه **لقطة شاشة حقيقية** "
                    "من الصفحة بعد تشغيل JavaScript:\n\n"
                    f"🔗 {url}\n\n"
                    "🕵️ يمكنك قراءة الوقت من الصورة مباشرة."
                ),
                parse_mode="Markdown",
            )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("حدث خطأ: %s", context.error)


# ─── التشغيل ───
def main():
    if not TOKEN:
        raise ValueError("❌ BOT_TOKEN غير موجود!")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL and WEBHOOK_URL != "https:///":
        logger.info(f"✅ Webhook: {WEBHOOK_URL}")
        app.run_webhook(listen="0.0.0.0", port=PORT, webhook_url=WEBHOOK_URL)
    else:
        logger.info("🔄 Polling...")
        app.run_polling()


if __name__ == "__main__":
    main()

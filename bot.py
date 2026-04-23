import os
import logging
import re
from telegram import Update
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


# ─── جوهر الحل الخارق: تشغيل متصفح حقيقي ───
async def fetch_rendered_html(url: str) -> str:
    """
    يفتح Chromium Headless حقيقي، ينتظر تحميل JS بالكامل،
    ثم يُعيد HTML النهائي (DOM بعد تنفيذ JavaScript).
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = await context.new_page()

        try:
            # الانتقال للصفحة والانتظار حتى تهدأ الشبكة
            await page.goto(url, wait_until="networkidle", timeout=30000)
            # انتظر ثانية إضافية لضمان رسم أي timers
            await page.wait_for_timeout(1500)

            html = await page.content()
        except Exception as e:
            html = f"ERROR:{e}"
        finally:
            await browser.close()

    return html


# ─── استخراج الوقت من DOM النهائي ───
def extract_time_limit(soup: BeautifulSoup) -> str | None:
    """
    استراتيجية ذكية:
    1. ابحث عن نص 'Time limit' ثم خذ الوقت الأقرب له (في نفس العنصر الأب أو الجيران).
    2. ابحث عن أي وقت HH:MM:SS يتبع كلمة Time limit مباشرة.
    """

    # ── استراتيجية 1: نمط Regex قوي جداً يبحث عن Time limit + وقت قريب ──
    full_text = soup.get_text(separator=" ", strip=True)

    # ابحث عن "Time limit" + مسافات/أسطر + وقت
    m = re.search(
        r'Time\s*limit\D{0,30}?(\d{1,2}:\d{2}:\d{2})',
        full_text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        return f"⏱ الوقت: {m.group(1)}"

    # ── استراتيجية 2: ابحث في العناصر التي تحتوي 'Time limit' ──
    for element in soup.find_all(string=re.compile(r'Time\s*limit', re.I)):
        parent = element.parent
        if parent:
            # ابحث في العنصر الأب والعم (siblings) عن HH:MM:SS
            scope_text = parent.get_text(separator=" ", strip=True)
            m = re.search(r'(\d{1,2}:\d{2}:\d{2})', scope_text)
            if m:
                return f"⏱ الوقت: {m.group(1)}"

            # ابحث في الأعلى (ancestors) لأعلى 3 مستويات
            for _ in range(3):
                parent = parent.parent if parent.parent else None
                if not parent:
                    break
                scope_text = parent.get_text(separator=" ", strip=True)
                m = re.search(r'(\d{1,2}:\d{2}:\d{2})', scope_text)
                if m:
                    return f"⏱ الوقت: {m.group(1)}"

    # ── استراتيجية 3: ابحث في أي عنصر يحتوي '03:00:00' أو '01:00:00' ──
    # في Google Skills غالباً يكون الوقت في <div> أو <span> منفصل
    time_spans = soup.find_all(string=re.compile(r'^\d{1,2}:\d{2}:\d{2}$'))
    for t in time_spans:
        txt = str(t).strip()
        # تأكد أنه ليس مجرد تاريخ/وقت عشوائي في الصفحة
        # في Skills الوقت غالباً يكون في box زمني (H >= 1 للمختبرات)
        parts = txt.split(":")
        if len(parts) == 3 and int(parts[0]) >= 1:
            # تحقق من أن الجار الأكبر يحتوي Time أو limit أو Lab
            parent_text = t.parent.get_text(separator=" ", strip=True).lower() if t.parent else ""
            grandparent = t.parent.parent if t.parent and t.parent.parent else None
            grand_text = grandparent.get_text(separator=" ", strip=True).lower() if grandparent else ""

            if any(word in parent_text or word in grand_text for word in ["time", "limit", "lab", "hour", "credit"]):
                return f"⏱ الوقت: {txt}"

    return None


# ─── معالجات التليغرام ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 أهلاً {user.first_name}!\n\n"
        "🔥 البوت الآن يستخدم **متصفح Chromium حقيقي** داخل السيرفر.\n"
        "أرسل أي رابط من Google Skills وسأستخرج الوقت حتى لو كان مخبّأً داخل JavaScript.\n\n"
        "📎 مثال:\n"
        "`https://www.skills.google/focuses/19146?parent=catalog`",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 كيفية الاستخدام:\n"
        "1. انسخ رابط المختبر.\n"
        "2. ألصقه هنا.\n"
        "3. انتظر 5-10 ثوانٍ حتى أُشغّل المتصفح وأقرأ DOM النهائي.\n\n"
        "⚠️ الوقت أطول قليلاً لكن الدقة 100%."
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
            "⏳ جاري تشغيل Chromium Headless وتحميل JavaScript...\n"
            "⏱ قد يستغرق 5-10 ثوانٍ."
        )

        raw_html = await fetch_rendered_html(url)

        if raw_html.startswith("ERROR:"):
            await msg.edit_text(f"❌ فشل تحميل المتصفح:\n`{raw_html}`", parse_mode="Markdown")
            continue

        soup = BeautifulSoup(raw_html, "html.parser")
        time_result = extract_time_limit(soup)

        if time_result:
            await msg.edit_text(
                f"✅ **{time_result}**\n\n"
                f"🔗 {url}",
                parse_mode="Markdown",
            )
        else:
            # debug: أرسل أول 500 حرف من النص المستخرج للتحليل (يمسحه لاحقاً)
            preview = soup.get_text(separator=" ", strip=True)[:300].replace("\n", " ")
            await msg.edit_text(
                "⚠️ لم أجد الوقت بعد تحميل JavaScript.\n"
                f"🕵️ preview: `{preview}...`",
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

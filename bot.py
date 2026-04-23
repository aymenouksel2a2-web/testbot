import os
import logging
import re
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from bs4 import BeautifulSoup
import aiohttp

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

# Regex لاكتشاف الروابط
URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')


# ─── دوال المساعدة ───
def format_seconds(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"⏱ الوقت: {h:02d}:{m:02d}:{s:02d}"


def parse_iso_duration(duration: str) -> str:
    """تحويل PT3H أو PT1H25M إلى 03:00:00"""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    if match:
        h = int(match.group(1) or 0)
        m = int(match.group(2) or 0)
        s = int(match.group(3) or 0)
        return f"{h:02d}:{m:02d}:{s:02d}"
    return duration


async def fetch_url_html(url: str):
    """جلب HTML وإرجاع (html_text, BeautifulSoup_object)"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    return (f"⚠️ تعذر الوصول للصفحة (كود HTTP: {response.status})", None)
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                return (html, soup)
    except Exception as e:
        return (f"❌ خطأ في الاتصال: {e}", None)


def extract_time_from_skills(html: str, soup: BeautifulSoup) -> str | None:
    """
    تستخرج Time Limit من صفحات skills.google بدقة.
    الأولوية:
    1. timeLimitSeconds/timeLimit داخل <script>
    2. النص الظاهر المحيط بـ "Time limit"
    3. JSON-LD (احتياطي)
    """

    # ── المحاولة 1: البحث الدقيق في كل <script> ──
    for script in soup.find_all("script"):
        text = script.string or ""
        # ابحث عن timeLimitSeconds: 10800  (الأدق في Google Skills)
        m = re.search(r'timeLimitSeconds["\']?\s*[:=]\s*(\d+)', text)
        if m:
            logger.info(f"Found timeLimitSeconds raw: {m.group(1)}")
            return format_seconds(int(m.group(1)))

        # ابحث عن "timeLimit": "03:00:00"
        m = re.search(r'timeLimit["\']?\s*[:=]\s*["\']?(\d{1,2}:\d{2}:\d{2})', text)
        if m:
            logger.info(f"Found timeLimit string: {m.group(1)}")
            return f"⏱ الوقت: {m.group(1)}"

    # ── المحاولة 2: البحث في النص الظاهر حول "Time limit" ──
    # نحافظ على الأسطر المنفصلة لأن الوقت غالبًا في سطر مستقل فوق/تحت "Time limit"
    lines = [
        line.strip()
        for line in soup.get_text(separator="\n", strip=True).splitlines()
        if line.strip()
    ]

    for i, line in enumerate(lines):
        if re.search(r'Time\s*limit', line, re.IGNORECASE):
            # افحص السطرين السابقين والسطرين التاليين (تجاهل السطر نفسه)
            for j in range(max(0, i - 2), min(len(lines), i + 3)):
                candidate = lines[j]
                if re.search(r'Time\s*limit', candidate, re.IGNORECASE):
                    continue
                time_match = re.search(r'\b(\d{1,2}:\d{2}:\d{2})\b', candidate)
                if time_match:
                    logger.info(f"Found time near 'Time limit': {time_match.group(1)}")
                    return f"⏱ الوقت: {time_match.group(1)}"

    # ── المحاولة 3: JSON-LD (Structured Data) - احتياطي ──
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    tr = item.get("timeRequired")
                    if tr:
                        logger.info(f"Found JSON-LD timeRequired: {tr}")
                        return f"⏱ الوقت: {parse_iso_duration(tr)}"
        except Exception:
            pass

    return None


# ─── معالجات الأوامر ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 أهلاً {user.first_name}!\n\n"
        "أنا بوت متخصص في استخراج **وقت المختبر** من روابط Google Skills Boost.\n\n"
        "📎 أرسل لي رابطاً مثل:\n"
        "`https://www.skills.google/focuses/19146?parent=catalog`",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 كيفية الاستخدام:\n"
        "1. انسخ رابط المختبر من skills.google.\n"
        "2. ألصقه هنا.\n"
        "3. سأرسل لك الوقت المحدد (مثل 03:00:00).\n\n"
        "⚠️ بعض الصفحات تتطلب تسجيل دخين، وقد لا أتمكن من قراءتها."
    )


# ─── معالج الرسائل ───
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_text = update.message.text
    urls = URL_PATTERN.findall(user_text)

    if not urls:
        await update.message.reply_text(
            "📎 لم أجد رابطاً.\nأرسل لي رابطاً من skills.google لأستخرج الوقت."
        )
        return

    for url in urls:
        if not url.startswith(("http://", "https://")):
            continue

        if "skills.google" not in url:
            await update.message.reply_text(
                "⚠️ هذا البوت متخصص حالياً في روابط `skills.google` فقط.\n"
                f"🔗 {url}",
                parse_mode="Markdown"
            )
            continue

        msg = await update.message.reply_text("⏳ جاري تحليل الرابط واستخراج الوقت...")
        html, soup = await fetch_url_html(url)

        if soup is None:
            await msg.edit_text(f"❌ فشل الوصول:\n{html}")
            continue

        time_result = extract_time_from_skills(html, soup)

        if time_result:
            await msg.edit_text(
                f"✅ **{time_result}**\n\n"
                f"🔗 {url}",
                parse_mode="Markdown"
            )
        else:
            await msg.edit_text(
                "⚠️ لم أتمكن من العثور على الوقت في هذه الصفحة.\n"
                "الأسباب المحتملة:\n"
                "• الصفحة تتطلب تسجيل دخول.\n"
                "• تم تغيير تصميم الموقع.\n"
                "• الرابط غير صحيح."
            )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("حدث خطأ: %s", context.error)


# ─── التشغيل ───
def main():
    if not TOKEN:
        raise ValueError("❌ لم يتم تعيين متغير البيئة BOT_TOKEN!")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL and WEBHOOK_URL != "https:///":
        logger.info(f"✅ تشغيل Webhook: {WEBHOOK_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL,
        )
    else:
        logger.info("🔄 تشغيل Polling...")
        app.run_polling()


if __name__ == "__main__":
    main()

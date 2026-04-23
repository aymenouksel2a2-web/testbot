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

# Regex لاكتشاف الروابط (محسّن)
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
    تستخرج الـ Time Limit من صفحات skills.google (Google Cloud Skills Boost).
    تعمل حتى لو كان الوقت مخبّأً داخل JSON أو داخل <script>.
    """

    # ── المحاولة 1: JSON-LD (Structured Data) ──
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    tr = item.get("timeRequired")
                    if tr:
                        # غالباً ما يكون الشكل PT3H
                        iso = parse_iso_duration(tr)
                        return f"⏱ الوقت: {iso}"
        except Exception:
            pass

    # ── المحاولة 2: داخل أي <script> ابحث عن timeLimitSeconds أو duration بالثواني ──
    for script in soup.find_all("script"):
        if not script.string:
            continue
        text = script.string

        # ابحث عن timeLimitSeconds: 10800
        m = re.search(r'timeLimitSeconds["\']?\s*[:=]\s*(\d+)', text)
        if m:
            return format_seconds(int(m.group(1)))

        # ابحث عن "duration": 10800
        m = re.search(r'"duration"\s*:\s*(\d+)', text)
        if m:
            secs = int(m.group(1))
            if secs >= 3600:  # فقط إذا كانت القيمة منطقية (ساعة أو أكثر)
                return format_seconds(secs)

    # ── المحاولة 3: البحث في النص المرئي عن HH:MM:SS بالقرب من Time limit ──
    # نستخدم get_text لإزالة التاجات والحصول على نص نظيف
    visible_text = soup.get_text(separator=" ", strip=True)

    # ابحث عن كل الأوقات
    time_matches = list(re.finditer(r'\b(\d{1,2}:\d{2}:\d{2})\b', visible_text))

    for match in time_matches:
        # خذ مقطعاً من النص حول الوقت (±200 حرف)
        start = max(0, match.start() - 200)
        end = min(len(visible_text), match.end() + 200)
        snippet = visible_text[start:end].lower()

        if any(keyword in snippet for keyword in ["time limit", "time-limit", "timelimit", "timed", "limit"]):
            return f"⏱ الوقت: {match.group(1)}"

    # ── المحاولة 4: احتياطي - أي وقت طويل (ساعات >= 1) ──
    for match in time_matches:
        hours = int(match.group(1).split(":")[0])
        if hours >= 1:
            return f"⏱ الوقت: {match.group(1)}"

    return None


# ─── معالجات الأوامر ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 أهلاً {user.first_name}!\n\n"
        "أنا بوت متخصص في استخراج **وقت المختبر** من روابط Google Skills Boost.\n\n"
        "📎 فقط أرسل لي رابطاً مثل:\n"
        "`https://www.skills.google/focuses/19146?parent=catalog`",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 كيفية الاستخدام:\n"
        "1. انسخ رابط المختبر من skills.google.\n"
        "2. ألصقه هنا في الدردشة.\n"
        "3. سأرسل لك الوقت المحدد (مثل 03:00:00) فوراً.\n\n"
        "⚠️ ملاحظة: بعض المختبرات قد تحتاج تسجيل دخول، وفي هذه الحالة قد لا أتمكن من قراءة الوقت."
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
        # تأكد من صحة البروتوكول
        if not url.startswith(("http://", "https://")):
            continue

        # التحقق إذا كان الرابط من skills.google
        is_skills = "skills.google" in url

        if not is_skills:
            await update.message.reply_text(
                "⚠️ هذا البوت متخصص حالياً في روابط `skills.google` فقط.\n"
                f"🔗 {url}",
                parse_mode="Markdown"
            )
            continue

        # جلب الصفحة
        msg = await update.message.reply_text("⏳ جاري تحليل الرابط واستخراج الوقت...")
        html, soup = await fetch_url_html(url)

        if soup is None:
            await msg.edit_text(f"❌ فشل الوصول:\n{html}")
            continue

        # استخراج الوقت
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
                "قد يكون أحد الأسباب:\n"
                "• الصفحة تتطلب تسجيل دخين.\n"
                "• تم تغيير هيكل الموقع.\n"
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

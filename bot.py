import os
import logging
import re
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
URL_PATTERN = re.compile(
    r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
)


# ─── دوال جلب النص ───
async def fetch_page_text(url: str) -> str:
    """يجلب HTML الصفحة ويستخرج النص المرئي فقط"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    return f"⚠️ تعذر الوصول للصفحة (كود: {response.status})"
                
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")

                # إزالة العناصر غير المرغوبة
                for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                    tag.decompose()

                # استخراج النص
                text = soup.get_text(separator="\n", strip=True)

                # تنظيف الأسطر الفارغة المتكررة
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                clean_text = "\n".join(lines)

                if not clean_text:
                    return "⚠️ الصفحة فارغة أو تعتمد على JavaScript لتوليد المحتوى."
                return clean_text

    except aiohttp.ClientError as e:
        return f"❌ خطأ في الاتصال: {e}"
    except Exception as e:
        return f"❌ خطأ غير متوقع: {e}"


async def send_long_text(update: Update, text: str, max_length: int = 4000):
    """يقسم النص الطويل ويرسله على أجزاء (حد Telegram 4096)"""
    if len(text) <= max_length:
        await update.message.reply_text(text)
        return

    # تقسيم ذكي عند أقرب سطر جديد
    while text:
        if len(text) <= max_length:
            await update.message.reply_text(text)
            break

        # ابحث عن آخر \n قبل الحد
        split_idx = text.rfind("\n", 0, max_length)
        if split_idx == -1:
            split_idx = max_length  # اقطع عند الحد إذا لم يوجد سطر

        chunk = text[:split_idx].strip()
        if chunk:
            await update.message.reply_text(chunk)
        text = text[split_idx:].strip()


# ─── معالجات الأوامر ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"أهلاً {user.first_name}! 👋\n\n"
        "أرسل لي أي **رابط** وسأستخرج لك النص الكامل من الصفحة.\n"
        "مثال:\n`https://example.com/article`",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 كيفية الاستخدام:\n"
        "1. أرسل رابطاً مباشرة في الدردشة.\n"
        "2. انتظر ثوانٍ حتى أقوم بجلب النص.\n"
        "3. إذا كان النص طويلاً جداً، سأرسله على أجزاء.\n\n"
        "⚠️ ملاحظة: بعض المواقع المحمية أو التي تعتمد على JavaScript قد لا تعمل."
    )


# ─── معالج الرسائل ───
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_text = update.message.text
    urls = URL_PATTERN.findall(user_text)

    if not urls:
        # إذا لم يكن الرسالة تحتوي رابطاً، نرددها أو نتجاهل
        await update.message.reply_text(
            "📎 لم أجد رابطاً في رسالتك.\n"
            "أرسل لي رابطاً مباشرة وسأقوم باستخراج النص."
        )
        return

    # معالجة كل رابط في الرسالة
    for url in urls:
        # تأكد من أن الرابط يبدأ بـ http
        if not url.startswith(("http://", "https://")):
            continue

        await update.message.reply_text(f"⏳ جاري جلب المحتوى من:\n`{url}`", parse_mode="Markdown")
        
        content = await fetch_page_text(url)
        await send_long_text(update, content)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("حدث خطأ: %s", context.error)


# ─── التشغيل ───
def main():
    if not TOKEN:
        raise ValueError("❌ لم يتم تعيين متغير البيئة BOT_TOKEN!")

    app = Application.builder().token(TOKEN).build()

    # التسجيل
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    # Webhook أو Polling
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

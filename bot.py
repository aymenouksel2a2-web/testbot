import os
import io
import re
import logging
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

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


async def fetch_skills_data(url: str):
    """
    يُشغّل Chromium حقيقي، ينتظر React، ثم يُنفّذ JavaScript مخصوص
    للبحث عن العنصر الذي يحتوي 'Time limit' واستخراج HH:MM:SS الأقرب له.
    يُرجع: (screenshot_bytes, time_string أو None)
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
            await page.wait_for_timeout(3000)  # انتظر React يُنهي الرندر

            screenshot = await page.screenshot(full_page=False)

            # ═══════════════════════════════════════════════════════
            # الحل الخارق: JavaScript داخل المتصفح يقرأ DOM النهائي
            # ═══════════════════════════════════════════════════════
            extracted_time = await page.evaluate(
                """() => {
                    // ── استراتيجية 1: ابحث عن نص يحتوي 'Time limit' (غير حساس لحالة الأحرف) ──
                    const allElements = document.querySelectorAll('body *:not(script):not(style)');
                    for (const el of allElements) {
                        const txt = el.textContent || '';
                        if (/Time\\s*limit/i.test(txt)) {
                            // ابحث في هذا العنصر وأبنائه عن HH:MM:SS
                            const m = txt.match(/(\\d{1,2}:\\d{2}:\\d{2})/);
                            if (m) return m[1];

                            // اصعد إلى الأب (3 مستويات) وابحث
                            let parent = el.parentElement;
                            for (let i = 0; i < 4; i++) {
                                if (!parent) break;
                                const pTxt = parent.textContent || '';
                                const pM = pTxt.match(/(\\d{1,2}:\\d{2}:\\d{2})/);
                                if (pM) return pM[1];
                                parent = parent.parentElement;
                            }
                        }
                    }

                    // ── استراتيجية 2: العكس - ابحث عن كل HH:MM:SS ثم تحقق من السياق ──
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let node;
                    while (node = walker.nextNode()) {
                        const t = node.textContent.trim();
                        if (/^(\\d{1,2}:\\d{2}:\\d{2})$/.test(t)) {
                            // اصعد 5 مستويات وتحقق أن السياق يحتوي Time limit
                            let p = node.parentElement;
                            for (let i = 0; i < 5; i++) {
                                if (!p) break;
                                const ctx = (p.textContent || '').toLowerCase();
                                if (ctx.includes('time limit') || (ctx.includes('time') && ctx.includes('limit'))) {
                                    return t;
                                }
                                if (/start lab/.test(ctx)) {  // العنصر يكون في صندوق Start Lab
                                    return t;
                                }
                                p = p.parentElement;
                            }
                        }
                    }

                    // ── استراتيجية 3: محاولة أخيرة عبر XPath (Google Skills أحيانًا تستخدمه) ──
                    const xp = document.evaluate(
                        "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'time limit')]",
                        document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
                    );
                    for (let i = 0; i < xp.snapshotLength; i++) {
                        const el = xp.snapshotItem(i);
                        const m = (el.textContent || '').match(/(\\d{1,2}:\\d{2}:\\d{2})/);
                        if (m) return m[1];
                        // ابحث في الأشقاء
                        const parent = el.parentElement;
                        if (parent) {
                            const pm = (parent.textContent || '').match(/(\\d{1,2}:\\d{2}:\\d{2})/);
                            if (pm) return pm[1];
                        }
                    }

                    return null;
                }"""
            )

            await browser.close()
            return screenshot, extracted_time

        except Exception as e:
            await browser.close()
            return None, f"ERROR:{e}"


# ─── معالجات التليغرام ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 أهلاً {user.first_name}!\n\n"
        "🔥 البوت يشغّل **Chromium حقيقي داخل السيرفر**.\n"
        "أرسل رابط Google Skills وسأُرسل لك **لقطة شاشة** + الوقت المستخرج من DOM النهائي.\n\n"
        "📎 مثال:\n"
        "`https://www.skills.google/focuses/19146?parent=catalog`",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 كيفية الاستخدام:\n"
        "1. أرسل رابط المختبر.\n"
        "2. انتظر 5-10 ثوانٍ.\n"
        "3. سأُرسل **صورة حقيقية** من الموقع + الوقت.\n\n"
        "⚠️ إذا فشل الاستخراج، ستقرأ الوقت بنفسك من الصورة."
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

        msg = await update.message.reply_text("⏳ جاري تشغيل Chromium + انتظار JavaScript...")

        screenshot, result = await fetch_skills_data(url)

        try:
            await msg.delete()
        except Exception:
            pass

        if screenshot is None:
            await update.message.reply_text(f"❌ فشل المتصفح:\n`{result}`", parse_mode="Markdown")
            continue

        photo = InputFile(io.BytesIO(screenshot), filename="screenshot.png")

        if result and not result.startswith("ERROR"):
            await update.message.reply_photo(
                photo=photo,
                caption=f"✅ **⏱ الوقت: {result}**\n\n🔗 {url}",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_photo(
                photo=photo,
                caption=(
                    "⚠️ لم أستخرج الوقت تلقائياً (DOM ديناميكي).\n"
                    "🔍 **لكن هذه لقطة شاشة حقيقية من الصفحة:**\n\n"
                    f"🔗 {url}\n\n"
                    "👁️ يمكنك قراءة الوقت مباشرة من الصورة."
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

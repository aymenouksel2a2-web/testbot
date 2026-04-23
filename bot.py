import os
import asyncio
import logging
from io import BytesIO
from typing import Any, Dict

from telegram import Update, InputFile, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import BadRequest
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
URL = os.environ.get("TARGET_URL", "https://gratisfy.xyz/chat")
PORT = int(os.environ.get("PORT", "8080"))
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

LOGIN_EMAIL = os.environ.get("LOGIN_EMAIL")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD")

streams: Dict[int, Dict[str, Any]] = {}
streams_lock = asyncio.Lock()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت البث المباشر\n\n"
        "📌 /stream — بدء بث شاشة الموقع (لقطة كل 3 ثوانٍ)\n"
        "📌 /stop  — إيقاف البث\n\n"
        "⚠️ قد يستهلك البث موارد الجهاز."
    )


async def stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with streams_lock:
        if chat_id in streams and streams[chat_id].get("active"):
            await update.message.reply_text("⚠️ البث يعمل بالفعل! أرسل /stop لإيقافه.")
            return

    await update.message.reply_text("⏳ جاري تهيئة البث...")

    task = asyncio.create_task(stream_worker(chat_id, context))

    async with streams_lock:
        streams[chat_id] = {
            "active": True,
            "task": task,
        }


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with streams_lock:
        if chat_id not in streams or not streams[chat_id].get("active"):
            await update.message.reply_text("❌ لا يوجد بث نشط حالياً.")
            return
        info = streams[chat_id]
        info["active"] = False
        msg_id = info.get("message_id")
        task = info.get("task")

    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    if msg_id:
        try:
            await context.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption="⏹️ توقف البث المباشر.",
            )
        except Exception:
            pass

    await update.message.reply_text("⏹️ تم إيقاف البث وتصفية المتصفح.")


async def stream_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    browser = None
    page = None
    pw = None
    message_id = None

    async def snap(caption: str, first: bool = False):
        """يلتقط صورة ويرسلها أو يُحدّثها. يستخدم BytesIO بدون ملفات مؤقتة."""
        nonlocal message_id
        if page is None or page.is_closed():
            return

        try:
            # التقاط اللقطة (مع حماية timeout 15 ثانية)
            raw = await asyncio.wait_for(
                page.screenshot(type="jpeg", quality=80),
                timeout=15.0,
            )
            buf = BytesIO(raw)

            if first or message_id is None:
                msg = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(buf, filename="stream.jpg"),
                    caption=caption,
                )
                message_id = msg.message_id
                async with streams_lock:
                    if chat_id in streams:
                        streams[chat_id]["message_id"] = message_id
            else:
                try:
                    await context.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=message_id,
                        media=InputMediaPhoto(
                            media=InputFile(buf, filename="stream.jpg"),
                            caption=caption,
                        ),
                    )
                except BadRequest as e:
                    err = str(e).lower()
                    if "message is not modified" in err:
                        pass
                    elif "message to edit not found" in err:
                        # إذا حُذفت الرسالة، نعيد الإرسال
                        message_id = None
                        msg = await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=InputFile(buf, filename="stream.jpg"),
                            caption=caption,
                        )
                        message_id = msg.message_id
                        async with streams_lock:
                            if chat_id in streams:
                                streams[chat_id]["message_id"] = message_id
                    else:
                        logger.warning(f"BadRequest: {e}")

        except asyncio.TimeoutError:
            logger.warning("Screenshot timeout")
        except Exception as e:
            logger.warning(f"Snap error: {e}")

    try:
        pw = await async_playwright().start()

        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--no-first-run",
                "--no-zygote",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        page = await browser.new_page(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        # ═══════════════════════════════════════
        # 1) لقطة أولية (عن:blank — بيضاء طبيعي)
        # ═══════════════════════════════════════
        await snap("🌐 جاري فتح المتصفح...", first=True)
        await asyncio.sleep(3)

        # ═══════════════════════════════════════
        # 2) فتح الموقع — استخدام "load" بدلاً من "networkidle"
        #    لأن networkidle يتعلّق مع التطبيقات التي تستطلع الشبكة.
        # ═══════════════════════════════════════
        try:
            await page.goto(URL, wait_until="load", timeout=45000)
        except PlaywrightTimeout:
            await snap("⛔️ انتهى الوقت أثناء تحميل الموقع")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ الموقع لا يستجيب (Timeout). حاول مجدداً لاحقاً."
            )
            return

        # نمنح الصفحة مهلة إضافية للـ SPA حتى يُرسم المحتوى
        await page.wait_for_timeout(3000)
        await snap("🌐 تم الوصول إلى الموقع")
        await asyncio.sleep(3)

        # ═══════════════════════════════════════
        # 3) إغلاق Popups شائعة
        # ═══════════════════════════════════════
        for sel in [
            "button:has-text('Close')",
            "[aria-label='Close']",
            ".popup-close",
            "button.close",
            "button:has-text('Got it')",
        ]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(500)
                    await snap("🧹 تم إغلاق نافذة منبثقة")
                    await asyncio.sleep(3)
            except Exception:
                pass

        # ═══════════════════════════════════════
        # 4) تسجيل الدخول (إن وُجدت البيانات)
        # ═══════════════════════════════════════
        if LOGIN_EMAIL and LOGIN_PASSWORD:
            await snap("🔐 البحث عن زر Log in...")

            login_btn = None
            for sel in [
                'text=Log in',
                'button:has-text("Log in")',
                'a:has-text("Log in")',
                '[data-testid="login-button"]',
            ]:
                login_btn = await page.query_selector(sel)
                if login_btn and await login_btn.is_visible():
                    break

            if login_btn:
                await login_btn.click()
                await snap("🔐 تم الضغط على Log in")
                await asyncio.sleep(3)

                # انتظار حقول النموذج
                await page.wait_for_selector(
                    'input[name="email"], input[type="email"]',
                    timeout=10000,
                    state="visible",
                )

                await snap("📝 جاري كتابة البريد...")
                await page.fill('input[name="email"], input[type="email"]', LOGIN_EMAIL)
                await asyncio.sleep(2)

                await snap("📝 جاري كتابة كلمة المرور...")
                await page.fill('input[name="password"], input[type="password"]', LOGIN_PASSWORD)
                await asyncio.sleep(2)

                await snap("🔑 جاري الضغط على Sign in...")

                sign_in_btn = await page.query_selector(
                    'button:has-text("Sign in"), button[type="submit"]'
                )
                if sign_in_btn and await sign_in_btn.is_visible():
                    try:
                        async with page.expect_navigation(wait_until="load", timeout=15000):
                            await sign_in_btn.click()
                    except PlaywrightTimeout:
                        await page.wait_for_timeout(3000)
                else:
                    try:
                        async with page.expect_navigation(wait_until="load", timeout=15000):
                            await page.press('input[name="password"], input[type="password"]', "Enter")
                    except PlaywrightTimeout:
                        await page.wait_for_timeout(3000)

                # التحقق
                email_still_visible = await page.is_visible(
                    'input[name="email"], input[type="email"]'
                )
                if email_still_visible:
                    await snap("⚠️ بقي نموذج الدخول ظاهراً (بيانات خاطئة؟)، البث مستمر...")
                else:
                    await snap("✅ تم تسجيل الدخول! البث المباشر يعمل الآن.")
                await asyncio.sleep(3)
            else:
                await snap("ℹ️ لم يُعثر على زر Log in، البث مستمر...")
                await asyncio.sleep(3)
        else:
            await snap("ℹ️ لا توجد بيانات LOGIN_EMAIL/LOGIN_PASSWORD، البث بدون تسجيل دخول.")
            await asyncio.sleep(3)

        # ═══════════════════════════════════════
        # 5) الحلقة المستمرة
        # ═══════════════════════════════════════
        while True:
            async with streams_lock:
                if chat_id not in streams or not streams[chat_id].get("active"):
                    break
            await snap("📡 لقطة مباشرة · مُحدّثة الآن")
            await asyncio.sleep(3)

    except asyncio.CancelledError:
        logger.info(f"Stream worker cancelled for {chat_id}")
        raise
    except Exception as e:
        logger.exception("Stream worker error")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ توقف البث بسبب خطأ:\n{e}"
            )
        except Exception:
            pass
    finally:
        # ─── تنظيف Playwright بغض النظر عن سبب الخروج ───
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass
        async with streams_lock:
            streams.pop(chat_id, None)


def main():
    if not TOKEN:
        raise RuntimeError("❌ متغير BOT_TOKEN غير موجود!")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stream", stream))
    app.add_handler(CommandHandler("stop", stop))

    if RAILWAY_DOMAIN:
        secret_path = TOKEN.split(":")[-1]
        webhook_url = f"https://{RAILWAY_DOMAIN}/{secret_path}"
        logger.info(f"🚀 Webhook: {webhook_url}")

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=secret_path,
            webhook_url=webhook_url,
            drop_pending_updates=True,
        )
    else:
        logger.warning("⚠️ RAILWAY_PUBLIC_DOMAIN غير موجود! سيعمل بالـ Polling.")
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

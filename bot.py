import os
import asyncio
import logging
from io import BytesIO
from typing import Dict, Any

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


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت البث المباشر\n\n"
        "📌 /stream — بدء بث شاشة الموقع\n"
        "📌 /stop  — إيقاف البث\n\n"
        "⚠️ يستهلك البث موارد الجهاز."
    )


async def stream(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with streams_lock:
        if chat_id in streams and streams[chat_id].get("active"):
            await update.message.reply_text("⚠️ البث يعمل بالفعل! أرسل /stop.")
            return

    await update.message.reply_text("⏳ جاري تهيئة البث...")
    task = asyncio.create_task(stream_worker(chat_id, ctx))
    async with streams_lock:
        streams[chat_id] = {"active": True, "task": task, "message_id": None}


async def stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with streams_lock:
        info = streams.get(chat_id)
        if not info or not info.get("active"):
            await update.message.reply_text("❌ لا يوجد بث نشط.")
            return
        info["active"] = False
        msg_id = info.get("message_id")
        task = info.get("task")

    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    if msg_id:
        try:
            await ctx.bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption="⏹️ توقف البث.")
        except Exception:
            pass

    async with streams_lock:
        streams.pop(chat_id, None)

    await update.message.reply_text("⏹️ تم إيقاف البث.")


async def stream_worker(chat_id: int, tg_ctx: ContextTypes.DEFAULT_TYPE):
    pw = None
    browser = None
    browser_ctx = None
    page = None
    msg_id = None

    async def snap(caption: str, first: bool = False):
        nonlocal msg_id
        if not page or page.is_closed():
            logger.warning("Page closed, skipping snap")
            return
        try:
            raw = await asyncio.wait_for(
                page.screenshot(type="jpeg", quality=85),
                timeout=12.0,
            )
            buf = BytesIO(raw)
            if first or msg_id is None:
                m = await tg_ctx.bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(buf, filename="live.jpg"),
                    caption=caption,
                )
                msg_id = m.message_id
                async with streams_lock:
                    if chat_id in streams:
                        streams[chat_id]["message_id"] = msg_id
            else:
                try:
                    await tg_ctx.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=msg_id,
                        media=InputMediaPhoto(
                            media=InputFile(buf, filename="live.jpg"),
                            caption=caption,
                        ),
                    )
                except BadRequest as e:
                    txt = str(e).lower()
                    if "message is not modified" in txt:
                        return
                    if "message to edit not found" in txt or "wrong file" in txt:
                        msg_id = None
                        m = await tg_ctx.bot.send_photo(
                            chat_id=chat_id,
                            photo=InputFile(buf, filename="live.jpg"),
                            caption=caption,
                        )
                        msg_id = m.message_id
                        async with streams_lock:
                            if chat_id in streams:
                                streams[chat_id]["message_id"] = msg_id
                        return
                    raise
        except asyncio.TimeoutError:
            logger.error("Screenshot timeout")
        except Exception as e:
            logger.error(f"Snap error: {e}")

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
                "--disable-web-security",
            ],
        )

        browser_ctx = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        page = await browser_ctx.new_page()
        page.set_default_navigation_timeout(30000)
        page.set_default_timeout(15000)

        # ─── 1) اللقطة الأولى (about:blank — بيضاء طبيعي) ───
        await snap("🌐 جاري فتح المتصفح...", first=True)
        await asyncio.sleep(2)

        # ─── 2) فتح الموقع بأسرع طريقة ممكنة ───
        logger.info(f"Navigating to {URL}")
        try:
            # "commit" ينتظر فقط وصول أول بايت من الـ Response (لا ينتظر JS/CSS)
            await page.goto(URL, wait_until="commit", timeout=30000)
        except PlaywrightTimeout:
            logger.error("Navigation commit timeout")
            await snap("⛔️ الموقع لا يستجيب (Timeout)")
            return
        except Exception as e:
            logger.error(f"Navigation error: {e}")
            await snap("⛔️ خطأ في فتح الموقع")
            return

        # ─── 3) انتظار SPA يُنشئ المحتوى ───
        # نحاول الانتظار لـ load لكن لا نُفشل إذا انتهى الوقت (ads/tracking)
        try:
            await page.wait_for_load_state("load", timeout=8000)
        except PlaywrightTimeout:
            logger.info("Load timeout, continuing anyway")

        # انتظار يدوي أمن للـ JavaScript يُرسم الواجهة
        await page.wait_for_timeout(6000)

        # التحقق السريع من أن DOM ليس فارغًا
        try:
            html_len = await page.evaluate("document.documentElement.outerHTML.length")
            logger.info(f"HTML length after wait: {html_len}")
            if html_len < 200:
                await page.wait_for_timeout(4000)
        except Exception:
            pass

        await snap("🌐 تم الوصول إلى الموقع")
        await asyncio.sleep(3)

        # ─── 4) إغلاق Popups ───
        for sel in [
            "button:has-text('Close')",
            "[aria-label='Close']",
            ".popup-close",
            "button.close",
            "button:has-text('Got it')",
            "button:has-text('Accept')",
        ]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(800)
                    await snap("🧹 تم إغلاق نافذة منبثقة")
                    await asyncio.sleep(2)
            except Exception:
                pass

        # ─── 5) تسجيل الدخول ───
        if LOGIN_EMAIL and LOGIN_PASSWORD:
            await snap("🔐 البحث عن زر Log in...")
            login_btn = None
            for sel in ['text=Log in', 'button:has-text("Log in")', 'a:has-text("Log in")']:
                try:
                    login_btn = await page.query_selector(sel)
                    if login_btn and await login_btn.is_visible():
                        break
                except Exception:
                    continue

            if login_btn:
                await login_btn.click()
                await snap("🔐 تم الضغط على Log in")
                await asyncio.sleep(3)

                try:
                    await page.wait_for_selector(
                        'input[type="email"], input[name="email"]',
                        timeout=10000,
                        state="visible",
                    )
                except Exception:
                    pass

                try:
                    await snap("📝 جاري كتابة البريد...")
                    await page.fill('input[type="email"], input[name="email"]', LOGIN_EMAIL)
                    await asyncio.sleep(1)

                    await snap("📝 جاري كتابة كلمة المرور...")
                    await page.fill('input[type="password"], input[name="password"]', LOGIN_PASSWORD)
                    await asyncio.sleep(1)

                    await snap("🔑 جاري تسجيل الدخول...")
                    btn = await page.query_selector('button:has-text("Sign in"), button[type="submit"]')
                    if btn and await btn.is_visible():
                        await btn.click()
                    else:
                        await page.press('input[type="password"]', "Enter")

                    await page.wait_for_timeout(5000)

                    email_still = await page.is_visible('input[type="email"]')
                    if email_still:
                        await snap("⚠️ بقي نموذج الدخول (بيانات خاطئة؟)")
                    else:
                        await snap("✅ تم تسجيل الدخول!")
                    await asyncio.sleep(3)
                except Exception as e:
                    logger.error(f"Login error: {e}")
            else:
                await snap("ℹ️ لم يُعثر على زر Log in")

        # ─── 6) الحلقة المستمرة ───
        while True:
            async with streams_lock:
                if chat_id not in streams or not streams[chat_id].get("active"):
                    break
            await snap("📡 لقطة مباشرة")
            await asyncio.sleep(3)

    except asyncio.CancelledError:
        logger.info(f"Worker cancelled for {chat_id}")
        raise
    except Exception as e:
        logger.exception("Fatal worker error")
        try:
            await tg_ctx.bot.send_message(chat_id=chat_id, text=f"⚠️ توقف البث: {e}")
        except Exception:
            pass
    finally:
        # تنظيف Playwright بأي حال (ترتيب: page -> context -> browser -> playwright)
        for obj, method in [(page, "close"), (browser_ctx, "close"), (browser, "close"), (pw, "stop")]:
            if obj:
                try:
                    await getattr(obj, method)()
                except Exception:
                    pass
        async with streams_lock:
            streams.pop(chat_id, None)


def main():
    if not TOKEN:
        raise RuntimeError("❌ BOT_TOKEN غير موجود!")

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
        logger.warning("⚠️ RAILWAY_PUBLIC_DOMAIN غير موجود! يعمل بالـ Polling.")
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

import os
import asyncio
import logging
from io import BytesIO
from typing import Any, Dict

from telegram import (
    Update,
    InputFile,
    InputMediaPhoto,
)
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
URL = "https://gratisfy.xyz/chat"
PORT = int(os.environ.get("PORT", "8080"))
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

# ─── بيانات تسجيل الدخول من متغيرات Railway ───
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

    status_msg = await update.message.reply_text(f"⏳ جاري فتح المتصفح والاتصال بـ {URL} ...")

    browser = None
    page = None
    pw = None
    login_performed = False

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
            ]
        )

        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.goto(URL, wait_until="networkidle", timeout=60000)

        # ═══════════════════════════════════════
        #  🔐 تسجيل الدخول التلقائي (إن وُجد)
        # ═══════════════════════════════════════
        if LOGIN_EMAIL and LOGIN_PASSWORD:
            try:
                # إغلاق أي Popup ترحيبي
                for sel in [
                    "button:has-text('Close')",
                    "[aria-label='Close']",
                    ".popup-close",
                    "button.close",
                ]:
                    el = await page.query_selector(sel)
                    if el:
                        try:
                            await el.click()
                            await page.wait_for_timeout(300)
                        except Exception:
                            pass

                # البحث عن زر Log in العلوي
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

                if not login_btn:
                    await status_msg.edit_text("ℹ️ لا يوجد زر Log in مرئي، البث سيبدأ مباشرة...")
                else:
                    await status_msg.edit_text("🔐 تم العثور على زر Log in، جاري فتح نموذج Sign in...")
                    await login_btn.click()

                    # انتظار ظهور نموذج Sign in (حقل البريد)
                    await page.wait_for_selector(
                        'input[name="email"], input[type="email"]',
                        timeout=10000,
                        state="visible",
                    )

                    await status_msg.edit_text("🔐 جاري إدخال البريد وكلمة المرور...")

                    # ملء البريد
                    await page.fill(
                        'input[name="email"], input[type="email"]',
                        LOGIN_EMAIL,
                    )
                    # ملء كلمة المرور
                    await page.fill(
                        'input[name="password"], input[type="password"]',
                        LOGIN_PASSWORD,
                    )

                    # الضغط على Enter داخل حقل الباسورد (يُرسِّل النموذج)
                    await page.press(
                        'input[name="password"], input[type="password"]',
                        "Enter",
                    )

                    # انتظار اختفاء حقل البريد كدليل على اجتياز تسجيل الدخول
                    try:
                        await page.wait_for_selector(
                            'input[name="email"], input[type="email"]',
                            timeout=15000,
                            state="detached",
                        )
                        login_performed = True
                        await status_msg.edit_text("✅ تم تسجيل الدخول بنجاح! جاري بدء البث...")
                    except PlaywrightTimeout:
                        # fallback: ربما أصبح hidden بدلاً من detached
                        still_visible = await page.is_visible(
                            'input[name="email"], input[type="email"]'
                        )
                        if not still_visible:
                            login_performed = True
                            await status_msg.edit_text("✅ تم تسجيل الدخول بنجاح! جاري بدء البث...")
                        else:
                            raise RuntimeError(
                                "بقي نموذج تسجيل الدخول ظاهراً بعد الضغط على Enter"
                            )

            except PlaywrightTimeout:
                logger.warning("انتهى الوقت أثناء محاولة تسجيل الدخول.")
                await status_msg.edit_text(
                    "⚠️ انتهى الوقت أثناء تسجيل الدخول، البث سيبدأ بدونه..."
                )
            except Exception as e:
                logger.warning(f"خطأ أثناء تسجيل الدخول: {e}")
                await status_msg.edit_text(
                    f"⚠️ فشل تسجيل الدخول ({e})، سيبدأ البث على أي حال."
                )
        else:
            await status_msg.edit_text(
                "ℹ️ لم تُضف بيانات LOGIN_EMAIL/LOGIN_PASSWORD، البث بدون تسجيل دخول."
            )

        # ─── أول لقطة بعد تسجيل الدخول (أو بدونه) ───
        first_screenshot = await page.screenshot(type="jpeg", quality=80)

        sent_msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(BytesIO(first_screenshot), filename="live.jpg"),
            caption="📡 جاري البث المباشر...",
        )

        # ─── تشغيل حلقة التحديث ───
        task = asyncio.create_task(
            broadcast_loop(chat_id, context, page, sent_msg.message_id)
        )

        async with streams_lock:
            streams[chat_id] = {
                "playwright": pw,
                "browser": browser,
                "page": page,
                "message_id": sent_msg.message_id,
                "active": True,
                "task": task,
            }

        if not login_performed:
            await status_msg.edit_text(
                "✅ تم بدء البث! 🎥\nسأُحدّث نفس الرسالة كل 3 ثوانٍ."
            )

    except Exception as e:
        logger.exception("Error starting stream")
        try:
            await status_msg.edit_text(
                f"❌ فشل بدء البث:\n`{e}`", parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

        # تنظيف فوري إذا فشل البث قبل الاشتغال
        if page:
            await page.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with streams_lock:
        if chat_id not in streams:
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

    await cleanup_stream(chat_id)

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


# ─── حلقة التحديث ───
async def broadcast_loop(
    chat_id: int, context: ContextTypes.DEFAULT_TYPE, page, message_id: int
):
    try:
        while True:
            async with streams_lock:
                if chat_id not in streams or not streams[chat_id].get("active"):
                    break

            screenshot = await page.screenshot(type="jpeg", quality=80)

            media = InputMediaPhoto(
                media=InputFile(BytesIO(screenshot), filename="live.jpg"),
                caption="📡 لقطة مباشرة · مُحدّثة الآن",
            )

            try:
                await context.bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=media,
                )
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    pass
                else:
                    logger.warning(f"BadRequest in broadcast: {e}")
            except Exception as e:
                logger.warning(f"Broadcast edit error: {e}")

            await asyncio.sleep(3)

    except asyncio.CancelledError:
        logger.info(f"Stream cancelled for {chat_id}")
        raise
    except Exception as e:
        logger.error(f"Stream loop error: {e}")
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=f"⚠️ توقف البث بسبب خطأ: {e}"
            )
        except Exception:
            pass
    finally:
        await cleanup_stream(chat_id)


async def cleanup_stream(chat_id: int):
    async with streams_lock:
        data = streams.pop(chat_id, None)

    if not data:
        return

    try:
        if data.get("page"):
            await data["page"].close()
    except Exception as e:
        logger.debug(f"Page close error: {e}")

    try:
        if data.get("browser"):
            await data["browser"].close()
    except Exception as e:
        logger.debug(f"Browser close error: {e}")

    try:
        if data.get("playwright"):
            await data["playwright"].stop()
    except Exception as e:
        logger.debug(f"Playwright stop error: {e}")


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

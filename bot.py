import os
import asyncio
import logging
import io
from typing import Any, Dict

from telegram import (
    Update,
    InputMediaPhoto,
)
from telegram.ext import Application, CommandHandler, ContextTypes
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

# الفاصل الزمني للبث (تم وضعه كمتغير لتسهيل تغييره)
STREAM_INTERVAL = 3 

streams: Dict[int, Dict[str, Any]] = {}
streams_lock = None

async def post_init(application: Application) -> None:
    """تهيئة الـ Lock بعد بدء الـ Event Loop لتجنب تحذيرات Python 3.10+"""
    global streams_lock
    streams_lock = asyncio.Lock()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت البث المباشر (مُحسّن)\n\n"
        "📌 /stream — بدء بث شاشة الموقع\n"
        "📌 /stop  — إيقاف البث\n\n"
        "⚡️ البث يعمل بالذاكرة (RAM) لسرعة أكبر."
    )

async def stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with streams_lock:
        if chat_id in streams and streams[chat_id].get("active"):
            await update.message.reply_text("⚠️ البث يعمل بالفعل! أرسل /stop لإيقافه.")
            return

    await update.message.reply_text("⏳ جاري تهيئة المتصفح والبث...")

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
    pw = None
    message_id = None

    # ─── دالة مساعدة: لقطة + إرسال/تحديث (بدون حفظ على القراص) ───
    async def snap(caption: str, first: bool = False):
        nonlocal message_id
        try:
            screenshot_bytes = await page.screenshot(type="jpeg", quality=60)
            photo_stream = io.BytesIO(screenshot_bytes)
            photo_stream.name = "stream.jpg"

            if first or message_id is None:
                msg = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_stream,
                    caption=caption,
                )
                message_id = msg.message_id
                async with streams_lock:
                    if chat_id in streams:
                        streams[chat_id]["message_id"] = message_id
            else:
                # نحتاج لإنشاء BytesIO جديد لكل إرسال لأن المكتبة تقرأ الـ Stream وتغلقه
                edit_stream = io.BytesIO(screenshot_bytes)
                await context.bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=InputMediaPhoto(
                        media=edit_stream,
                        caption=caption,
                    ),
                )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass # لا مشكلة إذا لم تتغير الصورة
            else:
                logger.warning(f"BadRequest: {e}")
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
                "--single-process" # تقليل استهلاك الرام
            ]
        )

        page = await browser.new_page(viewport={"width": 960, "height": 540})
        
        # إخفاء خصائص الأتمتة لمنع الحظر من بعض المواقع
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false
            });
        """)

        # ═══════════════════════════════════════
        #  🎬 البث يبدأ فوراً من هنا
        # ═══════════════════════════════════════

        await snap("🌐 جاري فتح المتصفح...", first=True)
        
        # ملاحظة: استخدمنا domcontentloaded بدلاً من networkidle لأن مواقع الدردشة 
        # تستخدم WebSockets ولن تصل لحالة networkidle أبداً مما يسبب Timeout
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await snap("🌐 تم الوصول إلى الموقع")
        await asyncio.sleep(1.5)

        # إغلاق أي Popup ترحيبي
        for sel in [
            "button:has-text('Close')",
            "[aria-label='Close']",
            ".popup-close",
            "button.close",
        ]:
            try:
                locator = page.locator(sel).first
                if await locator.is_visible(timeout=1000):
                    await locator.click()
                    await page.wait_for_timeout(500)
                    await snap("🧹 تم إغلاق النافذة المنبثقة")
                    await asyncio.sleep(1.5)
            except Exception:
                pass

        # ═══════════════════════════════════════
        #  🔐 تسجيل الدخول خطوة بخطوة
        # ═══════════════════════════════════════
        if LOGIN_EMAIL and LOGIN_PASSWORD:
            await snap("🔐 البحث عن زر Log in...")

            login_locator = page.locator('text=Log in, button:has-text("Log in"), a:has-text("Log in")').first
            
            try:
                await login_locator.click(timeout=5000)
                await snap("🔐 تم الضغط على Log in")
                await asyncio.sleep(1.5)

                # ملء البريد
                email_input = page.locator('input[name="email"], input[type="email"]').first
                await email_input.wait_for(state="visible", timeout=10000)
                await snap("📝 جاري كتابة البريد...")
                await email_input.fill(LOGIN_EMAIL)
                await asyncio.sleep(0.5) # انتظار بسيط لظهور الكتابة في اللقطة

                # ملء كلمة المرور
                pass_input = page.locator('input[name="password"], input[type="password"]').first
                await snap("📝 جاري كتابة كلمة المرور...")
                await pass_input.fill(LOGIN_PASSWORD)
                await asyncio.sleep(0.5)

                # الضغط على Sign in
                await snap("🔑 جاري تسجيل الدخول...")
                sign_in_btn = page.locator('button:has-text("Sign in"), button[type="submit"]').first
                
                try:
                    await sign_in_btn.click(timeout=5000)
                    # ننتظر قليلاً بعد الضغط ليتفاعل السيرفر
                    await page.wait_for_timeout(3000)
                except Exception:
                    # في حال لم يجد الزر، نضغط Enter
                    await pass_input.press("Enter")
                    await page.wait_for_timeout(3000)

                # التحقق من نجاح الدخول
                email_still_visible = await page.locator('input[name="email"], input[type="email"]').is_visible()
                if email_still_visible:
                    await snap("⚠️ بقي نموذج الدخول ظاهراً (بيانات خاطئة؟)، البث مستمر...")
                else:
                    await snap("✅ تم تسجيل الدخول! البث المباشر يعمل الآن.")
                await asyncio.sleep(1.5)

            except PlaywrightTimeout:
                await snap("ℹ️ لم يُعثر على زر Log in أو حقول الدخول، البث مستمر...")
                await asyncio.sleep(1.5)
        else:
            await snap("ℹ️ لا توجد بيانات LOGIN_EMAIL/LOGIN_PASSWORD، البث بدون تسجيل دخول.")
            await asyncio.sleep(1.5)

        # ═══════════════════════════════════════
        #  📡 الحلقة المستمرة (بث حي)
        # ═══════════════════════════════════════
        while True:
            async with streams_lock:
                if chat_id not in streams or not streams[chat_id].get("active"):
                    break

            await snap("📡 لقطة مباشرة · مُحدّثة الآن")
            await asyncio.sleep(STREAM_INTERVAL)

    except asyncio.CancelledError:
        logger.info(f"Stream worker cancelled for {chat_id}")
        raise
    except Exception as e:
        logger.exception("Stream worker error")
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=f"⚠️ توقف البث بسبب خطأ: {e}"
            )
        except Exception:
            pass
    finally:
        # ─── تنظيف Playwright ───
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
                
        # إزالة من الذاكرة
        async with streams_lock:
            streams.pop(chat_id, None)


def main():
    if not TOKEN:
        raise RuntimeError("❌ متغير BOT_TOKEN غير موجود!")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

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

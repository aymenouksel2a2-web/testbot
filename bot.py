import os
import re
import asyncio
import logging
import hashlib
from io import BytesIO
from typing import Optional, Dict

from telegram import Update, InputFile, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import BadRequest, TelegramError
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── إعدادات البيئة ───
TOKEN = os.environ.get("BOT_TOKEN")
URL = os.environ.get("TARGET_URL", "https://gratisfy.xyz/chat")
PORT = int(os.environ.get("PORT", "8080"))
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
LOGIN_EMAIL = os.environ.get("LOGIN_EMAIL")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD")

if not TOKEN:
    raise RuntimeError("❌ متغير BOT_TOKEN غير موجود!")


# ═══════════════════════════════════════════
#  إدارة حالة البث (أكثر أماناً من Dict عشوائي)
# ═══════════════════════════════════════════
class StreamInfo:
    __slots__ = ("active", "task", "message_id", "last_hash", "page", "browser", "pw")

    def __init__(self):
        self.active: bool = True
        self.task: Optional[asyncio.Task] = None
        self.message_id: Optional[int] = None
        self.last_hash: Optional[str] = None
        # نحتفظ بمراجع Playwright لإغلاقها فوراً من /stop (Kill Switch)
        self.page: Optional[object] = None
        self.browser: Optional[object] = None
        self.pw: Optional[object] = None


streams: Dict[int, StreamInfo] = {}
streams_lock = asyncio.Lock()


# ─── دوال المساعدة ───
async def _kill_browser(info: StreamInfo) -> None:
    """إغلاق عناصر Playwright بشكل آمن (يمكن استدعاؤها من أي مكان)."""
    for attr in ("page", "browser", "pw"):
        obj = getattr(info, attr, None)
        if obj is not None:
            try:
                if attr == "page":
                    await obj.close()
                elif attr == "browser":
                    await obj.close()
                elif attr == "pw":
                    await obj.stop()
            except Exception:
                pass
            finally:
                setattr(info, attr, None)


# ─── Handlers ───
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
        existing = streams.get(chat_id)
        if existing and existing.active:
            await update.message.reply_text(
                "⚠️ البث يعمل بالفعل! أرسل /stop لإيقافه."
            )
            return

    await update.message.reply_text("⏳ جاري تهيئة البث...")

    info = StreamInfo()
    async with streams_lock:
        streams[chat_id] = info

    info.task = asyncio.create_task(
        stream_worker(chat_id, info, context),
        name=f"stream_{chat_id}",
    )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with streams_lock:
        info = streams.get(chat_id)
        if not info or not info.active:
            await update.message.reply_text("❌ لا يوجد بث نشط حالياً.")
            return
        info.active = False
        msg_id = info.message_id

    # ─── Kill Switch: نقتل المتصفح فوراً من هنا ───
    await _kill_browser(info)

    # ─── ننتظر انتهاء الـ Task (إذا لم تُلغَ بعد) ───
    if info.task and not info.task.done():
        info.task.cancel()
        try:
            await info.task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass  # Browser closed أو أي خطأ إغلاق آخر

    # ─── تحديث الرسالة الأخيرة ───
    if msg_id:
        try:
            await context.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption="⏹️ توقف البث المباشر.",
            )
        except Exception:
            pass

    async with streams_lock:
        streams.pop(chat_id, None)

    await update.message.reply_text("⏹️ تم إيقاف البث وتصفية المتصفح.")


# ═══════════════════════════════════════════
#  العامل الرئيسي (مُحسَّن وقوي ضد الأخطاء)
# ═══════════════════════════════════════════
async def stream_worker(chat_id: int, info: StreamInfo, context: ContextTypes.DEFAULT_TYPE):
    """
    التحسينات الرئيسية:
    • لا يوجد ملفات /tmp (BytesIO مباشرة).
    • hash للصورة: إذا لم تتغير لا نُرسل تحديثاً (يقلل RateLimit).
    • screenshot مع timeout (لا ينهار إذا علّق الموقع).
    • kill-browser من /stop يوقف العملية حتى لو كانت في screenshot.
    • تسجيل الدخول باستخدام Locators (API الحديث في Playwright).
    """

    # ─── دالة snap محسّنة ───
    async def snap(caption: str, *, force: bool = False) -> bool:
        """يلتقط صورة ويرسل/يُحدّث. يرجع True إذا نجح الإرسال."""
        if not info.page or info.page.is_closed():
            return False

        # 1) لقطة الشاشة (مع timeout حتى لا تتعلّق للأبد)
        try:
            raw_bytes = await asyncio.wait_for(
                info.page.screenshot(type="jpeg", quality=80),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Screenshot timeout")
            return False
        except Exception as e:
            logger.warning(f"Screenshot error: {e}")
            return False

        # 2) تجنب إرسال صورة مطابقة للسابقة (توفير Rate Limit)
        current_hash = hashlib.md5(raw_bytes).hexdigest()
        if not force and info.last_hash == current_hash:
            return False
        info.last_hash = current_hash

        buf = BytesIO(raw_bytes)

        # 3) إرسال أو تحديث
        try:
            if info.message_id is None:
                msg = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(buf, filename="stream.jpg"),
                    caption=caption,
                )
                info.message_id = msg.message_id
            else:
                try:
                    await context.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=info.message_id,
                        media=InputMediaPhoto(
                            media=InputFile(buf, filename="stream.jpg"),
                            caption=caption,
                        ),
                    )
                except BadRequest as e:
                    err = str(e).lower()
                    # إذا حُذفت الرسالة القديمة، نعيد الإرسال
                    if "message to edit not found" in err or "wrong file identifier" in err:
                        logger.info("Message lost, resending...")
                        info.message_id = None
                        msg = await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=InputFile(buf, filename="stream.jpg"),
                            caption=caption,
                        )
                        info.message_id = msg.message_id
                    elif "message is not modified" in err:
                        pass  # طبيعي
                    else:
                        raise  # BadRequest غير متوقع
            return True

        except BadRequest as e:
            if "message is not modified" in str(e):
                return True
            logger.warning(f"BadRequest suppressed: {e}")
            return False
        except TelegramError as e:
            logger.warning(f"Telegram error: {e}")
            return False

    # ═══════════════════════════════════════
    #  بدء Playwright
    # ═══════════════════════════════════════
    try:
        info.pw = await async_playwright().start()

        info.browser = await info.pw.chromium.launch(
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

        info.page = await info.browser.new_page(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        # 1) لقطة أولية سريعة
        await snap("🌐 جاري فتح المتصفح...", force=True)
        await asyncio.sleep(3)

        # 2) فتح الموقع
        try:
            await info.page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await info.page.wait_for_timeout(1500)  # مهلة إضافية للـ SPA
            await snap("🌐 تم الوصول إلى الموقع")
        except PlaywrightTimeout:
            await snap("⛔️ الموقع لا يستجيب...")
            raise
        await asyncio.sleep(3)

        # 3) إغلاق النوافذ المنبثقة الشائعة
        for btn_text in ("Close", "Accept", "Got it", "No thanks"):
            try:
                btn = info.page.get_by_role("button", name=re.compile(btn_text, re.I)).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await info.page.wait_for_timeout(500)
                    await snap("🧹 تم إغلاق نافذة منبثقة")
                    await asyncio.sleep(2)
            except Exception:
                pass

        # ═══════════════════════════════════════
        #  🔐 تسجيل الدخول (خطوة بخطوة)
        # ═══════════════════════════════════════
        if LOGIN_EMAIL and LOGIN_PASSWORD:
            await snap("🔐 البحث عن زر Log in...")

            login_btn = None
            try:
                login_btn = info.page.get_by_role("button", name=re.compile("Log in", re.I)).first
                if not await login_btn.is_visible(timeout=3000):
                    login_btn = None
            except Exception:
                login_btn = None

            if login_btn is None:
                try:
                    login_btn = info.page.locator(
                        'a:has-text("Log in"), [data-testid="login-button"], button:has-text("Log in")'
                    ).first
                    if not await login_btn.is_visible(timeout=3000):
                        login_btn = None
                except Exception:
                    login_btn = None

            if login_btn:
                await login_btn.click()
                await snap("🔐 تم الضغط على Log in")
                await asyncio.sleep(2)

                # ─── ملء البيانات باستخدام Locators (أسرع وأدق) ───
                email_in = info.page.locator('input[type="email"], input[name="email"]').first
                pass_in = info.page.locator('input[type="password"], input[name="password"]').first

                await email_in.wait_for(state="visible", timeout=10000)

                await snap("📝 جاري كتابة البريد...")
                await email_in.fill(LOGIN_EMAIL)

                await snap("📝 جاري كتابة كلمة المرور...")
                await pass_in.fill(LOGIN_PASSWORD)
                await asyncio.sleep(1)

                await snap("🔑 جاري الضغط على Sign in...")
                submit = info.page.locator(
                    'button:has-text("Sign in"), button[type="submit"]'
                ).first

                if await submit.is_visible(timeout=3000):
                    try:
                        async with info.page.expect_navigation(
                            wait_until="domcontentloaded", timeout=15000
                        ):
                            await submit.click()
                    except PlaywrightTimeout:
                        # ربما الموقع SPA
                        await submit.click()
                        await info.page.wait_for_timeout(3000)
                else:
                    await info.page.press('input[type="password"]', "Enter")
                    await info.page.wait_for_timeout(3000)

                # التحقق
                if await email_in.is_visible(timeout=3000):
                    await snap("⚠️ بقي نموذج الدخول ظاهراً (بيانات خاطئة؟)، البث مستمر...")
                else:
                    await snap("✅ تم تسجيل الدخول! البث المباشر يعمل الآن.")
                await asyncio.sleep(3)
            else:
                await snap("ℹ️ لم يُعثر على زر Log in، البث مستمر...")
                await asyncio.sleep(3)
        else:
            await snap("ℹ️ لا توجد بيانات تسجيل دخول، البث بدونها.")
            await asyncio.sleep(3)

        # ═══════════════════════════════════════
        #  📡 الحلقة المستمرة (بث حي)
        # ═══════════════════════════════════════
        loop_tick = 0
        while True:
            async with streams_lock:
                if not info.active:
                    break

            # كل 10 لقطات نرسل "force" لتجنب مشاكل Telegram caching
            loop_tick += 1
            force_tick = loop_tick % 10 == 1

            await snap("📡 لقطة مباشرة · مُحدّثة الآن", force=force_tick)
            await asyncio.sleep(3)

    except asyncio.CancelledError:
        logger.info(f"Stream worker cancelled for {chat_id}")
        raise
    except Exception as e:
        logger.exception("Stream worker error")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ توقف البث بسبب خطأ:\n<code>{e}</code>",
            )
        except Exception:
            pass
    finally:
        # ─── تنظيف مضمون ───
        await _kill_browser(info)
        async with streams_lock:
            if chat_id in streams and streams[chat_id] is info:
                streams.pop(chat_id, None)


# ─── معالج الأخطاء العام ───
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "😕 حدث خطأ داخلي. حاول مجدداً لاحقاً."
            )
        except Exception:
            pass


# ─── التشغيل ───
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stream", stream))
    app.add_handler(CommandHandler("stop", stop))
    app.add_error_handler(error_handler)

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

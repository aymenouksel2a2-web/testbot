import os
import asyncio
import logging
import io
import re
from typing import Any, Dict

from telegram import (
    Update,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
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

# ─── اسم النموذج المطلوب (افتراضي: Grok Uncensored) ───
TARGET_MODEL = os.environ.get("TARGET_MODEL", "Grok Uncensored")

# الفاصل الزمني للبث (ثوانٍ)
STREAM_INTERVAL = 3

streams: Dict[int, Dict[str, Any]] = {}
streams_lock = None

sessions: Dict[int, Dict[str, Any]] = {}
sessions_lock = None


async def post_init(application: Application) -> None:
    """تهيئة الـ Lock بعد بدء الـ Event Loop لتجنب تحذيرات Python 3.10+"""
    global streams_lock, sessions_lock
    streams_lock = asyncio.Lock()
    sessions_lock = asyncio.Lock()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت Gratisfy (مُحسّن)\n\n"
        "📌 /ask — سؤال جديد (يفتح المتصفح وينتظر الـ prompt)\n"
        "📌 /stream — بدء بث شاشة الموقع\n"
        "📌 /stop  — إيقاف البث\n"
        "📌 /cancel — إلغاء جلسة /ask الحالية\n\n"
        "⚡️ عند إرسال /ask انتظر حتى يقول (أرسل الـ prompt) ثم اكتب سؤالك."
    )


# ═══════════════════════════════════════════════════════════════
#  📡 أوامر البث المباشر (كما هي)
# ═══════════════════════════════════════════════════════════════

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
    page = None
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
                pass
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
                "--single-process",
            ]
        )

        page = await browser.new_page(viewport={"width": 960, "height": 540})

        # إخفاء خصائص الأتمتة
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false
            });
        """)

        # ═══════════════════════════════════════
        #  🎬 البث يبدأ فوراً من هنا
        # ═══════════════════════════════════════

        await snap("🌐 جاري فتح المتصفح...", first=True)

        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await snap("🌐 تم الوصول إلى الموقع")
        await asyncio.sleep(1.5)

        # إغلاق أي Popup ترحيبي
        await page.wait_for_timeout(1500)
        for sel in [
            "button:has-text('Close')",
            "[aria-label='Close']",
            ".popup-close",
            "button.close",
            "[data-testid='close-button']",
        ]:
            try:
                locator = page.locator(sel).first
                if await locator.is_visible(timeout=1500):
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

            login_btn = None
            for sel in [
                'button:has-text("Log in")',
                'a:has-text("Log in")',
                '[data-testid="login-button"]',
            ]:
                try:
                    tmp = page.locator(sel).first
                    await tmp.wait_for(state="visible", timeout=3000)
                    login_btn = tmp
                    break
                except Exception:
                    continue

            if not login_btn:
                try:
                    login_btn = page.get_by_text("Log in", exact=False).first
                    await login_btn.wait_for(state="visible", timeout=3000)
                except Exception:
                    login_btn = None

            if login_btn:
                try:
                    await login_btn.click()
                    await snap("🔐 تم الضغط على Log in · انتظار نموذج الدخول...")
                    await asyncio.sleep(2.5)
                    await page.wait_for_timeout(1000)

                    # ─── ملء البريد ───
                    email_input = None
                    for sel in [
                        'input[name="email"]',
                        'input[type="email"]',
                        'input[placeholder*="email" i]',
                        'input[id="email"]',
                    ]:
                        try:
                            tmp = page.locator(sel).first
                            await tmp.wait_for(state="visible", timeout=8000)
                            email_input = tmp
                            break
                        except Exception:
                            continue

                    if not email_input:
                        raise Exception("لم يُعثر على حقل البريد")

                    await snap("📝 جاري كتابة البريد...")
                    await email_input.fill(LOGIN_EMAIL)
                    await asyncio.sleep(0.5)

                    # ─── ملء كلمة المرور ───
                    pass_input = None
                    for sel in [
                        'input[name="password"]',
                        'input[type="password"]',
                        'input[placeholder*="password" i]',
                        'input[id="password"]',
                    ]:
                        try:
                            tmp = page.locator(sel).first
                            await tmp.wait_for(state="visible", timeout=8000)
                            pass_input = tmp
                            break
                        except Exception:
                            continue

                    if not pass_input:
                        raise Exception("لم يُعثر على حقل كلمة المرور")

                    await snap("📝 جاري كتابة كلمة المرور...")
                    await pass_input.fill(LOGIN_PASSWORD)
                    await asyncio.sleep(0.5)

                    # ─── إرسال النموذج ───
                    await snap("🔑 جاري إرسال تسجيل الدخول...")
                    await pass_input.press("Enter")
                    await asyncio.sleep(1.0)
                    await page.wait_for_timeout(3000)

                    # Fallback: النقر على أي زر Sign in/Submit إن وجد
                    try:
                        submit_btn = page.locator('button[type="submit"]').last
                        await submit_btn.wait_for(state="visible", timeout=3000)
                        await submit_btn.click(timeout=5000)
                        await page.wait_for_timeout(2000)
                    except Exception:
                        pass

                    try:
                        sign_in_btn = page.locator('button:has-text("Sign in")').last
                        if await sign_in_btn.is_visible(timeout=2000):
                            await sign_in_btn.click(timeout=5000)
                            await page.wait_for_timeout(2000)
                    except Exception:
                        pass

                    await snap("⏳ التحقق من نجاح تسجيل الدخول...")
                    await page.wait_for_timeout(3000)

                    # التحقق من نجاح الدخول (هل بقي زر Log in في الهيدر؟)
                    login_still_visible = False
                    try:
                        hdr_login = page.locator('header').locator('button:has-text("Log in"), a:has-text("Log in")').first
                        login_still_visible = await hdr_login.is_visible(timeout=3000)
                    except Exception:
                        login_still_visible = False

                    if login_still_visible:
                        await snap("⚠️ بقي زر Log in ظاهراً (بيانات خاطئة؟)، البث مستمر...")
                    else:
                        await snap("✅ تم تسجيل الدخول!")
                    await asyncio.sleep(1.5)

                    # ═══════════════════════════════════════════════════
                    #  🔄 الانتقال إلى /chat بعد تسجيل الدخول
                    # ═══════════════════════════════════════════════════
                    await snap("💬 جاري الانتقال إلى صفحة الدردشة...")
                    await page.goto("https://gratisfy.xyz/chat", wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(2)
                    await snap("💬 تم الانتقال إلى صفحة الدردشة")

                except PlaywrightTimeout:
                    await snap("ℹ️ انتهى الوقت أثناء التفاعل مع نموذج الدخول، البث مستمر...")
                except Exception as login_err:
                    logger.warning(f"Login flow error: {login_err}")
                    await snap(f"⚠️ خطأ أثناء تسجيل الدخول: {login_err} · البث مستمر...")
            else:
                await snap("ℹ️ لم يُعثر على زر Log in، البث مستمر...")
        else:
            await snap("ℹ️ لا توجد بيانات LOGIN_EMAIL/LOGIN_PASSWORD، البث بدون تسجيل دخول.")

        # ═══════════════════════════════════════════════════════════════
        #  🤖 تغيير نموذج المحادثة (مثال: Grok Uncensored)
        # ═══════════════════════════════════════════════════════════════
        if TARGET_MODEL:
            await snap(f"🔽 جاري فتح قائمة النماذج لاختيار {TARGET_MODEL}...")
            await asyncio.sleep(1.5)

            # 1) الضغط على محث اختيار النموذج
            model_trigger = None
            for sel in [
                '[data-testid="model-selector"]',
                'button[class*="model-selector"]',
                'button[class*="select-model"]',
                '[aria-haspopup="listbox"]',
                'button:has([class*="chevron"])',
            ]:
                try:
                    tmp = page.locator(sel).first
                    await tmp.wait_for(state="visible", timeout=5000)
                    model_trigger = tmp
                    break
                except Exception:
                    continue

            if not model_trigger:
                try:
                    tmp = page.locator('button', has=re.compile(r"Ling|GPT|Grok|model", re.IGNORECASE)).first
                    await tmp.wait_for(state="visible", timeout=3000)
                    model_trigger = tmp
                except Exception:
                    model_trigger = None

            if model_trigger:
                try:
                    await model_trigger.click()
                    await snap("🔽 تم فتح قائمة النماذج")
                    await asyncio.sleep(1.5)
                    await page.wait_for_timeout(1000)

                    # 2) ملء حقل البحث داخل القائمة
                    search_input = None
                    for sel in [
                        'input[placeholder*="Search" i]',
                        'input[placeholder*="search" i]',
                        'input[placeholder*="model" i]',
                        'input[type="text"]',
                    ]:
                        try:
                            tmp = page.locator(sel).first
                            await tmp.wait_for(state="visible", timeout=5000)
                            search_input = tmp
                            break
                        except Exception:
                            continue

                    if search_input:
                        await search_input.fill(TARGET_MODEL)
                        await asyncio.sleep(1.0)
                        await snap(f"🔍 تم البحث عن {TARGET_MODEL}")

                        # 3) اختيار النتيجة
                        result_locator = None
                        for sel in [
                            f'text={TARGET_MODEL}',
                            f'div:has-text("{TARGET_MODEL}")',
                            f'li:has-text("{TARGET_MODEL}")',
                            '[data-testid="model-option"]',
                            '[role="option"]',
                        ]:
                            try:
                                tmp = page.locator(sel).first
                                await tmp.wait_for(state="visible", timeout=5000)
                                result_locator = tmp
                                break
                            except Exception:
                                continue

                        if result_locator:
                            await result_locator.click()
                            await snap(f"✅ تم اختيار {TARGET_MODEL}")
                        else:
                            await search_input.press("Enter")
                            await snap(f"⌨️ تم اختيار {TARGET_MODEL} بـ Enter")

                        await asyncio.sleep(1.5)
                        await page.wait_for_timeout(1500)
                    else:
                        await snap("⚠️ لم يُعثر على حقل البحث في قائمة النماذج")
                except Exception as model_err:
                    logger.warning(f"Model selection error: {model_err}")
                    await snap(f"⚠️ خطأ أثناء اختيار النموذج: {model_err}")
            else:
                await snap("ℹ️ لم يُعثر على قائمة اختيار النماذج")

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


# ═══════════════════════════════════════════════════════════════
#  ✍️ /ask : سؤال جديد (Prompt -> Result)
# ═══════════════════════════════════════════════════════════════

async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with sessions_lock:
        if chat_id in sessions and sessions[chat_id].get("active"):
            await update.message.reply_text(
                "⚠️ هناك جلسة نشطة بالفعل! أرسل /cancel لإلغائها أولاً."
            )
            return

    await update.message.reply_text("⏳ جاري فتح المتصفح وتسجيل الدخول...")

    task = asyncio.create_task(ask_worker(chat_id, context))
    async with sessions_lock:
        sessions[chat_id] = {
            "active": True,
            "task": task,
        }


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with sessions_lock:
        if chat_id not in sessions or not sessions[chat_id].get("active"):
            await update.message.reply_text("❌ لا توجد جلسة /ask نشطة.")
            return
        info = sessions[chat_id]
        info["active"] = False
        ev = info.get("prompt_event")
        task = info.get("task")

    if ev:
        try:
            ev.set()
        except Exception:
            pass

    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await update.message.reply_text("❌ تم إلغاء الجلسة وإغلاق المتصفح.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل الـ prompt من المستخدم عندما تكون الجلسة في وضع الانتظار"""
    chat_id = update.effective_chat.id
    text = update.message.text

    async with sessions_lock:
        if chat_id not in sessions:
            return
        session = sessions[chat_id]
        if session.get("status") != "waiting_prompt":
            return

        session["prompt_text_container"]["text"] = text
        session["prompt_event"].set()
        session["status"] = "generating"

    await update.message.reply_text("✅ تم استلام الـ prompt! جاري المعالجة...")


async def ask_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    browser = None
    pw = None
    page = None

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
                "--single-process",
            ]
        )

        page = await browser.new_page(viewport={"width": 960, "height": 540})

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)

        # ═══════════════════════════════════════
        # 1) فتح الموقع
        # ═══════════════════════════════════════
        await context.bot.send_message(chat_id=chat_id, text="🌐 فتح الموقع...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.5)

        # إغلاق أي Popup
        await page.wait_for_timeout(1500)
        for sel in [
            "button:has-text('Close')",
            "[aria-label='Close']",
            ".popup-close",
            "button.close",
            "[data-testid='close-button']",
            "button:has-text('NOT NOW')",
            "button:has-text('Not now')",
        ]:
            try:
                locator = page.locator(sel).first
                if await locator.is_visible(timeout=1500):
                    await locator.click()
                    await page.wait_for_timeout(500)
            except Exception:
                pass

        # ═══════════════════════════════════════
        # 2) تسجيل الدخول
        # ═══════════════════════════════════════
        if LOGIN_EMAIL and LOGIN_PASSWORD:
            await context.bot.send_message(chat_id=chat_id, text="🔐 تسجيل الدخول...")

            login_btn = None
            for sel in [
                'button:has-text("Log in")',
                'a:has-text("Log in")',
                '[data-testid="login-button"]',
            ]:
                try:
                    tmp = page.locator(sel).first
                    await tmp.wait_for(state="visible", timeout=3000)
                    login_btn = tmp
                    break
                except Exception:
                    continue

            if not login_btn:
                try:
                    login_btn = page.get_by_text("Log in", exact=False).first
                    await login_btn.wait_for(state="visible", timeout=3000)
                except Exception:
                    login_btn = None

            if login_btn:
                try:
                    await login_btn.click()
                    await asyncio.sleep(2.5)
                    await page.wait_for_timeout(1000)

                    # ─── إدخال البريد ───
                    email_input = None
                    for sel in [
                        'input[name="email"]',
                        'input[type="email"]',
                        'input[placeholder*="email" i]',
                        'input[id="email"]',
                    ]:
                        try:
                            tmp = page.locator(sel).first
                            await tmp.wait_for(state="visible", timeout=8000)
                            email_input = tmp
                            break
                        except Exception:
                            continue

                    if not email_input:
                        raise Exception("لم يُعثر على حقل البريد")

                    await email_input.fill(LOGIN_EMAIL)
                    await asyncio.sleep(0.5)

                    # ─── إدخال كلمة المرور ───
                    pass_input = None
                    for sel in [
                        'input[name="password"]',
                        'input[type="password"]',
                        'input[placeholder*="password" i]',
                        'input[id="password"]',
                    ]:
                        try:
                            tmp = page.locator(sel).first
                            await tmp.wait_for(state="visible", timeout=8000)
                            pass_input = tmp
                            break
                        except Exception:
                            continue

                    if not pass_input:
                        raise Exception("لم يُعثر على حقل كلمة المرور")

                    await pass_input.fill(LOGIN_PASSWORD)
                    await asyncio.sleep(0.5)

                    await pass_input.press("Enter")
                    await asyncio.sleep(1.0)
                    await page.wait_for_timeout(3000)

                    # Fallback submit
                    try:
                        submit_btn = page.locator('button[type="submit"]').last
                        await submit_btn.wait_for(state="visible", timeout=3000)
                        await submit_btn.click(timeout=5000)
                        await page.wait_for_timeout(2000)
                    except Exception:
                        pass
                    try:
                        sign_in_btn = page.locator('button:has-text("Sign in")').last
                        if await sign_in_btn.is_visible(timeout=2000):
                            await sign_in_btn.click(timeout=5000)
                            await page.wait_for_timeout(2000)
                    except Exception:
                        pass

                    await page.wait_for_timeout(3000)

                    # انتقال لـ /chat
                    await page.goto("https://gratisfy.xyz/chat", wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(2)

                except Exception as login_err:
                    logger.warning(f"Login flow error: {login_err}")
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ في الدخول: {login_err}")
                    return
            else:
                await context.bot.send_message(chat_id=chat_id, text="ℹ️ لم يُعثر على زر Log in")
                return

        # ═══════════════════════════════════════
        # 3) اختيار النموذج
        # ═══════════════════════════════════════
        if TARGET_MODEL:
            await context.bot.send_message(chat_id=chat_id, text=f"🔽 اختيار النموذج: {TARGET_MODEL}...")
            await asyncio.sleep(1.5)

            model_trigger = None
            for sel in [
                '[data-testid="model-selector"]',
                'button[class*="model-selector"]',
                'button[class*="select-model"]',
                '[aria-haspopup="listbox"]',
                'button:has([class*="chevron"])',
            ]:
                try:
                    tmp = page.locator(sel).first
                    await tmp.wait_for(state="visible", timeout=5000)
                    model_trigger = tmp
                    break
                except Exception:
                    continue

            if not model_trigger:
                try:
                    tmp = page.locator('button', has=re.compile(r"Ling|GPT|Grok|model", re.IGNORECASE)).first
                    await tmp.wait_for(state="visible", timeout=3000)
                    model_trigger = tmp
                except Exception:
                    model_trigger = None

            if model_trigger:
                try:
                    await model_trigger.click()
                    await asyncio.sleep(1.5)
                    await page.wait_for_timeout(1000)

                    search_input = None
                    for sel in [
                        'input[placeholder*="Search" i]',
                        'input[placeholder*="search" i]',
                        'input[placeholder*="model" i]',
                        'input[type="text"]',
                    ]:
                        try:
                            tmp = page.locator(sel).first
                            await tmp.wait_for(state="visible", timeout=5000)
                            search_input = tmp
                            break
                        except Exception:
                            continue

                    if search_input:
                        await search_input.fill(TARGET_MODEL)
                        await asyncio.sleep(1.0)

                        result_locator = None
                        for sel in [
                            f'text={TARGET_MODEL}',
                            f'div:has-text("{TARGET_MODEL}")',
                            f'li:has-text("{TARGET_MODEL}")',
                            '[data-testid="model-option"]',
                            '[role="option"]',
                        ]:
                            try:
                                tmp = page.locator(sel).first
                                await tmp.wait_for(state="visible", timeout=5000)
                                result_locator = tmp
                                break
                            except Exception:
                                continue

                        if result_locator:
                            await result_locator.click()
                        else:
                            await search_input.press("Enter")

                        await asyncio.sleep(1.5)
                        await page.wait_for_timeout(1500)
                except Exception as model_err:
                    logger.warning(f"Model selection error: {model_err}")
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ في اختيار النموذج: {model_err}")

        # ═══════════════════════════════════════
        # 4) انتظار الـ prompt من المستخدم
        # ═══════════════════════════════════════
        prompt_event = asyncio.Event()
        prompt_text_container = {"text": None}

        async with sessions_lock:
            if chat_id not in sessions or not sessions[chat_id].get("active"):
                return
            sessions[chat_id]["status"] = "waiting_prompt"
            sessions[chat_id]["prompt_event"] = prompt_event
            sessions[chat_id]["prompt_text_container"] = prompt_text_container

        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ تم تسجيل الدخول واختيار النموذج.\n\n✍️ <b>أرسل الـ prompt الآن</b> (اكتب سؤالك في محادثة Telegram وسأرسله للموقع)",
            parse_mode="HTML",
        )

        # ننتظر المستخدم
        await prompt_event.wait()

        # تحقق من الإلغاء
        async with sessions_lock:
            if chat_id not in sessions or not sessions[chat_id].get("active"):
                return
            sessions[chat_id]["status"] = "generating"

        prompt_text = prompt_text_container["text"]
        if not prompt_text:
            await context.bot.send_message(chat_id=chat_id, text="❌ لم يُستلم نص صالح.")
            return

        await context.bot.send_message(chat_id=chat_id, text="⏳ جاري إرسال الـ prompt إلى الموقع...")

        # ═══════════════════════════════════════
        # 5) إرسال الـ prompt للموقع
        # ═══════════════════════════════════════
        textarea = None
        for sel in [
            'textarea[placeholder*="Message" i]',
            'textarea[placeholder*="message" i]',
            'textarea[placeholder*="Grok" i]',
            'textarea',
            'div[contenteditable="true"]',
            'input[placeholder*="Message" i]',
        ]:
            try:
                tmp = page.locator(sel).first
                await tmp.wait_for(state="visible", timeout=5000)
                textarea = tmp
                break
            except Exception:
                continue

        if not textarea:
            raise Exception("لم يُعثر على حقل كتابة الرسالة في الموقع")

        await textarea.fill(prompt_text)
        await asyncio.sleep(0.5)
        await textarea.press("Enter")

        await context.bot.send_message(chat_id=chat_id, text="⏳ تم الإرسال! جاري انتظار رد النموذج (قد يستغرق 10-60 ثانية)...")

        # ═══════════════════════════════════════
        # 6) انتظار الرد وقراءته
        # ═══════════════════════════════════════
        response_text = ""
        start_time = asyncio.get_event_loop().time()
        last_text = ""
        stable_count = 0

        while (asyncio.get_event_loop().time() - start_time) < 120:  # انتظر حتى دقيقتين
            current_text = ""
            try:
                for sel in [
                    '.message-item.bot-message:last-child .message-content',
                    '.message.bot:last-child .content',
                    '[data-testid="assistant-message"]',
                    '[data-testid="assistant-message"] .markdown-body',
                    '.markdown-body:last-child',
                    '.prose:last-child',
                    'div[class*="message"]:nth-last-child(1) div[class*="content"]',
                    'div[class*="chat"]:nth-last-child(1) div[class*="text"]',
                ]:
                    try:
                        el = page.locator(sel).last
                        txt = await el.inner_text(timeout=3000)
                        if txt and txt.strip() and txt.strip() != prompt_text.strip():
                            current_text = txt.strip()
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            if current_text:
                # إذا لم يتغير النص لمدة 4 ثوانٍ متتالية، نعتبر أن الرد اكتمل
                if current_text == last_text:
                    stable_count += 1
                    if stable_count >= 2:  # 2 * sleep(2) = ~4 ثوانٍ ثابتة
                        response_text = current_text
                        break
                else:
                    stable_count = 0
                    last_text = current_text

            await asyncio.sleep(2)

        # ═══════════════════════════════════════
        # 7) إرسال النتيجة للمستخدم
        # ═══════════════════════════════════════
        if response_text:
            max_len = 4000
            if len(response_text) <= max_len:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🤖 <b>النتيجة:</b>\n\n{response_text}",
                    parse_mode="HTML",
                )
            else:
                parts = [response_text[i:i + max_len] for i in range(0, len(response_text), max_len)]
                for idx, part in enumerate(parts):
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"🤖 <b>النتيجة (جزء {idx + 1}/{len(parts)}):</b>\n\n{part}",
                        parse_mode="HTML",
                    )
        else:
            # لم نستطع قراءة النص، نرسل صورة
            screenshot_bytes = await page.screenshot(type="jpeg", quality=70)
            photo_stream = io.BytesIO(screenshot_bytes)
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo_stream,
                caption="📸 لم أستطع استخراج النص تلقائياً، إليك لقطة شاشة للرد.",
            )

        await context.bot.send_message(chat_id=chat_id, text="✅ انتهت العملية. أرسل /ask لسؤال جديد.")

    except asyncio.CancelledError:
        logger.info(f"Ask worker cancelled for {chat_id}")
        raise
    except Exception as e:
        logger.exception("Ask worker error")
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء العملية: {e}")
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

        async with sessions_lock:
            sessions.pop(chat_id, None)


def main():
    if not TOKEN:
        raise RuntimeError("❌ متغير BOT_TOKEN غير موجود!")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stream", stream))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(CommandHandler("cancel", cancel))

    # استقبال الرسائل النصية (للـ prompt)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

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

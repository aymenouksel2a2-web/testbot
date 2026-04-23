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


async def post_init(application: Application) -> None:
    """تهيئة الـ Lock بعد بدء الـ Event Loop لتجنب تحذيرات Python 3.10+"""
    global streams_lock
    streams_lock = asyncio.Lock()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت Gratisfy — وضع البث المباشر + دردشة نصية\n\n"
        "📌 /stream — بدء جلسة جديدة (يفتح المتصفح ويبث الشاشة)\n"
        "📌 /stop  — إيقاف الجلسة وإغلاق المتصفح\n\n"
        "⚡️ بمجرد بدء البث، أرسل أي رسالة وسأرد عليك بالنص مباشرة."
    )


# ═══════════════════════════════════════════════════════════════
#  📡 أوامر البث المباشر (صورة تُحدّث كل 3 ثواني)
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
            "page": None,
            "lock": asyncio.Lock(),
            "message_id": None,
        }


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with streams_lock:
        if chat_id not in streams or not streams[chat_id].get("active"):
            await update.message.reply_text("❌ لا يوجد بث نشط حالياً.")
            return
        info = streams[chat_id]
        info["active"] = False
        task = info.get("task")

    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await update.message.reply_text("⏹️ تم إيقاف البث وتصفية المتصفح.")


async def stream_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    browser = None
    pw = None
    page = None
    message_id = None

    # ─── دالة مساعدة: لقطة + إرسال/تحديث (بدون حفظ على القرص) ───
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

        # ═══════════════════════════════════════════════════
        #  ✅ تخزين page للدردشة النصية
        # ═══════════════════════════════════════════════════
        async with streams_lock:
            if chat_id in streams and streams[chat_id].get("active"):
                streams[chat_id]["page"] = page

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
#  ✉️ استقبال الرسائل النصية وإرسالها للموقع أثناء البث
# ═══════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل الرسائل النصية أثناء وجود بث نشط ويرد بالنص"""
    chat_id = update.effective_chat.id
    text = update.message.text

    async with streams_lock:
        if chat_id not in streams or not streams[chat_id].get("active"):
            return
        session = streams[chat_id]
        page = session.get("page")
        lock = session.get("lock")
        if not page:
            await update.message.reply_text("⏳ المتصفح لم يجهز بعد، انتظر قليلاً...")
            return

    async with lock:
        try:
            await update.message.reply_text("⏳ جاري إرسال السؤال...")

            # ─── إرسال الـ prompt للموقع ───
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

            await textarea.fill(text)
            await asyncio.sleep(0.3)
            await textarea.press("Enter")

            await update.message.reply_text("⏳ تم الإرسال! جاري انتظار رد النموذج (قد يستغرق 10-60 ثانية)...")

            # ─── انتظار ظهور الرد وقراءته عبر JavaScript ───
            await asyncio.sleep(2)  # مهلة أولية للبدء

            response_text = ""
            start_time = asyncio.get_event_loop().time()
            last_text = ""
            stable_count = 0

            while (asyncio.get_event_loop().time() - start_time) < 120:
                current_text = ""

                try:
                    # استخدام JavaScript للبحث في DOM بشكل أعمق وأقوى
                    result = await page.evaluate(
                        """([userText]) => {
                            const selectors = [
                                '.message-item.bot-message:last-child .message-content',
                                '.message.bot:last-child .content',
                                '[data-testid="assistant-message"]',
                                '[data-testid="assistant-message"] .markdown-body',
                                '.markdown-body:last-child',
                                '.prose:last-child',
                                'div[class*="message-content"]',
                                'div[class*="prose"]',
                                'div[class*="markdown-body"]',
                                '.flex-col-reverse div[class*="group"] .whitespace-pre-wrap',
                                'div[class*="flex-col"] div[class*="rounded-2xl"] div[class*="text-sm"]',
                                'div[class*="chat"] div[class*="response"]',
                                'div[class*="bot"] div[class*="message"]',
                                'div[class*="assistant"] div[class*="message"]',
                                'div[class*="bubble"]:last-child',
                                'article',
                                'div[role="log"] > div:last-child'
                            ];
                            for (const sel of selectors) {
                                const els = document.querySelectorAll(sel);
                                if (els.length > 0) {
                                    const last = els[els.length - 1];
                                    const txt = (last.innerText || last.textContent || '').trim();
                                    if (txt && txt.length > 0 && txt !== userText.trim()) {
                                        return txt;
                                    }
                                }
                            }
                            // fallback: أي عنصر يحتوي على نص في الجزء السفلي من الشاشة
                            const allDivs = document.querySelectorAll('div');
                            for (let i = allDivs.length - 1; i >= Math.max(0, allDivs.length - 20); i--) {
                                const txt = (allDivs[i].innerText || '').trim();
                                if (txt.length > 2 && txt !== userText.trim() && allDivs[i].children.length <= 5) {
                                    return txt;
                                }
                            }
                            return '';
                        }""",
                        [text],
                    )
                    current_text = (result or "").strip()
                except Exception as eval_err:
                    logger.warning(f"JS evaluate error: {eval_err}")
                    current_text = ""

                if current_text:
                    # إذا لم يتغير النص لمدة 6 ثوانٍ متتالية، نعتبر أن الرد اكتمل
                    if current_text == last_text:
                        stable_count += 1
                        if stable_count >= 3:  # 3 × 2 ثانية = ~6 ثوانٍ ثابتة
                            response_text = current_text
                            break
                    else:
                        stable_count = 0
                        last_text = current_text

                await asyncio.sleep(2)

            # ─── إرسال النتيجة للمستخدم ───
            if response_text:
                max_len = 4000
                if len(response_text) <= max_len:
                    await update.message.reply_text(response_text)
                else:
                    parts = [response_text[i:i + max_len] for i in range(0, len(response_text), max_len)]
                    for part in parts:
                        await update.message.reply_text(part)
            else:
                await update.message.reply_text("⚠️ لم أتمكن من استخراج رد نصي من الموقع.")

        except Exception as e:
            logger.exception("Handle message error")
            await update.message.reply_text(f"⚠️ خطأ أثناء المعالجة: {e}")


def main():
    if not TOKEN:
        raise RuntimeError("❌ متغير BOT_TOKEN غير موجود!")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stream", stream))
    app.add_handler(CommandHandler("stop", stop))

    # استقبال الرسائل النصية (أثناء البث)
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

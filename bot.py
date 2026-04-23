import os
import asyncio
import logging
import re
from typing import Any, Dict

from telegram import (
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
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

sessions: Dict[int, Dict[str, Any]] = {}
sessions_lock = None


async def post_init(application: Application) -> None:
    """تهيئة الـ Lock بعد بدء الـ Event Loop"""
    global sessions_lock
    sessions_lock = asyncio.Lock()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت Gratisfy — وضع الدردشة النصية\n\n"
        "📌 /stream — بدء جلسة جديدة (يفتح المتصفح)\n"
        "📌 /stop  — إيقاف الجلسة وإغلاق المتصفح\n\n"
        "⚡️ بمجرد تشغيل الجلسة، أرسل أي رسالة وسأرد عليك بالنص مباشرة."
    )


async def stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبدأ جلسة دردشة نصية: يفتح المتصفح ويحتفظ به مفتوحاً"""
    chat_id = update.effective_chat.id

    async with sessions_lock:
        if chat_id in sessions and sessions[chat_id].get("active"):
            await update.message.reply_text("⚠️ هناك جلسة نشطة بالفعل! أرسل /stop لإيقافها.")
            return

    await update.message.reply_text("⏳ جاري فتح المتصفح وتسجيل الدخول واختيار النموذج...")

    task = asyncio.create_task(chat_session_worker(chat_id, context))
    async with sessions_lock:
        sessions[chat_id] = {
            "active": True,
            "ready": False,
            "task": task,
            "lock": asyncio.Lock(),
        }


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يغلق الجلسة وينظف المتصفح"""
    chat_id = update.effective_chat.id

    async with sessions_lock:
        if chat_id not in sessions or not sessions[chat_id].get("active"):
            await update.message.reply_text("❌ لا توجد جلسة نشطة حالياً.")
            return
        info = sessions[chat_id]
        info["active"] = False
        task = info.get("task")

    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await update.message.reply_text("⏹️ تم إيقاف الجلسة وإغلاق المتصفح.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل الرسائل النصية ويرسلها للموقع إذا كانت الجلسة جاهزة"""
    chat_id = update.effective_chat.id
    text = update.message.text

    async with sessions_lock:
        if chat_id not in sessions or not sessions[chat_id].get("active"):
            return
        session = sessions[chat_id]
        if not session.get("ready"):
            await update.message.reply_text("⏳ المتصفح لم يجهز بعد، انتظر قليلاً...")
            return
        page = session.get("page")
        if not page:
            await update.message.reply_text("❌ خطأ داخلي: الصفحة غير متوفرة.")
            return
        lock = session.get("lock")

    async with lock:
        try:
            await update.message.reply_text("⏳ جاري إرسال السؤال والانتظار للرد...")
            response = await send_prompt_and_get_response(page, text)

            if response:
                max_len = 4000
                if len(response) <= max_len:
                    await update.message.reply_text(response)
                else:
                    parts = [response[i:i + max_len] for i in range(0, len(response), max_len)]
                    for part in parts:
                        await update.message.reply_text(part)
            else:
                await update.message.reply_text("⚠️ لم أتمكن من استخراج رد نصي من الموقع.")

        except Exception as e:
            logger.exception("Error handling message")
            await update.message.reply_text(f"⚠️ خطأ أثناء المعالجة: {e}")


async def send_prompt_and_get_response(page, prompt_text: str) -> str:
    """يرسل prompt للموقع وينتظر الرد النصي حتى يستقر"""
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
    await asyncio.sleep(0.3)
    await textarea.press("Enter")

    # ─── استخراج الرد النصي ───
    response_text = ""
    start_time = asyncio.get_event_loop().time()
    last_text = ""
    stable_count = 0

    while (asyncio.get_event_loop().time() - start_time) < 120:
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
            if current_text == last_text:
                stable_count += 1
                if stable_count >= 3:  # ~6 ثوانٍ ثابتة = نعتبر الرد اكتمل
                    response_text = current_text
                    break
            else:
                stable_count = 0
                last_text = current_text

        await asyncio.sleep(2)

    return response_text


async def chat_session_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """يفتح المتصفح ويحتفظ به مفتوحاً للدردشة النصية المستمرة"""
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

        # ─── 1) فتح الموقع ───
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

        # ─── 2) تسجيل الدخول ───
        if LOGIN_EMAIL and LOGIN_PASSWORD:
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

                    # إدخال البريد
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

                    # إدخال كلمة المرور
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
                    await page.goto("https://gratisfy.xyz/chat", wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(2)

                except Exception as login_err:
                    logger.warning(f"Login flow error: {login_err}")
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ في الدخول: {login_err}")
                    return
            else:
                await context.bot.send_message(chat_id=chat_id, text="ℹ️ لم يُعثر على زر Log in")
                return

        # ─── 3) اختيار النموذج ───
        if TARGET_MODEL:
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

        # ─── 4) الجلسة جاهزة ───
        async with sessions_lock:
            if chat_id in sessions and sessions[chat_id].get("active"):
                sessions[chat_id]["page"] = page
                sessions[chat_id]["browser"] = browser
                sessions[chat_id]["pw"] = pw
                sessions[chat_id]["ready"] = True

        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ الجلسة جاهزة!\n\nأرسل أي رسالة الآن وسأرد عليك بالنص.\nأرسل /stop لإيقاف الجلسة."
        )

        # إبقاء الـ Task حياً حتى يتم إلغاؤه
        while True:
            await asyncio.sleep(5)
            async with sessions_lock:
                if chat_id not in sessions or not sessions[chat_id].get("active"):
                    break

    except asyncio.CancelledError:
        logger.info(f"Chat session cancelled for {chat_id}")
        raise
    except Exception as e:
        logger.exception("Chat session worker error")
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ توقفت الجلسة بسبب خطأ: {e}")
        except Exception:
            pass
    finally:
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

    # استقبال الرسائل النصية
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

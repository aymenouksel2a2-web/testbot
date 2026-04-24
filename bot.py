import os
import asyncio
import logging
import re
from typing import Any, Dict, Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from playwright.async_api import async_playwright, BrowserContext

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")
URL = "https://gratisfy.xyz/chat"
PORT = int(os.environ.get("PORT", "8080"))
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

LOGIN_EMAIL = os.environ.get("LOGIN_EMAIL")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD")
TARGET_MODEL = os.environ.get("TARGET_MODEL", "Grok Uncensored")

PERSISTENT_DIR = "/tmp/gratisfy-data"

sessions: Dict[int, Dict[str, Any]] = {}
sessions_lock: Optional[asyncio.Lock] = None

# ── Global browser context (initialized once on startup) ──
pw: Optional[Any] = None
browser_ctx: Optional[BrowserContext] = None
browser_ready = asyncio.Event()


async def initialize_browser():
    """يُستدعى مرة واحدة عند تشغيل البوت"""
    global pw, browser_ctx

    pw = await async_playwright().start()

    browser_ctx = await pw.chromium.launch_persistent_context(
        PERSISTENT_DIR,
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-setuid-sandbox",
            "--no-first-run",
            "--no-zygote",
            "--single-process",
        ],
        viewport={"width": 960, "height": 540},
        locale="en-US",
    )

    page = await browser_ctx.new_page()
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        window.chrome = { runtime: {} };
    """)

    await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(0.8)

    # إغلاق popups
    for sel in ["button:has-text('Close')", "[aria-label='Close']", "button.close"]:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=800):
                await loc.click(timeout=1500)
                await asyncio.sleep(0.2)
        except Exception:
            pass

    # تسجيل دخول ذكي
    if LOGIN_EMAIL and LOGIN_PASSWORD:
        needs_login = False
        for sel in ['button:has-text("Log in")', 'a:has-text("Log in")', '[data-testid="login-button"]']:
            try:
                loc = page.locator(sel).first
                await loc.wait_for(state="visible", timeout=3000)
                needs_login = True
                break
            except Exception:
                continue

        if needs_login:
            try:
                await _perform_login(page)
            except Exception as e:
                logger.warning(f"Login error: {e}")

        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(0.8)

    # اختيار النموذج
    if TARGET_MODEL:
        await _select_model(page, TARGET_MODEL)
        await asyncio.sleep(0.3)

    await page.close()

    logger.info("✅ Browser initialized & logged in globally")
    browser_ready.set()


async def post_init(app: Application) -> None:
    global sessions_lock
    sessions_lock = asyncio.Lock()
    await initialize_browser()


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

async def _perform_login(page):
    """تسجيل دخول سريع"""
    btn = page.locator('button:has-text("Log in"), a:has-text("Log in")').first
    await btn.wait_for(state="visible", timeout=5000)
    await btn.click()
    await asyncio.sleep(1.2)

    email_in = None
    for sel in ['input[name="email"]', 'input[type="email"]', 'input[id="email"]']:
        try:
            email_in = page.locator(sel).first
            await email_in.wait_for(state="visible", timeout=5000)
            break
        except Exception:
            continue
    if not email_in:
        raise Exception("حقل البريد غير موجود")
    await email_in.fill(LOGIN_EMAIL)
    await asyncio.sleep(0.2)

    pass_in = None
    for sel in ['input[name="password"]', 'input[type="password"]', 'input[id="password"]']:
        try:
            pass_in = page.locator(sel).first
            await pass_in.wait_for(state="visible", timeout=5000)
            break
        except Exception:
            continue
    if not pass_in:
        raise Exception("حقل كلمة المرور غير موجود")
    await pass_in.fill(LOGIN_PASSWORD)
    await asyncio.sleep(0.2)

    await pass_in.press("Enter")
    await asyncio.sleep(1)

    for txt in ["Submit", "Sign in", "Login", "Continue"]:
        try:
            b = page.locator(f'button:has-text("{txt}")').last
            await b.wait_for(state="visible", timeout=1500)
            await b.click(timeout=2000)
            await asyncio.sleep(0.3)
        except Exception:
            pass
    await asyncio.sleep(1.5)


async def _select_model(page, model_name: str) -> bool:
    """اختيار النموذج بسرعة"""
    trigger = None
    for sel in [
        '[data-testid="model-selector"]',
        'button[class*="model-selector"]',
        '[aria-haspopup="listbox"]',
        'button:has([class*="chevron"])',
    ]:
        try:
            trigger = page.locator(sel).first
            await trigger.wait_for(state="visible", timeout=3000)
            break
        except Exception:
            continue
    if not trigger:
        return False

    await trigger.click()
    await asyncio.sleep(0.6)

    search_in = None
    for sel in ['input[placeholder*="Search" i]', 'input[type="text"]', '[role="searchbox"]']:
        try:
            search_in = page.locator(sel).first
            await search_in.wait_for(state="visible", timeout=3000)
            break
        except Exception:
            continue
    if not search_in:
        return False

    await search_in.fill(model_name)
    await asyncio.sleep(0.4)

    result = None
    for sel in [f"text={model_name}", f'li:has-text("{model_name}")', '[role="option"]']:
        try:
            result = page.locator(sel).first
            await result.wait_for(state="visible", timeout=3000)
            break
        except Exception:
            continue

    if result:
        await result.click()
    else:
        await search_in.press("Enter")
    await asyncio.sleep(0.4)
    return True


async def _extract_response(page, user_text: str, max_wait: int = 90) -> str:
    """استخراج الرد بأسرع طريقة (0.5s interval)"""
    start = asyncio.get_event_loop().time()
    last = ""
    stable = 0

    while (asyncio.get_event_loop().time() - start) < max_wait:
        js = await page.evaluate("""(u) => {
            const raw = document.body.innerText || '';
            const lines = raw.split('\\n').map(l => l.trim()).filter(Boolean);
            
            const junk = [
                'Enter to send','Shift + Enter','Ctrl/Cmd + V','paste attachment',
                'attach file','record','Message Grok','Start a conversation',
                'Select a model','Settings','Gratisfy','to send','for new line',
                'Send message','Attach','Paperclip','Mic','new line',
                'tokens','tok/s','Thinking','Stop generating','Regenerate',
                'Copy','Like','Dislike','Share','Export','Web search','Reason'
            ];
            
            const clean = lines.filter(l => {
                const low = l.toLowerCase();
                return l.length > 2 && l !== u 
                    && !junk.some(j => low.includes(j.toLowerCase()))
                    && !/^\\d+\\.?\\d*\\s*s?$/.test(l)
                    && !/^\\d+\\.?\\d*\\s*tok\\/s?$/.test(l)
                    && !/^\\d+\\s*tokens?$/.test(l);
            });
            
            let idx = -1;
            for(let i=0; i<clean.length; i++){
                if(clean[i]===u || clean[i].includes(u) || u.includes(clean[i])){
                    idx = i;
                    break;
                }
            }
            
            if(idx >= 0 && idx < clean.length - 1){
                return clean.slice(idx + 1).join('\\n');
            }
            
            const long = clean.slice(2).filter(l => l.length > 15);
            if(long.length) return long.sort((a,b) => b.length - a.length)[0];
            
            return clean.length ? clean[clean.length - 1] : '';
        }""", [user_text])

        current = (js or "").strip()
        if current:
            if current == last:
                stable += 1
                if stable >= 2:
                    return current
            else:
                stable = 0
                last = current

        await asyncio.sleep(0.5)

    return last


def _clean_response(text: str) -> str:
    """تنظيف نهائي للرد"""
    t = re.sub(r'\n?\d+\.?\d*\s*s?\s*\n?', '\n', text)
    t = re.sub(r'\n?\d+\.?\d*\s*tok/s?\s*\n?', '\n', t)
    t = re.sub(r'\n?\d+\s*tokens?\s*\n?', '\n', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


# ═══════════════════════════════════════════════════════════════
#  Session Worker (يفتح صفحة جديدة من الـ Context الجاهز)
# ═══════════════════════════════════════════════════════════════

async def session_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    page = None

    try:
        await browser_ready.wait()

        page = await browser_ctx.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            window.chrome = { runtime: {} };
        """)

        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(0.8)

        # إغلاق popups
        for sel in ["button:has-text('Close')", "[aria-label='Close']", "button.close"]:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=800):
                    await loc.click(timeout=1500)
                    await asyncio.sleep(0.2)
            except Exception:
                pass

        # اختيار النموذج على الصفحة الجديدة (إذا لزم)
        if TARGET_MODEL:
            await _select_model(page, TARGET_MODEL)
            await asyncio.sleep(0.3)

        # تخزين الصفحة
        async with sessions_lock:
            if chat_id in sessions:
                sessions[chat_id]["page"] = page
                sessions[chat_id]["ready"] = True

        logger.info(f"[{chat_id}] Session ready")

        # إبقاء الجلسة حية
        while True:
            await asyncio.sleep(5)
            async with sessions_lock:
                if chat_id not in sessions or not sessions[chat_id].get("active"):
                    break

    except asyncio.CancelledError:
        logger.info(f"[worker] Cancelled {chat_id}")
        raise
    except Exception as e:
        logger.exception("Session worker error")
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ في الجلسة: {str(e)[:200]}")
        except Exception:
            pass
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        async with sessions_lock:
            sessions.pop(chat_id, None)


async def ensure_session(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """يضمن وجود جلسة نشطة"""
    async with sessions_lock:
        sess = sessions.get(chat_id)
        if sess and sess.get("active"):
            return sess

    task = asyncio.create_task(session_worker(chat_id, context))
    async with sessions_lock:
        sessions[chat_id] = {
            "active": True,
            "ready": False,
            "page": None,
            "lock": asyncio.Lock(),
            "task": task,
        }

    for _ in range(90):
        await asyncio.sleep(1)
        async with sessions_lock:
            s = sessions.get(chat_id)
            if s and s.get("ready"):
                return s
    return None


# ═══════════════════════════════════════════════════════════════
#  Handlers
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Gratisfy AI* — دردشة نصية سريعة\n"
        "أرسل أي رسالة للبدء.\n"
        "📌 /stop — إغلاق الجلسة",
        parse_mode="Markdown",
    )
    # تشغيل الجلسة في الخلفية مباشرة
    asyncio.create_task(ensure_session(update.effective_chat.id, context))


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with sessions_lock:
        sess = sessions.pop(chat_id, None)

    if not sess:
        await update.message.reply_text("❌ لا توجد جلسة نشطة.")
        return

    task = sess.get("task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await update.message.reply_text("⏹️ تم إغلاق الجلسة.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    # التأكد من الجلسة
    sess = None
    async with sessions_lock:
        sess = sessions.get(chat_id)

    if not sess or not sess.get("active"):
        await update.message.reply_text("⏳ جاري تهيئة الجلسة...")
        sess = await ensure_session(chat_id, context)
        if not sess:
            await update.message.reply_text("❌ فشل في تهيئة الجلسة.")
            return

    if not sess.get("ready"):
        await update.message.reply_text("⏳ المتصفح يجهز، أرسل مجدداً خلال ثوانٍ...")
        return

    lock = sess.get("lock")
    page = sess.get("page")
    if not lock or not page:
        await update.message.reply_text("❌ خطأ داخلي.")
        return

    async with lock:
        try:
            textarea = None
            for sel in [
                'textarea[placeholder*="Message" i]',
                'textarea[class*="chat-input"]',
                'textarea',
                'div[contenteditable="true"]',
            ]:
                try:
                    textarea = page.locator(sel).first
                    await textarea.wait_for(state="visible", timeout=4000)
                    break
                except Exception:
                    continue

            if not textarea:
                await update.message.reply_text("❌ لم أجد حقل الكتابة.")
                return

            await textarea.fill(text)
            await asyncio.sleep(0.2)
            await textarea.press("Enter")

            response = await _extract_response(page, text)

            if not response:
                await update.message.reply_text("⚠️ لم يصل رد من الموقع.")
                return

            cleaned = _clean_response(response)

            max_len = 4000
            if len(cleaned) <= max_len:
                await update.message.reply_text(cleaned)
            else:
                for i in range(0, len(cleaned), max_len):
                    await update.message.reply_text(cleaned[i : i + max_len])

        except Exception as e:
            logger.exception("Handle message error")
            await update.message.reply_text(f"⚠️ خطأ: {str(e)[:200]}")


def main():
    if not TOKEN:
        raise RuntimeError("❌ BOT_TOKEN غير موجود!")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if RAILWAY_DOMAIN:
        secret = TOKEN.split(":")[-1]
        webhook_url = f"https://{RAILWAY_DOMAIN}/{secret}"
        logger.info(f"🚀 Webhook: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=secret,
            webhook_url=webhook_url,
            drop_pending_updates=True,
        )
    else:
        logger.warning("⚠️ Polling mode")
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

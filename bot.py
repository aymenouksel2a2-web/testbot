import os
import asyncio
import logging
import io
import re
from typing import Any, Dict, Optional, List

from telegram import Update, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest, RetryAfter, TimedOut, NetworkError
from playwright.async_api import async_playwright

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  إعدادات البيئة
# ═══════════════════════════════════════════════════════════════

TOKEN = os.environ.get("BOT_TOKEN")
URL = os.environ.get("GRATISFY_URL", "https://gratisfy.xyz/chat")
PORT = int(os.environ.get("PORT", "8080"))
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

LOGIN_EMAIL = os.environ.get("LOGIN_EMAIL")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD")
TARGET_MODEL = os.environ.get("TARGET_MODEL", "Grok Uncensored")

STREAM_INTERVAL = float(os.environ.get("STREAM_INTERVAL", "3"))
EXTRACT_TIMEOUT = int(os.environ.get("EXTRACT_TIMEOUT", "120"))
RESPONSE_STABLE_ROUNDS = int(os.environ.get("RESPONSE_STABLE_ROUNDS", "2"))
PERSISTENT_DIR = os.environ.get("PERSISTENT_DIR", "/tmp/gratisfy-data")
SCREENSHOT_QUALITY = int(os.environ.get("SCREENSHOT_QUALITY", "60"))
VIEWPORT_WIDTH = int(os.environ.get("VIEWPORT_WIDTH", "960"))
VIEWPORT_HEIGHT = int(os.environ.get("VIEWPORT_HEIGHT", "540"))

# ═══════════════════════════════════════════════════════════════
#  بنية البيانات
# ═══════════════════════════════════════════════════════════════

streams: Dict[int, Dict[str, Any]] = {}
streams_lock: Optional[asyncio.Lock] = None


async def post_init(app: Application) -> None:
    global streams_lock
    streams_lock = asyncio.Lock()
    logger.info("✅ Bot initialized")


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

async def safe_reply(update: Update, text: str, **kwargs):
    """يرسل رسالة Telegram مع التعامل مع حدود Telegram المؤقتة."""
    try:
        return await update.message.reply_text(text, **kwargs)
    except RetryAfter as e:
        await asyncio.sleep(float(e.retry_after) + 0.5)
        return await update.message.reply_text(text, **kwargs)


async def safe_send_text(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
    try:
        return await context.bot.send_message(chat_id=chat_id, text=text)
    except RetryAfter as e:
        await asyncio.sleep(float(e.retry_after) + 0.5)
        return await context.bot.send_message(chat_id=chat_id, text=text)


def split_telegram_text(text: str, max_len: int = 3900) -> List[str]:
    """يقسم النص الطويل مع محاولة القص عند نهاية سطر أو مسافة."""
    text = (text or "").strip()
    if not text:
        return []

    parts: List[str] = []
    remaining = text
    while len(remaining) > max_len:
        cut = remaining.rfind("\n", 0, max_len)
        if cut < max_len * 0.55:
            cut = remaining.rfind(" ", 0, max_len)
        if cut < max_len * 0.55:
            cut = max_len
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


async def snap(
    page: Any,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    caption: str,
    first: bool = False,
):
    """يلتقط لقطة شاشة ويُحدّث رسالة البث نفسها."""
    try:
        if page.is_closed():
            return

        screenshot = await page.screenshot(type="jpeg", quality=SCREENSHOT_QUALITY)
        photo = io.BytesIO(screenshot)
        photo.name = "stream.jpg"

        async with streams_lock:
            session = streams.get(chat_id)
            if not session:
                return
            msg_id = session.get("message_id")

        if first or msg_id is None:
            sent = await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
            )
            async with streams_lock:
                if chat_id in streams:
                    streams[chat_id]["message_id"] = sent.message_id
        else:
            edit = io.BytesIO(screenshot)
            edit.name = "stream.jpg"
            await context.bot.edit_message_media(
                chat_id=chat_id,
                message_id=msg_id,
                media=InputMediaPhoto(media=edit, caption=caption),
            )
    except RetryAfter as e:
        logger.warning(f"[snap] RetryAfter: {e.retry_after}")
        await asyncio.sleep(float(e.retry_after) + 0.5)
    except (TimedOut, NetworkError) as e:
        logger.warning(f"[snap] Telegram network error: {e}")
    except BadRequest as e:
        low = str(e).lower()
        if "not modified" not in low and "message to edit not found" not in low:
            logger.warning(f"[snap] BadRequest: {e}")
    except Exception as e:
        logger.warning(f"[snap] Error: {e}")


async def close_popups(page) -> None:
    """يغلق النوافذ المنبثقة مثل Discord popup حتى لا تغطي حقل الكتابة."""
    selectors = [
        'button:has-text("Not now")',
        'button:has-text("NOT NOW")',
        'button:has-text("No thanks")',
        'button:has-text("Maybe later")',
        'button:has-text("Close")',
        '[aria-label="Close"]',
        '[aria-label="close"]',
        'button.close',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=700):
                await loc.click(timeout=1500)
                await page.wait_for_timeout(300)
        except Exception:
            continue


async def is_login_visible(page) -> bool:
    """يتحقق هل زر Log in ظاهر في الصفحة حالياً."""
    selectors = [
        'button:has-text("Log in")',
        'a:has-text("Log in")',
        'button:has-text("Login")',
        'a:has-text("Login")',
        '[data-testid="login-button"]',
        'header button:has-text("Log in")',
        'header a:has-text("Log in")',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=2500)
            return True
        except Exception:
            continue
    return False


async def perform_login(page):
    """ينفّذ تسجيل الدخول كاملاً إذا ظهرت شاشة الدخول."""
    login_btn = page.locator(
        'button:has-text("Log in"), a:has-text("Log in"), button:has-text("Login"), a:has-text("Login")'
    ).first
    await login_btn.wait_for(state="visible", timeout=7000)
    await login_btn.click()
    await asyncio.sleep(2.0)

    email_in = None
    for sel in [
        'input[name="email"]',
        'input[type="email"]',
        'input[id="email"]',
        'input[placeholder*="email" i]',
    ]:
        try:
            email_in = page.locator(sel).first
            await email_in.wait_for(state="visible", timeout=8000)
            break
        except Exception:
            continue
    if not email_in:
        raise RuntimeError("لم يُعثر على حقل البريد")

    await email_in.fill(LOGIN_EMAIL or "")
    await asyncio.sleep(0.3)

    pass_in = None
    for sel in [
        'input[name="password"]',
        'input[type="password"]',
        'input[id="password"]',
        'input[placeholder*="password" i]',
    ]:
        try:
            pass_in = page.locator(sel).first
            await pass_in.wait_for(state="visible", timeout=8000)
            break
        except Exception:
            continue
    if not pass_in:
        raise RuntimeError("لم يُعثر على حقل كلمة المرور")

    await pass_in.fill(LOGIN_PASSWORD or "")
    await asyncio.sleep(0.3)
    await pass_in.press("Enter")
    await asyncio.sleep(1.0)

    # fallback إذا لم يعمل Enter
    for btn_text in ["Submit", "Sign in", "Sign In", "Login", "Continue"]:
        try:
            btn = page.locator(f'button:has-text("{btn_text}")').last
            if await btn.is_visible(timeout=1000):
                await btn.click(timeout=3000)
                await asyncio.sleep(1.0)
        except Exception:
            pass

    await asyncio.sleep(3.0)


async def select_model(page, model_name: str) -> bool:
    """يختار النموذج من القائمة المنسدلة بدون كسر إذا تغيّر شكل الزر."""
    if not model_name:
        return False

    await close_popups(page)

    trigger = None
    selectors = [
        '[data-testid="model-selector"]',
        'button[class*="model-selector"]',
        '[aria-haspopup="listbox"]',
        'button:has([class*="chevron"])',
        'button:has-text("Grok")',
        'button:has-text("Select")',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=3500)
            trigger = loc
            break
        except Exception:
            continue

    if not trigger:
        try:
            trigger = page.locator("button", has_text=re.compile(r"grok|model|select", re.IGNORECASE)).first
            await trigger.wait_for(state="visible", timeout=3000)
        except Exception:
            return False

    try:
        selected_text = (await trigger.inner_text(timeout=1500)).strip()
        if model_name.lower() in selected_text.lower():
            return True
    except Exception:
        pass

    await trigger.click()
    await asyncio.sleep(1.0)

    # إذا ظهرت خانة بحث في القائمة
    search_in = None
    for sel in [
        'input[placeholder*="Search" i]',
        'input[type="search"]',
        'input[type="text"]',
        '[role="searchbox"]',
    ]:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=2500)
            search_in = loc
            break
        except Exception:
            continue

    if search_in:
        try:
            await search_in.fill(model_name)
            await asyncio.sleep(0.8)
        except Exception:
            pass

    # اختيار النتيجة
    result = None
    result_selectors = [
        f'text="{model_name}"',
        f'text={model_name}',
        f'li:has-text("{model_name}")',
        f'button:has-text("{model_name}")',
        f'[role="option"]:has-text("{model_name}")',
        '[role="option"]',
    ]
    for sel in result_selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=3500)
            result = loc
            break
        except Exception:
            continue

    if result:
        await result.click()
    elif search_in:
        await search_in.press("Enter")
    else:
        return False

    await asyncio.sleep(1.2)
    await close_popups(page)
    return True


async def find_message_input(page):
    """يعثر على حقل الرسالة في Gratisfy."""
    selectors = [
        'textarea[placeholder*="Message" i]',
        'textarea[placeholder*="Grok" i]',
        'textarea[placeholder*="Ask" i]',
        'textarea[class*="chat-input"]',
        'textarea',
        '[role="textbox"]',
        'div[contenteditable="true"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).last
            await loc.wait_for(state="visible", timeout=5000)
            return loc
        except Exception:
            continue
    return None


async def send_prompt_to_page(page, text: str) -> None:
    """يرسل النص من واجهة الموقع كما في النسخة الأصلية."""
    await close_popups(page)
    textarea = await find_message_input(page)
    if not textarea:
        raise RuntimeError("لم أجد حقل الكتابة في الموقع")

    await textarea.click(timeout=5000)
    try:
        await textarea.fill(text)
    except Exception:
        # fallback لبعض حقول contenteditable
        modifier = "Meta" if os.name == "posix" and os.uname().sysname == "Darwin" else "Control"
        await page.keyboard.press(f"{modifier}+A")
        await page.keyboard.type(text, delay=1)

    await asyncio.sleep(0.25)
    await textarea.press("Enter")
    await page.wait_for_timeout(800)

    # fallback إذا بقي النص في الحقل ولم يرسل بالـ Enter
    try:
        still_has_text = await textarea.evaluate("(el) => (el.value || el.innerText || '').trim().length > 0")
    except Exception:
        still_has_text = False

    if still_has_text:
        for sel in [
            'button[aria-label*="Send" i]',
            'button:has-text("Send")',
            'button[type="submit"]',
            'button svg[class*="send"]',
        ]:
            try:
                btn = page.locator(sel).last
                if await btn.is_visible(timeout=1000):
                    await btn.click(timeout=3000)
                    await page.wait_for_timeout(800)
                    break
            except Exception:
                continue


async def extract_response(page, user_text: str, timeout_sec: int = EXTRACT_TIMEOUT) -> str:
    """
    يستخرج آخر رد ظاهر من واجهة Gratisfy.

    هذه الدالة محافظة على طريقة الملف الأصلي: لا تستخدم API ولا wait_for_response.
    لكنها تصلح مشاكل النسخة الأصلية:
    - تمرير user_text إلى JS كنص وليس كقائمة.
    - اختيار آخر رسالة مستخدم، لا أول رسالة، حتى لا تختلط المحادثات السابقة.
    - حذف سطور الواجهة مثل Enter واسم النموذج والسرعة والتوكنات.
    - انتظار استقرار النص قبل إرساله.
    """
    start = asyncio.get_event_loop().time()
    last_text = ""
    stable_rounds = 0

    js = r"""(u) => {
        const normalize = (value) => String(value || '')
            .replace(/\u00a0/g, ' ')
            .replace(/[ \t]+/g, ' ')
            .trim();

        const user = normalize(u);
        const raw = document.body.innerText || '';
        const lines = raw.split(/\n+/).map(normalize).filter(Boolean);

        const exactJunk = new Set([
            'enter', 'shift + enter', 'ctrl/cmd + v', 'ctrl + v', 'cmd + v',
            'attach file', 'record', 'copy', 'like', 'dislike', 'share', 'export',
            'web search', 'reason', 'settings', 'gratisfy', 'send message',
            'start a conversation', 'select a model above and type a message to begin chatting.',
            'join discord', 'not now', 'community'
        ]);

        const junkContains = [
            'enter to send', 'to send', 'for new line', 'paste attachment', 'message grok',
            'message chatgpt', 'message claude', 'message gemini', 'stop generating',
            'regenerate', 'attach file', 'select a model', 'join the gratisfy discord',
            'get benchmark alerts', 'model drops in one place', 'microphone', 'paperclip'
        ];

        const isUserLine = (line) => {
            const n = normalize(line);
            if (!user) return false;
            if (n === user) return true;
            // بعض الواجهات تضيف رموزاً بسيطة حول رسالة المستخدم الطويلة
            if (user.length >= 12 && n.includes(user) && n.length <= user.length + 20) return true;
            return false;
        };

        const isJunkLine = (line) => {
            const low = normalize(line).toLowerCase();
            if (!low || low.length <= 1) return true;
            if (exactJunk.has(low)) return true;
            if (junkContains.some(j => low.includes(j))) return true;

            // سطور إحصاءات التوليد
            if (/^\d+(\.\d+)?\s*s$/.test(low)) return true;
            if (/^\d+(\.\d+)?\s*tok\/s$/.test(low)) return true;
            if (/^\d+\s*tokens?$/.test(low)) return true;
            if (/\b\d+(\.\d+)?\s*s\b/.test(low) && /\btok\/s\b|\btokens?\b/.test(low)) return true;

            // اسم النموذج/المزود الذي يظهر فوق الرد
            if (/^(navy|openai|anthropic|google|meta|deepseek|mistral|qwen)\s*[·•\-]\s*/i.test(line)) return true;
            if (/^grok\s*(uncensored|\d|mini|beta)?$/i.test(line)) return true;

            // أزرار/اختصارات أسفل حقل الكتابة
            if (/^(enter|shift\s*\+\s*enter|ctrl\/cmd\s*\+\s*v)/i.test(line)) return true;

            return false;
        };

        // نستخدم آخر ظهور لرسالة المستخدم لأن المحادثة قد تحتوي رسائل قديمة بنفس النص.
        let lastUserIndex = -1;
        for (let i = 0; i < lines.length; i++) {
            if (isUserLine(lines[i])) lastUserIndex = i;
        }

        let scoped = lastUserIndex >= 0 ? lines.slice(lastUserIndex + 1) : lines.slice();
        let cleaned = scoped.filter(l => !isJunkLine(l));

        // حذف التكرار المتتالي فقط، بدون حذف محتوى الرد نفسه.
        const out = [];
        for (const line of cleaned) {
            if (out[out.length - 1] !== line) out.push(line);
        }

        return out.join('\n').trim();
    }"""

    while (asyncio.get_event_loop().time() - start) < timeout_sec:
        try:
            current = (await page.evaluate(js, user_text) or "").strip()
        except Exception as e:
            logger.warning(f"[extract_response] evaluate failed: {e}")
            current = ""

        # لا نعتبر رسالة المستخدم نفسها رداً.
        if current and current.strip() != (user_text or "").strip():
            if current == last_text:
                stable_rounds += 1
                if stable_rounds >= RESPONSE_STABLE_ROUNDS:
                    return current
            else:
                stable_rounds = 0
                last_text = current

        await asyncio.sleep(1.5)

    return last_text.strip()


# ═══════════════════════════════════════════════════════════════
#  Handlers
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(
        update,
        "🤖 *Gratisfy Ultra* — بث مباشر + دردشة نصية\n\n"
        "📌 /stream — بدء جلسة جديدة (متصفح مستمر)\n"
        "📌 /stop  — إيقاف الجلسة\n"
        "📌 /status — حالة الجلسة\n\n"
        "⚡️ أرسل أي رسالة بعد بدء البث للحصول على رد نصي فوري.",
        parse_mode="Markdown",
    )


async def stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with streams_lock:
        if chat_id in streams and streams[chat_id].get("active"):
            await safe_reply(update, "⚠️ هناك بث نشط بالفعل! أرسل /stop لإيقافه أولاً.")
            return

        streams[chat_id] = {
            "active": True,
            "ready": False,
            "page": None,
            "lock": asyncio.Lock(),
            "message_id": None,
            "task": None,
            "started_at": asyncio.get_event_loop().time(),
        }

    await safe_reply(update, "⏳ جاري تهيئة المتصفح المستمر...")
    task = asyncio.create_task(stream_worker(chat_id, context))

    async with streams_lock:
        if chat_id in streams:
            streams[chat_id]["task"] = task


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with streams_lock:
        session = streams.get(chat_id)
        if not session or not session.get("active"):
            await safe_reply(update, "❌ لا يوجد بث نشط حالياً.")
            return
        session["active"] = False
        task = session.get("task")

    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await safe_reply(update, "⏹️ تم إيقاف البث وإغلاق المتصفح.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with streams_lock:
        session = streams.get(chat_id)
        if not session or not session.get("active"):
            await safe_reply(update, "❌ لا توجد جلسة نشطة.")
            return
        ready = session.get("ready", False)
        page = session.get("page")
        page_ok = bool(page and not page.is_closed())

    if ready and page_ok:
        await safe_reply(update, "✅ الجلسة نشطة وجاهزة لاستقبال الرسائل.")
    else:
        await safe_reply(update, "⏳ الجلسة قيد التجهيز أو المتصفح غير جاهز بعد.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل الرسائل أثناء البث النشط ويرد بالنص."""
    chat_id = update.effective_chat.id
    text = update.message.text or ""

    async with streams_lock:
        session = streams.get(chat_id)
        if not session or not session.get("active") or not session.get("ready"):
            return
        lock = session.get("lock")
        page = session.get("page")

    if not lock or not page or page.is_closed():
        await safe_reply(update, "⚠️ المتصفح غير جاهز. أرسل /stop ثم /stream لإعادة التشغيل.")
        return

    async with lock:
        status_msg = None
        try:
            status_msg = await safe_reply(update, "⏳ جاري إرسال السؤال...")

            await send_prompt_to_page(page, text)
            await close_popups(page)
            await status_msg.edit_text("⏳ تم الإرسال! بانتظار الرد...")

            response = await extract_response(page, text, timeout_sec=EXTRACT_TIMEOUT)

            try:
                await status_msg.delete()
            except Exception:
                pass

            if not response:
                await safe_reply(update, "⚠️ لم أتمكن من استخراج رد نصي من الموقع.")
                return

            for part in split_telegram_text(response):
                await safe_reply(update, part)

        except Exception as e:
            logger.exception("[handle_message] Error")
            error_text = f"⚠️ خطأ: {str(e)[:250]}"
            if status_msg:
                try:
                    await status_msg.edit_text(error_text)
                except Exception:
                    await safe_reply(update, error_text)
            else:
                await safe_reply(update, error_text)


# ═══════════════════════════════════════════════════════════════
#  Stream Worker
# ═══════════════════════════════════════════════════════════════

async def stream_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    pw = None
    browser_ctx = None
    page = None

    try:
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
                "--disable-extensions",
                "--disable-background-timer-throttling",
            ],
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            locale="en-US",
        )

        page = await browser_ctx.new_page()
        page.set_default_timeout(15000)

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            window.chrome = window.chrome || { runtime: {} };
        """)

        await snap(page, context, chat_id, "🌐 جاري فتح المتصفح...", first=True)
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1.5)
        await close_popups(page)
        await snap(page, context, chat_id, "🌐 تم الوصول إلى الموقع")

        if LOGIN_EMAIL and LOGIN_PASSWORD:
            await snap(page, context, chat_id, "🔍 التحقق من حالة الجلسة...")

            if await is_login_visible(page):
                await snap(page, context, chat_id, "🔐 تسجيل الدخول مطلوب...")
                try:
                    await perform_login(page)
                    await close_popups(page)
                    await snap(page, context, chat_id, "✅ تم تسجيل الدخول!")
                except Exception as e:
                    logger.warning(f"Login error: {e}")
                    await snap(page, context, chat_id, f"⚠️ خطأ في الدخول: {str(e)[:100]}")
            else:
                await snap(page, context, chat_id, "✅ الجلسة محفوظة (مسجل مسبقاً)")

            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1.5)
            await close_popups(page)

        if TARGET_MODEL:
            await snap(page, context, chat_id, f"🔽 اختيار النموذج: {TARGET_MODEL}...")
            ok = await select_model(page, TARGET_MODEL)
            if ok:
                await snap(page, context, chat_id, f"✅ النموذج: {TARGET_MODEL}")
            else:
                await snap(page, context, chat_id, "ℹ️ لم يُعثر على قائمة النماذج، سأكمل بالنموذج الحالي.")

        async with streams_lock:
            if chat_id in streams and streams[chat_id].get("active"):
                streams[chat_id]["page"] = page
                streams[chat_id]["ready"] = True

        await snap(page, context, chat_id, "✅ جاهز! أرسل أي رسالة الآن.\nأرسل /stop لإيقاف البث.")

        while True:
            async with streams_lock:
                if chat_id not in streams or not streams[chat_id].get("active"):
                    break
            await close_popups(page)
            await snap(page, context, chat_id, f"📡 بث مباشر · يُحدّث كل {int(STREAM_INTERVAL) if STREAM_INTERVAL.is_integer() else STREAM_INTERVAL}s")
            await asyncio.sleep(STREAM_INTERVAL)

    except asyncio.CancelledError:
        logger.info(f"[worker] Cancelled for {chat_id}")
        raise
    except Exception as e:
        logger.exception("Stream worker error")
        try:
            await safe_send_text(context, chat_id, f"⚠️ توقف البث: {str(e)[:300]}")
        except Exception:
            pass
    finally:
        async with streams_lock:
            if chat_id in streams:
                streams[chat_id]["ready"] = False
                streams[chat_id]["active"] = False

        if page:
            try:
                await page.close()
            except Exception:
                pass
        if browser_ctx:
            try:
                await browser_ctx.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass

        async with streams_lock:
            streams.pop(chat_id, None)


# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════

def main():
    if not TOKEN:
        raise RuntimeError("❌ BOT_TOKEN غير موجود!")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stream", stream))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("status", status))
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
        logger.warning("⚠️ Polling mode active")
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )


if __name__ == "__main__":
    main()

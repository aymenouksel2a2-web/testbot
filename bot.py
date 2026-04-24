import os
import asyncio
import logging
import io
import re
from typing import Any, Dict, Optional

from telegram import Update, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest, RetryAfter
from playwright.async_api import async_playwright

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  إعدادات البيئة
# ═══════════════════════════════════════════════════════════════

TOKEN = os.environ.get("BOT_TOKEN")
URL = os.environ.get("TARGET_URL", "https://gratisfy.xyz/chat")
PORT = int(os.environ.get("PORT", "8080"))
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

LOGIN_EMAIL = os.environ.get("LOGIN_EMAIL")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD")
TARGET_MODEL = os.environ.get("TARGET_MODEL", "Grok Uncensored")

STREAM_INTERVAL = int(os.environ.get("STREAM_INTERVAL", "10"))
PERSISTENT_BASE_DIR = os.environ.get("PERSISTENT_DIR", "/tmp/gratisfy-data")
MAX_CONCURRENT_SESSIONS = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "5"))

# صلاحيات المستخدمين (comma-separated user IDs)
ALLOWED_USERS = {
    int(x.strip())
    for x in os.environ.get("ALLOWED_USERS", "").split(",")
    if x.strip()
}

# ═══════════════════════════════════════════════════════════════
#  بنية البيانات
# ═══════════════════════════════════════════════════════════════

streams: Dict[int, Dict[str, Any]] = {}
streams_lock: Optional[asyncio.Lock] = None
active_count: int = 0


async def post_init(app: Application) -> None:
    global streams_lock
    streams_lock = asyncio.Lock()
    logger.info("✅ Bot initialized")


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

def is_allowed(update: Update) -> bool:
    if not ALLOWED_USERS:
        return True
    user = update.effective_user
    return bool(user and user.id in ALLOWED_USERS)


def get_user_data_dir(chat_id: int) -> str:
    safe_chat_id = str(chat_id).replace("-", "neg_")
    path = os.path.join(PERSISTENT_BASE_DIR, safe_chat_id)
    os.makedirs(path, exist_ok=True)
    return path


async def snap(
    page: Any,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    caption: str,
    first: bool = False,
):
    """يلتقط لقطة شاشة ويُحدّث الرسالة المصوّرة في نفس الرسالة"""
    try:
        screenshot = await page.screenshot(type="jpeg", quality=60)
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
            try:
                await context.bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=msg_id,
                    media=InputMediaPhoto(media=edit, caption=caption),
                )
            except RetryAfter as e:
                logger.warning(f"[snap] RetryAfter: sleeping {e.retry_after}s")
                await asyncio.sleep(e.retry_after)
            except BadRequest as e:
                if "not modified" not in str(e).lower():
                    logger.warning(f"[snap] BadRequest: {e}")
    except Exception as e:
        logger.warning(f"[snap] Error: {e}")


async def is_login_visible(page) -> bool:
    """يتحقق هل زر Log in ظاهر في الصفحة حالياً"""
    selectors = [
        'button:has-text("Log in")',
        'a:has-text("Log in")',
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
    """ينفّذ تسجيل الدخول كاملاً (يفترض أن الزر ظاهر)"""
    login_btn = page.locator(
        'button:has-text("Log in"), a:has-text("Log in")'
    ).first
    await login_btn.wait_for(state="visible", timeout=5000)
    await login_btn.click()
    await asyncio.sleep(2.5)

    # ── إدخال البريد ──
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
        raise Exception("لم يُعثر على حقل البريد")

    await email_in.fill(LOGIN_EMAIL)
    await asyncio.sleep(0.4)

    # ── إدخال كلمة المرور ──
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
        raise Exception("لم يُعثر على حقل كلمة المرور")

    await pass_in.fill(LOGIN_PASSWORD)
    await asyncio.sleep(0.4)

    # ── إرسال ──
    await pass_in.press("Enter")
    await asyncio.sleep(1.0)

    # Fallback submit buttons
    for btn_text in ["Submit", "Sign in", "Login", "Continue"]:
        try:
            btn = page.locator(f'button:has-text("{btn_text}")').last
            await btn.wait_for(state="visible", timeout=2000)
            await btn.click(timeout=3000)
            await asyncio.sleep(1.5)
        except Exception:
            pass

    await asyncio.sleep(3)

    # تحقق نهائي من نجاح الدخول
    if await is_login_visible(page):
        raise Exception("ما زال زر Log in ظاهراً بعد تسجيل الدخول")


async def select_model(page, model_name: str) -> bool:
    """يختار النموذج من القائمة المنسدلة"""
    trigger = None
    for sel in [
        '[data-testid="model-selector"]',
        'button[class*="model-selector"]',
        '[aria-haspopup="listbox"]',
        'button:has([class*="chevron"])',
    ]:
        try:
            trigger = page.locator(sel).first
            await trigger.wait_for(state="visible", timeout=5000)
            break
        except Exception:
            continue

    if not trigger:
        try:
            trigger = page.locator(
                "button", has_text=re.compile(r"Grok|model", re.IGNORECASE)
            ).first
            await trigger.wait_for(state="visible", timeout=3000)
        except Exception:
            return False

    await trigger.click()
    await asyncio.sleep(1.5)

    # حقل البحث
    search_in = None
    for sel in [
        'input[placeholder*="Search" i]',
        'input[type="text"]',
        '[role="searchbox"]',
    ]:
        try:
            search_in = page.locator(sel).first
            await search_in.wait_for(state="visible", timeout=5000)
            break
        except Exception:
            continue

    if not search_in:
        return False

    await search_in.fill(model_name)
    await asyncio.sleep(1.0)

    # اختيار النتيجة
    result = None
    for sel in [
        f"text={model_name}",
        f'li:has-text("{model_name}")',
        '[role="option"]',
        f'button:has-text("{model_name}")',
    ]:
        try:
            result = page.locator(sel).first
            await result.wait_for(state="visible", timeout=5000)
            break
        except Exception:
            continue

    if result:
        await result.click()
    else:
        await search_in.press("Enter")

    await asyncio.sleep(1.5)
    return True


async def extract_response(page, user_text: str, timeout_sec: int = 120) -> str:
    """يستخرج رد البوت باستخدام innerText مع فلترة UI قوية"""
    start = asyncio.get_event_loop().time()
    last_text = ""
    stable = 0

    while (asyncio.get_event_loop().time() - start) < timeout_sec:
        # محاولة 1: استخدام selectors DOM شائعة إن وجدت
        try:
            assistant_msgs = await page.locator(
                '[data-message-author-role="assistant"] .markdown, '
                '.message-content.bot-message, '
                '.assistant-message, '
                '.prose, '
                '[class*="message-content"]:not([class*="user"])'
            ).all_inner_texts()
            if assistant_msgs:
                candidate = assistant_msgs[-1].strip()
                if candidate and candidate != user_text and len(candidate) > 2:
                    if candidate == last_text:
                        stable += 1
                        if stable >= 3:
                            return candidate
                    else:
                        stable = 0
                        last_text = candidate
        except Exception:
            pass

        # محاولة 2: قراءة innerText
        js_result = await page.evaluate(
            """(u) => {
                const raw = document.body.innerText || '';
                const lines = raw.split('\\n').map(l => l.trim()).filter(Boolean);
                
                const junk = [
                    'Enter to send','Shift + Enter','Ctrl/Cmd + V','paste attachment',
                    'attach file','record','Message Grok','Start a conversation',
                    'Select a model','Settings','Gratisfy','to send','for new line',
                    'Send message','Attach','Paperclip','Mic','new line',
                    'tokens','tok/s','Thinking','Stop generating','Regenerate',
                    'Copy','Like','Dislike','Share','Export','Web search','Reason',
                    'Log in','Sign up','Start chatting'
                ];
                
                const clean = lines.filter(l => {
                    const low = l.toLowerCase();
                    return l.length > 2 
                        && !junk.some(j => low.includes(j.toLowerCase()))
                        && !/^\\d+\\.?\\d*\\s*s?$/.test(l)
                        && !/^\\d+\\.?\\d*\\s*tok\\/s?$/.test(l)
                        && !/^\\d+\\s*tokens?$/.test(l);
                });
                
                // البحث عن سؤال المستخدم في الخطوط الأصلية (غير المنقحة) من الأسفل
                let idx = -1;
                for(let i=lines.length-1; i>=0; i--){
                    if(lines[i]===u || lines[i].includes(u) || u.includes(lines[i])){
                        idx = i;
                        break;
                    }
                }
                
                if(idx >= 0 && idx < lines.length - 1){
                    const after = lines.slice(idx + 1).filter(l => {
                        const low = l.toLowerCase();
                        return l.length > 2 
                            && !junk.some(j => low.includes(j.toLowerCase()))
                            && !/^\\d+\\.?\\d*\\s*s?$/.test(l)
                            && !/^\\d+\\.?\\d*\\s*tok\\/s?$/.test(l)
                            && !/^\\d+\\s*tokens?$/.test(l);
                    });
                    if(after.length) return after.join('\\n');
                }
                
                // fallback: أطول سطر منظف
                const long = clean.filter(l => l.length > 15);
                if(long.length) return long.sort((a,b) => b.length - a.length)[0];
                
                return clean.length ? clean[clean.length - 1] : '';
            }""",
            user_text,
        )

        current = (js_result or "").strip()
        if current:
            if current == last_text:
                stable += 1
                if stable >= 3:
                    return current
            else:
                stable = 0
                last_text = current

        await asyncio.sleep(2)

    return last_text


# ═══════════════════════════════════════════════════════════════
#  Handlers
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("🚫 غير مصرح لك باستخدام هذا البوت.")
        return

    await update.message.reply_text(
        "🤖 *Gratisfy Ultra* — بث مباشر + دردشة نصية\n\n"
        "📌 /stream — بدء جلسة جديدة (متصفح مستمر)\n"
        "📌 /stop  — إيقاف الجلسة\n\n"
        "⚡️ أرسل أي رسالة بعد بدء البث للحصول على رد نصي فوري.",
        parse_mode="Markdown",
    )


async def stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("🚫 غير مصرح لك باستخدام هذا البوت.")
        return

    chat_id = update.effective_chat.id

    async with streams_lock:
        global active_count
        if chat_id in streams and streams[chat_id].get("active"):
            await update.message.reply_text(
                "⚠️ هناك بث نشط بالفعل! أرسل /stop لإيقافه أولاً."
            )
            return

        if active_count >= MAX_CONCURRENT_SESSIONS:
            await update.message.reply_text(
                "⚠️ الحد الأقصى للجلسات النشطة وُصل. جرّب لاحقاً."
            )
            return

        streams[chat_id] = {
            "active": True,
            "ready": False,
            "page": None,
            "lock": asyncio.Lock(),
            "message_id": None,
            "task": None,
        }
        active_count += 1

    await update.message.reply_text("⏳ جاري تهيئة المتصفح المستمر...")
    task = asyncio.create_task(stream_worker(chat_id, context))

    async with streams_lock:
        if chat_id in streams:
            streams[chat_id]["task"] = task


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("🚫 غير مصرح لك باستخدام هذا البوت.")
        return

    chat_id = update.effective_chat.id

    async with streams_lock:
        global active_count
        session = streams.get(chat_id)
        if not session or not session.get("active"):
            await update.message.reply_text("❌ لا يوجد بث نشط حالياً.")
            return
        session["active"] = False
        task = session.get("task")
        active_count = max(0, active_count - 1)

    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await update.message.reply_text("⏹️ تم إيقاف البث وإغلاق المتصفح.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل الرسائل أثناء البث النشط ويرد بالنص"""
    if not is_allowed(update):
        await update.message.reply_text("🚫 غير مصرح لك باستخدام هذا البوت.")
        return

    chat_id = update.effective_chat.id
    text = update.message.text

    async with streams_lock:
        session = streams.get(chat_id)
        if not session or not session.get("active"):
            await update.message.reply_text("❌ ابدأ جلسة أولاً باستخدام /stream")
            return

        if not session.get("ready"):
            await update.message.reply_text("⏳ الجلسة ما زالت قيد التجهيز، جرّب بعد قليل.")
            return

        lock = session.get("lock")
        page = session.get("page")

    if not lock or not page:
        return

    async with lock:
        status_msg = None
        try:
            status_msg = await update.message.reply_text("⏳ جاري إرسال السؤال...")

            # إيجاد textarea
            textarea = None
            for sel in [
                'textarea[placeholder*="Message" i]',
                'textarea[class*="chat-input"]',
                'textarea',
                'div[contenteditable="true"]',
            ]:
                try:
                    textarea = page.locator(sel).first
                    await textarea.wait_for(state="visible", timeout=5000)
                    break
                except Exception:
                    continue

            if not textarea:
                await status_msg.edit_text("❌ لم أجد حقل الكتابة في الموقع.")
                return

            await textarea.fill(text)
            await asyncio.sleep(0.3)
            await textarea.press("Enter")

            await status_msg.edit_text("⏳ تم الإرسال! بانتظار الرد...")

            # ── استخراج الرد ──
            response = await extract_response(page, text, timeout_sec=120)

            try:
                await status_msg.delete()
            except Exception:
                pass

            if not response:
                await update.message.reply_text(
                    "⚠️ لم أتمكن من استخراج رد نصي من الموقع."
                )
                return

            # تنظيف نهائي
            cleaned = re.sub(r"\n?\d+\.?\d*\s*s?\s*\n?", "\n", response)
            cleaned = re.sub(r"\n?\d+\.?\d*\s*tok/s?\s*\n?", "\n", cleaned)
            cleaned = re.sub(r"\n?\d+\s*tokens?\s*\n?", "\n", cleaned)
            cleaned = cleaned.strip()

            # إرسال للمستخدم
            max_len = 4000
            if len(cleaned) <= max_len:
                await update.message.reply_text(cleaned)
            else:
                parts = [cleaned[i : i + max_len] for i in range(0, len(cleaned), max_len)]
                for part in parts:
                    await update.message.reply_text(part)

        except Exception as e:
            logger.exception("[handle_message] Error")
            if status_msg:
                try:
                    await status_msg.edit_text(f"⚠️ خطأ: {str(e)[:200]}")
                except Exception:
                    pass
            else:
                await update.message.reply_text(f"⚠️ خطأ: {str(e)[:200]}")


# ═══════════════════════════════════════════════════════════════
#  Stream Worker (المحرك الرئيسي)
# ═══════════════════════════════════════════════════════════════

async def stream_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    pw = None
    browser_ctx = None
    page = None

    try:
        pw = await async_playwright().start()

        user_data_dir = get_user_data_dir(chat_id)

        browser_ctx = await pw.chromium.launch_persistent_context(
            user_data_dir,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--no-first-run",
                "--no-zygote",
            ],
            viewport={"width": 960, "height": 540},
            locale="en-US",
        )

        page = await browser_ctx.new_page()

        # إخفاء automation
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            window.chrome = { runtime: {} };
        """)

        # ━━ فتح الموقع ━━
        await snap(page, context, chat_id, "🌐 جاري فتح المتصفح...", first=True)
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await snap(page, context, chat_id, "🌐 تم الوصول إلى الموقع")
        await asyncio.sleep(1.5)

        # إغلاق Popups
        for sel in ["button:has-text('Close')", "[aria-label='Close']", "button.close"]:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1500):
                    await loc.click()
                    await page.wait_for_timeout(400)
            except Exception:
                pass

        # ━━ تسجيل الدخول الذكي ━━
        if LOGIN_EMAIL and LOGIN_PASSWORD:
            await snap(page, context, chat_id, "🔍 التحقق من حالة الجلسة...")

            if await is_login_visible(page):
                await snap(page, context, chat_id, "🔐 تسجيل الدخول مطلوب...")
                try:
                    await perform_login(page)
                    await snap(page, context, chat_id, "✅ تم تسجيل الدخول!")
                except Exception as e:
                    logger.warning(f"Login error: {e}")
                    await snap(
                        page, context, chat_id,
                        f"⚠️ خطأ في الدخول: {str(e)[:100]}",
                    )
                    # إيقاف الجلسة إذا فشل الدخول
                    async with streams_lock:
                        global active_count
                        if chat_id in streams:
                            if streams[chat_id].get("active"):
                                active_count = max(0, active_count - 1)
                            streams[chat_id]["active"] = False
                    return
            else:
                await snap(page, context, chat_id, "✅ الجلسة محفوظة (مسجل مسبقاً)")

            # تحقق نهائي في /chat
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)

        # ━━ اختيار النموذج ━━
        if TARGET_MODEL:
            await snap(
                page, context, chat_id,
                f"🔽 اختيار النموذج: {TARGET_MODEL}...",
            )
            ok = await select_model(page, TARGET_MODEL)
            if ok:
                await snap(page, context, chat_id, f"✅ النموذج: {TARGET_MODEL}")
            else:
                await snap(page, context, chat_id, "ℹ️ لم يُعثر على قائمة النماذج")

        # ━━ جاهز للدردشة ━━
        async with streams_lock:
            if chat_id in streams and streams[chat_id].get("active"):
                streams[chat_id]["page"] = page
                streams[chat_id]["ready"] = True

        await snap(
            page, context, chat_id,
            "✅ جاهز! أرسل أي رسالة الآن.\nأرسل /stop لإيقاف البث.",
        )

        # ━━ حلقة البث المستمرة ━━
        while True:
            async with streams_lock:
                if chat_id not in streams or not streams[chat_id].get("active"):
                    break
            await snap(page, context, chat_id, f"📡 بث مباشر · يُحدّث كل {STREAM_INTERVAL}s")
            await asyncio.sleep(STREAM_INTERVAL)

    except asyncio.CancelledError:
        logger.info(f"[worker] Cancelled for {chat_id}")
        raise
    except Exception as e:
        logger.exception("Stream worker error")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ توقف البث: {str(e)[:300]}",
            )
        except Exception:
            pass
    finally:
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
            global active_count
            if chat_id in streams:
                if streams[chat_id].get("active"):
                    active_count = max(0, active_count - 1)
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

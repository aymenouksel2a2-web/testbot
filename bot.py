import os
import asyncio
import logging
import io
import re
import json
from typing import Any, Dict, List, Optional

from telegram import Update, InputMediaPhoto
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

# ═══════════════════════════════════════════════════════════════
#  إعدادات البيئة
# ═══════════════════════════════════════════════════════════════

TOKEN = os.environ.get("BOT_TOKEN")
URL = "https://gratisfy.xyz/chat"
PORT = int(os.environ.get("PORT", "8080"))
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

LOGIN_EMAIL = os.environ.get("LOGIN_EMAIL")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD")
TARGET_MODEL = os.environ.get("TARGET_MODEL", "Grok Uncensored")

STREAM_INTERVAL = 3
PERSISTENT_DIR = "/tmp/gratisfy-data"
MAX_MSG_LEN = 4000  # Telegram text limit safety margin

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
#  Markdown → Telegram HTML
# ═══════════════════════════════════════════════════════════════

def _html_esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _escape_md_inline(text: str) -> str:
    """يحول **bold** __italic__ ~~strike~~ و inline `code` داخل نص عادي"""
    placeholders: List[str] = []

    def store_tag(m, tag_name):
        inner = _html_esc(m.group(1))
        token = f"\x00{len(placeholders)}\x00"
        placeholders.append(f"<{tag_name}>{inner}</{tag_name}>")
        return token

    text = re.sub(r'\*\*(.+?)\*\*', lambda m: store_tag(m, 'b'), text)
    text = re.sub(r'__(.+?)__', lambda m: store_tag(m, 'i'), text)
    text = re.sub(r'~~(.+?)~~', lambda m: store_tag(m, 's'), text)

    # inline code
    parts = []
    pos = 0
    for m in re.finditer(r'`([^`]+)`', text):
        if m.start() > pos:
            parts.append(_escape_md_plain(text[pos:m.start()]))
        parts.append(f'<code>{_html_esc(m.group(1))}</code>')
        pos = m.end()
    if pos < len(text):
        parts.append(_escape_md_plain(text[pos:]))
    else:
        parts.append('')

    return ''.join(parts)


def _escape_md_plain(text: str) -> str:
    """يهرب HTML ويحول \n إلى <br>"""
    text = _html_esc(text)
    text = text.replace('\n', '<br>')
    return text


def md_to_tg_html(md: str) -> str:
    """يحوّل Markdown (fenced blocks + inline) إلى Telegram HTML"""
    result_parts: List[str] = []
    last_idx = 0

    # fenced code blocks: ```lang\ncode\n```
    for m in re.finditer(r'```([^\n]*)\n(.*?)\n?```', md, re.DOTALL):
        start, end = m.span()
        if start > last_idx:
            result_parts.append(_escape_md_inline(md[last_idx:start]))

        lang = m.group(1).strip()
        code = m.group(2)
        code_escaped = _html_esc(code)
        # Telegram <pre> alone makes monospace block; optional lang class
        result_parts.append(f'<pre><code>{code_escaped}</code></pre>')
        last_idx = end

    if last_idx < len(md):
        result_parts.append(_escape_md_inline(md[last_idx:]))

    return '\n\n'.join(result_parts)


async def deliver_response(update: Update, md_text: str):
    """يُنسّق Markdown ويُرسله للمستخدم (HTML)"""
    if not md_text or not md_text.strip():
        await update.message.reply_text("⚠️ لم أتمكن من استخراج رد نصي من الموقع.")
        return

    md_text = md_text.rstrip()

    if len(md_text) <= MAX_MSG_LEN:
        await update.message.reply_text(md_to_tg_html(md_text), parse_mode="HTML")
        return

    # تقسيم بذكاء عند الفقرات (Markdown blocks)
    blocks = md_text.split('\n\n')
    current_md = ""

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        if len(current_md) + len(block) + 2 <= MAX_MSG_LEN - 200:
            current_md += block + '\n\n'
        else:
            if current_md.strip():
                await update.message.reply_text(md_to_tg_html(current_md.strip()), parse_mode="HTML")
                await asyncio.sleep(0.3)

            # إذا كانت الكتلة وحيدة ضخمة جداً
            if len(block) > MAX_MSG_LEN:
                buf = io.BytesIO(block.encode('utf-8'))
                buf.name = "response.txt"
                await update.message.reply_document(
                    document=buf,
                    caption="📄 الكتلة طويلة جداً — أرسلتها كملف نصي."
                )
            else:
                current_md = block + '\n\n'

    if current_md.strip():
        await update.message.reply_text(md_to_tg_html(current_md.strip()), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════
#  Helpers الباقية
# ═══════════════════════════════════════════════════════════════

async def snap(
    page: Any,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    caption: str,
    first: bool = False,
):
    """يلتقط لقطة شاشة ويُحدّث الرسالة المصوّرة في نفس الرسالة"""
    try:
        screenshot = await page.screenshot(type="jpeg", quality=55)
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
            await context.bot.edit_message_media(
                chat_id=chat_id,
                message_id=msg_id,
                media=InputMediaPhoto(media=edit, caption=caption),
            )
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
                "button", has=re.compile(r"Grok|model", re.IGNORECASE)
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


# ═══════════════════════════════════════════════════════════════
#  Handlers
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Gratisfy Ultra* — بث مباشر + دردشة نصية\n\n"
        "📌 /stream — بدء جلسة جديدة (متصفح مستمر)\n"
        "📌 /stop  — إيقاف الجلسة\n\n"
        "⚡️ أرسل أي رسالة بعد بدء البث للحصول على رد نصي فوري.",
        parse_mode="Markdown",
    )


async def stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with streams_lock:
        if chat_id in streams and streams[chat_id].get("active"):
            await update.message.reply_text(
                "⚠️ هناك بث نشط بالفعل! أرسل /stop لإيقافه أولاً."
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

    await update.message.reply_text("⏳ جاري تهيئة المتصفح المستمر...")
    task = asyncio.create_task(stream_worker(chat_id, context))

    async with streams_lock:
        streams[chat_id]["task"] = task


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    async with streams_lock:
        session = streams.get(chat_id)
        if not session or not session.get("active"):
            await update.message.reply_text("❌ لا يوجد بث نشط حالياً.")
            return
        session["active"] = False
        task = session.get("task")

    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await update.message.reply_text("⏹️ تم إيقاف البث وإغلاق المتصفح.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل الرسائل أثناء البث النشط ويرد بالنص المنسّق"""
    chat_id = update.effective_chat.id
    text = update.message.text

    async with streams_lock:
        session = streams.get(chat_id)
        if not session or not session.get("active") or not session.get("ready"):
            return
        lock = session.get("lock")
        page = session.get("page")

    if not lock or not page:
        return

    async with lock:
        status_msg = None
        try:
            status_msg = await update.message.reply_text("⏳ جاري إرسال السؤال...")

            # ── إيجاد حقل الإدخال ──
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

            # ── نبدأ الاستماع على الشبكة قبل الضغط Enter ──
            response_promise = asyncio.create_task(
                page.wait_for_response(
                    lambda r: "/api/chat" in r.url and r.request.method == "POST",
                    timeout=120000,
                )
            )

            await textarea.press("Enter")
            await status_msg.edit_text("⏳ تم الإرسال! بانتظار الرد من الخادم...")

            # ── انتظر الـ Response ثم اقرأ الـ SSE كاملاً ──
            response = await response_promise
            sse_text = await response.text()

            # تحليل Server-Sent Events
            response_md = ""
            for line in sse_text.splitlines():
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                    choices = obj.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    if isinstance(content, str):
                        response_md += content
                except Exception:
                    continue

            try:
                await status_msg.delete()
            except Exception:
                pass

            # ── تنسيق وإرسال الرد للمستخدم ──
            await deliver_response(update, response_md)

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
                        page,
                        context,
                        chat_id,
                        f"⚠️ خطأ في الدخول: {str(e)[:100]}",
                    )
            else:
                await snap(page, context, chat_id, "✅ الجلسة محفوظة (مسجل مسبقاً)")

            # تحقق نهائي في /chat
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)

        # ━━ اختيار النموذج ━━
        if TARGET_MODEL:
            await snap(
                page,
                context,
                chat_id,
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
            page,
            context,
            chat_id,
            "✅ جاهز! أرسل أي رسالة الآن.\nأرسل /stop لإيقاف البث.",
        )

        # ━━ حلقة البث المستمرة ━━
        while True:
            async with streams_lock:
                if chat_id not in streams or not streams[chat_id].get("active"):
                    break
            await snap(page, context, chat_id, "📡 بث مباشر · يُحدّث كل 3s")
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

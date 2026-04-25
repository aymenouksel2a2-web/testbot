import os
import asyncio
import logging
import io
import re
import html
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
MAX_MSG_LEN = 4000  # حد تليجرام الافتراضي

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
#  تنظيف + تنسيق الردود
# ═══════════════════════════════════════════════════════════════

def clean_text(text: str) -> str:
    """تنظيف UI lines مع الحفاظ على leading whitespace للكود"""
    if not text:
        return ""

    ui_line_patterns = [
        r'Enter(\s+to\s+send)?',
        r'Shift\s*\+\s*Enter',
        r'Ctrl[/\\]?Cmd\s*\+\s*V',
        r'paste\s*attachment(\s*[·•])?',
        r'attach\s*file?(\s*[·•])?',
        r'to\s+send(\s*[·•])?',
        r'for\s+new\s+line(\s*[·•])?',
        r'record(\s*[·•])?',
        r'Message\s*Grok',
        r'Start\s+a\s+conversation',
        r'Select\s+a\s+model',
        r'Settings?',
        r'^Gratisfy$',
        r'Send\s*message',
        r'Attach',
        r'Paper\s*clip',
        r'Mic(rophone)?',
        r'new\s*line',
        r'\d+\.?\d*\s*s(ec)?',
        r'\d+\.?\d*\s*tok[/\\]s?',
        r'\d+\s*tokens?',
        r'Thinking',
        r'Stop\s*generating',
        r'Regenerate',
        r'Copy',
        r'Like',
        r'Dislike',
        r'Share',
        r'Export',
        r'Web\s*search',
        r'Reason',
    ]

    lines = text.splitlines()
    cleaned_lines: List[str] = []

    for raw in lines:
        # ❌ لا نستخدم .strip() العمياء!
        # نحتفظ بالـ leading spaces (indentation) ونزيل trailing فقط
        line = raw.rstrip()

        stripped_for_check = line.strip()
        if not stripped_for_check:
            # نحتفظ بالأسطر الفارغة داخل الـ text لكنها ستُراجع لاحقاً
            cleaned_lines.append(line)
            continue

        is_junk = False
        for pat in ui_line_patterns:
            if re.fullmatch(rf'\s*{pat}\s*', stripped_for_check, re.IGNORECASE):
                is_junk = True
                break

        if is_junk:
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def _looks_like_code_line(line: str) -> bool:
    """يحدد ما إذا كان السطر جزءاً من كود برمجي"""
    if not line:
        return False

    leading = len(line) - len(line.lstrip())
    s = line.lstrip()

    # indentation واضح (tab أو 4+ مسافات أو 2+ مع رموز برمجية)
    if line.startswith('\t'):
        return True
    if leading >= 4:
        return True
    if leading >= 2 and any(ch in s for ch in '=[{()}:;.,|&+-*/%<>!'):
        return True

    # كلمات / أنماط مفتاحية
    code_signatures = [
        r'^import\s', r'^from\s+\S+\s+import', r'^def\s', r'^class\s',
        r'^return\b', r'^if\b', r'^elif\b', r'^else\b', r'^for\b',
        r'^while\b', r'^with\b', r'^try\b', r'^except\b', r'^finally\b',
        r'^print\s*\(', r'^console\.', r'^const\b', r'^let\b', r'^var\b',
        r'^function\b', r'^#\s', r'^//\s', r'^/\*', r'^\*/',
        r'^echo\b', r'^<\?php', r'^<\?', r'^<html', r'^<div', r'^<span', r'^<script',
        r'^npm\s', r'^yarn\s', r'^pip\s', r'^git\s', r'^curl\s',
        r'^python\s', r'^node\s', r'^bash\s', r'^```',
    ]

    for sig in code_signatures:
        if re.search(sig, s, re.IGNORECASE):
            return True

    return False


def format_as_telegram_html(text: str) -> str:
    """يحول النص إلى HTML مع تغليف الكود تلقائياً في <pre><code>"""
    lines = text.splitlines()
    out_segments: List[tuple[str, str]] = []  # (type, content)

    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if _looks_like_code_line(line):
            code_lines: List[str] = [line]
            i += 1
            while i < n:
                nxt = lines[i]
                if _looks_like_code_line(nxt):
                    code_lines.append(nxt)
                    i += 1
                elif nxt.strip() == '':
                    # سطر فارغ داخل الكود: استمر إذا كان السطر اللاحق كود أو فارغ أيضاً
                    if i + 1 < n and (_looks_like_code_line(lines[i + 1]) or lines[i + 1].strip() == ''):
                        code_lines.append(nxt)
                        i += 1
                    else:
                        break
                else:
                    break

            # إزالة الأسطر الفارغة من أطراف الكتلة فقط
            while code_lines and code_lines[0].strip() == '':
                code_lines.pop(0)
            while code_lines and code_lines[-1].strip() == '':
                code_lines.pop()

            if code_lines:
                body = html.escape('\n'.join(code_lines))
                out_segments.append(('code', f'<pre><code>{body}</code></pre>'))
        else:
            if line.strip() == '':
                out_segments.append(('text', ''))
            else:
                out_segments.append(('text', html.escape(line)))
            i += 1

    # دمج النص العادي المتتالي بـ <br>
    final_parts: List[str] = []
    text_buffer: List[str] = []

    for seg_type, seg_content in out_segments:
        if seg_type == 'code':
            if text_buffer:
                final_parts.append('<br>\n'.join(text_buffer))
                text_buffer = []
            final_parts.append(seg_content)
        else:
            text_buffer.append(seg_content)

    if text_buffer:
        final_parts.append('<br>\n'.join(text_buffer))

    return '\n\n'.join(final_parts)


async def deliver_response(update: Update, text: str):
    """تنسيق الرد وإرساله للمستخدم (HTML قصير أو ملف طويل)"""
    # 1. تنظيف نهائي قوي
    cleaned = clean_text(text)
    cleaned = re.sub(r'\n?\d+\.?\d*\s*s(?:ec)?\s*\n?', '\n', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n?\d+\.?\d*\s*tok[/\\]?s?\s*\n?', '\n', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n?\d+\s*tokens?\s*\n?', '\n', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip('\n')

    if not cleaned:
        await update.message.reply_text("⚠️ لم أتمكن من استخراج رد نصي من الموقع.")
        return

    html_body = format_as_telegram_html(cleaned)

    # إذا كان أقل من الحد، أرسله دفعة واحدة
    if len(html_body) <= MAX_MSG_LEN:
        await update.message.reply_text(html_body, parse_mode="HTML")
        return

    # تقسيم بذكاء عند الفقرات
    blocks = cleaned.split('\n\n')
    current_html = ""

    for block in blocks:
        if not block.strip():
            continue

        block_html = format_as_telegram_html(block)
        if len(current_html) + len(block_html) + 2 <= MAX_MSG_LEN:
            current_html += block_html + '\n\n'
        else:
            if current_html.strip():
                await update.message.reply_text(current_html.strip(), parse_mode="HTML")
                await asyncio.sleep(0.3)

            # إذا كان block وحده ضخماً، أرسل كملف نصي ليحافظ على التنسيق
            if len(block_html) > MAX_MSG_LEN:
                buf = io.BytesIO(block.encode('utf-8'))
                buf.name = "response.txt"
                await update.message.reply_document(
                    document=buf,
                    caption="📄 الكتلة طويلة جداً — أرسلتها كملف نصي."
                )
                current_html = ""
            else:
                current_html = block_html + '\n\n'

    if current_html.strip():
        await update.message.reply_text(current_html.strip(), parse_mode="HTML")


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


async def extract_response(
    page: Any,
    user_text: str,
    pre_text: Optional[str] = None,
    timeout_sec: int = 120,
) -> str:
    """يستخرج رد البوت باستخدام الفرق بين innerText قبل وبعد الإرسال"""
    start = asyncio.get_event_loop().time()
    last_text = ""
    stable = 0

    while (asyncio.get_event_loop().time() - start) < timeout_sec:
        js_result = await page.evaluate(
            """({ user, pre }) => {
                const raw = document.body.innerText || '';
                let candidate = '';

                if (pre && raw.length > pre.length && raw.startsWith(pre)) {
                    candidate = raw.substring(pre.length);
                } else {
                    const idx = raw.lastIndexOf(user);
                    if (idx !== -1) {
                        candidate = raw.substring(idx + user.length);
                    }
                }

                if (candidate.startsWith(user)) {
                    candidate = candidate.substring(user.length);
                }

                return candidate;
            }""",
            {"user": user_text, "pre": pre_text or ""},
        )

        current = (js_result or "").strip()

        if current:
            if current == last_text:
                stable += 1
                if stable >= 2:
                    return current
            else:
                stable = 0
                last_text = current

        await asyncio.sleep(1.5)

    return last_text


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

            # ── التقاط حالة الصفحة قبل الإرسال ──
            pre_inner = await page.evaluate("() => document.body.innerText || ''")

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
            await textarea.press("Enter")

            await status_msg.edit_text("⏳ تم الإرسال! بانتظار الرد...")

            # ── استخراج الرد ──
            response = await extract_response(
                page, text, pre_text=pre_inner, timeout_sec=120
            )

            try:
                await status_msg.delete()
            except Exception:
                pass

            # ── تنسيق وإرسال الرد للمستخدم ──
            await deliver_response(update, response)

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

        # ━━ متصفح مستمر (يحفظ الجلسة إلى الأبد) ━━
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

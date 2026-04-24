import asyncio
import io
import logging
import os
import re
from contextlib import suppress
from typing import Any, Dict, List, Optional, Sequence

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright
from telegram import InputMediaPhoto, Message, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  إعدادات البيئة
# ═══════════════════════════════════════════════════════════════

def env_int(name: str, default: int, minimum: Optional[int] = None) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        number = int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default
    if minimum is not None and number < minimum:
        logger.warning("%s=%s is below %s; using %s", name, number, minimum, default)
        return default
    return number


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


TOKEN = os.environ.get("BOT_TOKEN")
URL = os.environ.get("GRATISFY_URL", "https://gratisfy.xyz/chat")
PORT = env_int("PORT", 8080, minimum=1)
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

LOGIN_EMAIL = os.environ.get("LOGIN_EMAIL")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD")
TARGET_MODEL = os.environ.get("TARGET_MODEL", "Grok Uncensored")

STREAM_INTERVAL = env_int("STREAM_INTERVAL", 3, minimum=2)
PERSISTENT_DIR = os.environ.get("PERSISTENT_DIR", "/tmp/gratisfy-data")
HEADLESS = env_bool("HEADLESS", True)
VIEWPORT_WIDTH = env_int("VIEWPORT_WIDTH", 960, minimum=320)
VIEWPORT_HEIGHT = env_int("VIEWPORT_HEIGHT", 540, minimum=240)

MAX_TELEGRAM_TEXT = 4000
MAX_TELEGRAM_CAPTION = 1024


# ═══════════════════════════════════════════════════════════════
#  بنية البيانات
# ═══════════════════════════════════════════════════════════════

streams: Dict[int, Dict[str, Any]] = {}
streams_lock: Optional[asyncio.Lock] = None


async def get_streams_lock() -> asyncio.Lock:
    """يعيد قفل الجلسات، وينشئه عند الحاجة لتجنب NoneType أثناء التشغيل."""
    global streams_lock
    if streams_lock is None:
        streams_lock = asyncio.Lock()
    return streams_lock


async def post_init(app: Application) -> None:
    await get_streams_lock()
    logger.info("✅ Bot initialized")


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

UI_JUNK_EXACT = {
    "copy",
    "like",
    "dislike",
    "share",
    "export",
    "attach",
    "paperclip",
    "mic",
    "settings",
    "regenerate",
    "stop generating",
    "send message",
    "select a model",
    "start a conversation",
    "web search",
    "reason",
    "gratisfy",
    "new chat",
    "login",
    "log in",
    "sign in",
}

UI_JUNK_CONTAINS = (
    "enter to send",
    "shift + enter",
    "ctrl/cmd + v",
    "paste attachment",
    "attach file",
    "message grok",
    "for new line",
    "to send",
    "new line",
    "record",
)

METRIC_PATTERNS = (
    re.compile(r"^\d+(?:\.\d+)?\s*s$", re.IGNORECASE),
    re.compile(r"^\d+(?:\.\d+)?\s*tok/s$", re.IGNORECASE),
    re.compile(r"^\d+\s*tokens?$", re.IGNORECASE),
    re.compile(r"^\d+(?:\.\d+)?\s*s\s+\d+(?:\.\d+)?\s*tok/s\s+\d+\s*tokens?$", re.IGNORECASE),
    re.compile(r"^\d+(?:\.\d+)?\s*s.*?tok/s.*?tokens?$", re.IGNORECASE),
)


def normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_metric_line(line: str) -> bool:
    return any(pattern.match(line) for pattern in METRIC_PATTERNS)


def filter_ui_lines(lines: Sequence[str]) -> List[str]:
    cleaned: List[str] = []
    for raw_line in lines:
        line = normalize_line(raw_line)
        if not line:
            continue
        low = line.lower()
        if low in UI_JUNK_EXACT:
            continue
        if any(fragment in low for fragment in UI_JUNK_CONTAINS):
            continue
        if is_metric_line(line):
            continue
        cleaned.append(line)
    return cleaned


def clean_response_text(text: str) -> str:
    """تنظيف آمن لا يحذف الأرقام الموجودة داخل الردود الحقيقية."""
    if not text:
        return ""
    lines = filter_ui_lines(text.splitlines())

    # إزالة التكرارات المتجاورة فقط؛ لا نلمس التكرارات المقصودة في محتوى الرد.
    compact: List[str] = []
    for line in lines:
        if not compact or compact[-1] != line:
            compact.append(line)
    return "\n".join(compact).strip()


def split_telegram_text(text: str, limit: int = MAX_TELEGRAM_TEXT) -> List[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current = ""
    for paragraph in text.split("\n"):
        addition = paragraph if not current else f"\n{paragraph}"
        if len(current) + len(addition) <= limit:
            current += addition
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


async def send_long_text(message: Message, text: str) -> None:
    chunks = split_telegram_text(text)
    if not chunks:
        await message.reply_text("⚠️ الرد فارغ بعد التنظيف.")
        return
    for chunk in chunks:
        await message.reply_text(chunk)


async def edit_status(message: Optional[Message], text: str) -> None:
    if not message:
        return
    with suppress(TelegramError):
        await message.edit_text(text)


async def get_visible_text_snapshot(page: Any) -> Dict[str, List[str]]:
    """يجمع النص الظاهر بأكثر من طريقة.

    بعض صفحات Gratisfy لا تُرتب رسائل الدردشة في document.body.innerText بنفس
    ترتيب ظهورها في الشاشة، وأحياناً تكون الرسالة الجديدة داخل عنصر منفصل لا يظهر
    جيداً عند الاعتماد على body فقط. لذلك نجمع:
    1) أسطر body.innerText.
    2) كتل نصية من العناصر المرئية ذات الصلة بالدردشة.
    """
    try:
        result = await page.evaluate(
            """() => {
                const normalize = (value) => (value || '')
                    .replace(/\u00a0/g, ' ')
                    .replace(/[ \t]+/g, ' ')
                    .trim();

                const visible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) {
                        return false;
                    }
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };

                const bodyText = document.body?.innerText || '';
                const bodyLines = bodyText
                    .split('\n')
                    .map(normalize)
                    .filter(Boolean);

                const selectors = [
                    'article',
                    '[role="article"]',
                    '[data-message-author-role]',
                    '[data-testid*="message" i]',
                    '[class*="message" i]',
                    '[class*="chat" i]',
                    '[class*="markdown" i]',
                    '[class*="prose" i]',
                    'main p', 'main li', 'main pre', 'main code', 'main blockquote',
                    'main div', 'main span'
                ].join(',');

                const seen = new Set();
                const blocks = [];
                for (const el of document.querySelectorAll(selectors)) {
                    if (!visible(el)) continue;
                    const text = normalize(el.innerText || el.textContent || '');
                    if (!text || text.length < 2 || text.length > 5000) continue;
                    if (seen.has(text)) continue;
                    seen.add(text);
                    blocks.push(text);
                    if (blocks.length >= 350) break;
                }

                return { bodyLines, blocks };
            }"""
        )
    except Exception as exc:
        logger.warning("Failed to read visible page text: %s", exc)
        return {"bodyLines": [], "blocks": []}

    if not isinstance(result, dict):
        return {"bodyLines": [], "blocks": []}

    body_lines = result.get("bodyLines") if isinstance(result.get("bodyLines"), list) else []
    blocks = result.get("blocks") if isinstance(result.get("blocks"), list) else []
    return {
        "bodyLines": [str(item) for item in body_lines],
        "blocks": [str(item) for item in blocks],
    }


async def get_visible_lines(page: Any) -> List[str]:
    snapshot = await get_visible_text_snapshot(page)
    return snapshot.get("bodyLines", [])


async def wait_for_any_visible(
    page: Any,
    selectors: Sequence[str],
    timeout: int = 5000,
):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=timeout)
            return locator
        except Exception:
            continue
    return None


async def snap(
    page: Any,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    caption: str,
    first: bool = False,
) -> None:
    """يلتقط لقطة شاشة ويحدّث رسالة الصورة نفسها قدر الإمكان."""
    try:
        screenshot = await page.screenshot(type="jpeg", quality=60, full_page=False)
    except Exception as exc:
        logger.warning("[snap] Could not take screenshot: %s", exc)
        return

    caption = caption[:MAX_TELEGRAM_CAPTION]
    lock = await get_streams_lock()

    async with lock:
        session = streams.get(chat_id)
        if not session:
            return
        msg_id = session.get("message_id")

    try:
        if first or msg_id is None:
            photo = io.BytesIO(screenshot)
            photo.name = "stream.jpg"
            sent = await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
            )
            async with lock:
                if chat_id in streams:
                    streams[chat_id]["message_id"] = sent.message_id
        else:
            photo = io.BytesIO(screenshot)
            photo.name = "stream.jpg"
            await context.bot.edit_message_media(
                chat_id=chat_id,
                message_id=msg_id,
                media=InputMediaPhoto(media=photo, caption=caption),
            )
    except BadRequest as exc:
        # Telegram يعيد هذه الرسالة عند محاولة نشر نفس الصورة/الكابشن.
        if "not modified" not in str(exc).lower():
            logger.warning("[snap] BadRequest: %s", exc)
    except TelegramError as exc:
        logger.warning("[snap] Telegram error: %s", exc)
    except Exception as exc:
        logger.warning("[snap] Error: %s", exc)


async def is_login_visible(page: Any) -> bool:
    """يتحقق هل يوجد زر تسجيل دخول ظاهر."""
    selectors = [
        'button:has-text("Log in")',
        'a:has-text("Log in")',
        'button:has-text("Login")',
        'a:has-text("Login")',
        'button:has-text("Sign in")',
        'a:has-text("Sign in")',
        '[data-testid="login-button"]',
        'header button:has-text("Log in")',
        'header a:has-text("Log in")',
    ]
    return await wait_for_any_visible(page, selectors, timeout=1500) is not None


async def perform_login(page: Any) -> None:
    """ينفذ تسجيل الدخول عند ظهور النموذج."""
    if not LOGIN_EMAIL or not LOGIN_PASSWORD:
        raise RuntimeError("LOGIN_EMAIL و LOGIN_PASSWORD غير مضبوطين")

    login_btn = await wait_for_any_visible(
        page,
        [
            'button:has-text("Log in")',
            'a:has-text("Log in")',
            'button:has-text("Login")',
            'a:has-text("Login")',
            'button:has-text("Sign in")',
            'a:has-text("Sign in")',
            '[data-testid="login-button"]',
        ],
        timeout=5000,
    )
    if not login_btn:
        raise RuntimeError("لم يتم العثور على زر تسجيل الدخول")

    await login_btn.click()
    await page.wait_for_timeout(1200)

    email_in = await wait_for_any_visible(
        page,
        [
            'input[name="email"]',
            'input[type="email"]',
            'input[id="email"]',
            'input[placeholder*="email" i]',
            'input[autocomplete="email"]',
        ],
        timeout=8000,
    )
    if not email_in:
        raise RuntimeError("لم يُعثر على حقل البريد")
    await email_in.fill(LOGIN_EMAIL)

    pass_in = await wait_for_any_visible(
        page,
        [
            'input[name="password"]',
            'input[type="password"]',
            'input[id="password"]',
            'input[placeholder*="password" i]',
            'input[autocomplete="current-password"]',
        ],
        timeout=8000,
    )
    if not pass_in:
        raise RuntimeError("لم يُعثر على حقل كلمة المرور")
    await pass_in.fill(LOGIN_PASSWORD)

    # جرّب زر الإرسال أولاً، ثم Enter كحل احتياطي.
    submit_selectors = [
        'button[type="submit"]',
        'button:has-text("Submit")',
        'button:has-text("Sign in")',
        'button:has-text("Login")',
        'button:has-text("Continue")',
    ]
    submitted = False
    for selector in submit_selectors:
        with suppress(Exception):
            button = page.locator(selector).first
            if await button.is_visible(timeout=800) and await button.is_enabled(timeout=800):
                await button.click(timeout=3000)
                submitted = True
                break

    if not submitted:
        await pass_in.press("Enter")

    with suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=15000)
    await page.wait_for_timeout(2000)


async def select_model(page: Any, model_name: str) -> bool:
    """يحاول اختيار النموذج من القائمة المنسدلة، ويعود False إذا لم يجدها."""
    model_name = model_name.strip()
    if not model_name:
        return False

    trigger = await wait_for_any_visible(
        page,
        [
            '[data-testid="model-selector"]',
            'button[class*="model-selector"]',
            '[aria-haspopup="listbox"]',
            'button:has([class*="chevron"])',
            'button:has-text("Model")',
        ],
        timeout=4000,
    )

    if not trigger:
        try:
            trigger = page.locator(
                "button",
                has_text=re.compile(r"grok|model|select", re.IGNORECASE),
            ).first
            await trigger.wait_for(state="visible", timeout=3000)
        except Exception:
            return False

    try:
        await trigger.click()
        await page.wait_for_timeout(800)
    except Exception:
        return False

    search_in = await wait_for_any_visible(
        page,
        [
            'input[placeholder*="Search" i]',
            '[role="searchbox"]',
            'input[type="search"]',
            'input[type="text"]',
        ],
        timeout=4000,
    )

    if search_in:
        with suppress(Exception):
            await search_in.fill(model_name)
            await page.wait_for_timeout(700)

    option = None
    for selector in [
        '[role="option"]',
        'li',
        'button',
        'div',
    ]:
        try:
            option = page.locator(selector, has_text=model_name).first
            await option.wait_for(state="visible", timeout=2500)
            break
        except Exception:
            option = None

    try:
        if option:
            await option.click(timeout=3000)
        elif search_in:
            await search_in.press("Enter")
        else:
            return False
        await page.wait_for_timeout(1000)
        return True
    except Exception as exc:
        logger.warning("Model selection failed: %s", exc)
        return False


async def find_chat_input(page: Any):
    selectors = [
        'textarea[placeholder*="Message" i]',
        'textarea[class*="chat-input"]',
        'textarea',
        'div[contenteditable="true"]',
        '[role="textbox"]',
    ]
    locator = await wait_for_any_visible(page, selectors, timeout=5000)
    if locator:
        return locator

    with suppress(Exception):
        textbox = page.get_by_role("textbox").last
        await textbox.wait_for(state="visible", timeout=3000)
        return textbox
    return None


async def submit_prompt(page: Any, text: str) -> None:
    input_box = await find_chat_input(page)
    if not input_box:
        raise RuntimeError("لم أجد حقل الكتابة في الموقع")

    await input_box.click(timeout=5000)
    try:
        await input_box.fill(text, timeout=7000)
    except Exception:
        # حل احتياطي للـ contenteditable أو الحقول غير القياسية.
        with suppress(Exception):
            await page.keyboard.press("Control+A")
        await page.keyboard.type(text, delay=0)

    await page.wait_for_timeout(300)

    # بعض الواجهات تعتمد زر إرسال، وبعضها يعتمد Enter.
    send_selectors = [
        '[data-testid="send-button"]',
        'button[aria-label*="Send" i]',
        'button:has-text("Send")',
        'button[type="submit"]',
    ]
    for selector in send_selectors:
        with suppress(Exception):
            button = page.locator(selector).last
            if await button.is_visible(timeout=600) and await button.is_enabled(timeout=600):
                await button.click(timeout=3000)
                return

    await input_box.press("Enter")


def is_user_echo(line: str, user_text: str) -> bool:
    line_norm = normalize_line(line)
    user_norm = normalize_line(user_text)
    if not line_norm or not user_norm:
        return False
    if line_norm == user_norm:
        return True
    # لا نحذف الأسطر القصيرة بالاحتواء حتى لا نحذف رداً مثل "hello" بالخطأ.
    if len(user_norm) >= 12 and user_norm in line_norm:
        return True
    return False


def is_model_or_header_line(line: str) -> bool:
    """يزيل عناوين البطاقات مثل Navy · Grok Uncensored دون حذف الرد نفسه."""
    value = normalize_line(line)
    low = value.lower()
    target = normalize_line(TARGET_MODEL or "").lower()

    if not value:
        return True
    if low in UI_JUNK_EXACT:
        return True
    if any(fragment in low for fragment in UI_JUNK_CONTAINS):
        return True
    if is_metric_line(value):
        return True

    # أمثلة ظاهرة في الصورة: "Navy: Grok Uncensored" أو اسم النموذج فقط.
    if target and target in low and len(value) <= 90:
        return True
    if re.match(r"^(navy|model|assistant|bot|ai)\s*[:·-]", low) and len(value) <= 90:
        return True
    if low.startswith("grok") and len(value) <= 90:
        return True

    # أزرار أو تسميات قصيرة في الواجهة.
    if len(value) <= 2 and not re.search(r"[\w\u0600-\u06FF]", value):
        return True

    return False


def strip_inline_ui_fragments(line: str, user_text: str) -> str:
    """ينظف السطر عندما يجمع الموقع عدة عناصر في سطر واحد."""
    value = normalize_line(line)
    if not value:
        return ""

    user_norm = normalize_line(user_text)
    if user_norm and value == user_norm:
        return ""

    # إذا دمج العنصر فقاعة المستخدم مع الرد، احذف صدى السؤال من بداية السطر فقط.
    if user_norm and value.lower().startswith(user_norm.lower() + " "):
        value = value[len(user_norm) :].strip()

    # احذف عنوان النموذج من بداية السطر فقط، ولا تحذف ذكر النموذج داخل الرد.
    target = normalize_line(TARGET_MODEL or "")
    if target:
        target_re = re.escape(target)
        value = re.sub(
            rf"^(?:navy|model|assistant|bot|ai)?\s*[:·-]?\s*{target_re}\s*",
            "",
            value,
            flags=re.IGNORECASE,
        ).strip()

    value = re.sub(
        r"\s+\d+(?:\.\d+)?\s*s\s+\d+(?:\.\d+)?\s*tok/s\s+\d+\s*tokens?\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    value = re.sub(
        r"\s+\d+(?:\.\d+)?\s*s.*?tok/s.*?tokens?\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    return value


def response_lines_from_text(text: str, user_text: str) -> List[str]:
    lines = []
    for raw in str(text or "").splitlines():
        line = strip_inline_ui_fragments(raw, user_text)
        if not line:
            continue
        if is_user_echo(line, user_text):
            continue
        if is_model_or_header_line(line):
            continue
        lines.append(line)
    return lines


def clean_candidate_text(text: str, user_text: str) -> str:
    lines = response_lines_from_text(text, user_text)

    compact: List[str] = []
    for line in lines:
        if compact and compact[-1] == line:
            continue
        compact.append(line)

    return "\n".join(compact).strip()


def normalize_block_for_compare(value: str) -> str:
    return normalize_line(value).lower()


def find_new_line_candidates(
    current_lines: Sequence[str],
    before_lines: Sequence[str],
    user_text: str,
) -> str:
    """يستخرج الأسطر الجديدة حتى إذا كان ترتيب DOM مختلفاً عن ترتيب الشاشة."""
    before_set = {normalize_block_for_compare(line) for line in before_lines}
    user_norm = normalize_block_for_compare(user_text)

    new_lines: List[str] = []
    for line in current_lines:
        key = normalize_block_for_compare(line)
        if not key or key in before_set or key == user_norm:
            continue
        if is_user_echo(line, user_text) or is_model_or_header_line(line):
            continue
        new_lines.append(normalize_line(line))

    return clean_candidate_text("\n".join(new_lines), user_text)


def find_after_user_candidate(current_lines: Sequence[str], user_text: str) -> str:
    """حل احتياطي: اقرأ ما بعد رسالة المستخدم إذا كان ترتيب DOM صحيحاً."""
    filtered = [line for line in current_lines if not is_model_or_header_line(line)]
    user_index = -1
    for index, line in enumerate(filtered):
        if is_user_echo(line, user_text):
            user_index = index

    if user_index >= 0 and user_index < len(filtered) - 1:
        return clean_candidate_text("\n".join(filtered[user_index + 1 :]), user_text)
    return ""


def block_candidate_score(candidate: str, block: str, before_blocks: Sequence[str]) -> int:
    if not candidate:
        return -1
    block_key = normalize_block_for_compare(block)
    before_keys = {normalize_block_for_compare(item) for item in before_blocks}
    score = len(candidate)
    if block_key not in before_keys:
        score += 250
    # الردود الحقيقية غالباً تحتوي مسافات/جمل، أما عناصر الواجهة تكون قصيرة جداً.
    if " " in candidate or "\n" in candidate:
        score += 40
    return score


def build_response_candidate(
    current_snapshot: Dict[str, List[str]] | Sequence[str],
    user_text: str,
    before_snapshot: Optional[Dict[str, List[str]] | Sequence[str]] = None,
) -> str:
    """يبني أفضل رد محتمل من الصفحة.

    الإصلاح الأساسي هنا أن الرد لا يُشترط أن يأتي بعد رسالة المستخدم في innerText.
    في Gratisfy قد تظهر بطاقة الرد قبل/بعد فقاعة المستخدم داخل DOM، لذلك نبحث عن
    النصوص الجديدة في الصفحة ونقارنها بما كان ظاهراً قبل الإرسال.
    """
    if isinstance(current_snapshot, dict):
        current_lines = current_snapshot.get("bodyLines", [])
        current_blocks = current_snapshot.get("blocks", [])
    else:
        current_lines = list(current_snapshot)
        current_blocks = []

    if isinstance(before_snapshot, dict):
        before_lines = before_snapshot.get("bodyLines", [])
        before_blocks = before_snapshot.get("blocks", [])
    else:
        before_lines = list(before_snapshot or [])
        before_blocks = []

    candidates: List[tuple[int, str]] = []

    # 1) أفضل مسار: العناصر/البلوكات الجديدة، لأنها غالباً تمثل فقاعة الرد نفسها.
    for block in current_blocks:
        candidate = clean_candidate_text(block, user_text)
        score = block_candidate_score(candidate, block, before_blocks)
        if score >= 0:
            candidates.append((score, candidate))

    # 2) الأسطر الجديدة في body، وهذا يحل حالة ظهور الرد قبل المستخدم في DOM.
    new_lines_candidate = find_new_line_candidates(current_lines, before_lines, user_text)
    if new_lines_candidate:
        candidates.append((len(new_lines_candidate) + 220, new_lines_candidate))

    # 3) حل احتياطي قديم: ما بعد رسالة المستخدم.
    after_user_candidate = find_after_user_candidate(current_lines, user_text)
    if after_user_candidate:
        candidates.append((len(after_user_candidate) + 120, after_user_candidate))

    if not candidates:
        return ""

    # أحياناً يكون هناك عنصر أب يحتوي الصفحة كلها، فيكون طويلاً جداً ومليئاً بالواجهة.
    # اختر أعلى نتيجة، لكن فضّل المرشح الأقصر إذا كان يحتوي المرشح الأطول بالكامل تقريباً.
    candidates.sort(key=lambda item: item[0], reverse=True)
    best = candidates[0][1]
    for _, candidate in candidates[1:8]:
        if candidate and candidate in best and len(candidate) >= 3:
            # إذا كان المرشح المختصر ليس مجرد كلمة قصيرة، فهو غالباً نص فقاعة الرد.
            if len(candidate) >= 8 or " " in candidate or "\n" in candidate:
                best = candidate
                break

    return best.strip()


async def extract_response(
    page: Any,
    user_text: str,
    before_lines: Optional[Sequence[str] | Dict[str, List[str]]] = None,
    timeout_sec: int = 120,
) -> str:
    """ينتظر الرد ثم يعيده كنص.

    كان التعليق يحدث لأن الكود ينتظر استقرار innerText كاملاً، بينما الموقع يغير
    سطوراً مثل الوقت/سرعة التوكنات أو يرتب الرسائل بطريقة لا تجعل الرد بعد سؤال
    المستخدم. الآن نرسل الرد عند استقراره مرتين، أو بعد مهلة قصيرة من ظهور أول
    مرشح صالح حتى لا يبقى البوت معلقاً.
    """
    start = asyncio.get_running_loop().time()
    first_seen_at: Optional[float] = None
    last_text = ""
    stable_count = 0

    # مهلة قصيرة بعد ظهور الرد: تمنع التعليق إذا بقيت الواجهة تغير أرقام التوليد.
    return_after_first_seen = env_int("RETURN_AFTER_FIRST_SEEN", 8, minimum=2)
    poll_interval = 1.25

    while (asyncio.get_running_loop().time() - start) < timeout_sec:
        snapshot = await get_visible_text_snapshot(page)
        current_text = build_response_candidate(snapshot, user_text, before_lines)
        now = asyncio.get_running_loop().time()

        if current_text:
            if first_seen_at is None:
                first_seen_at = now
                logger.info("First response candidate detected: %s", current_text[:120])

            if current_text == last_text:
                stable_count += 1
                if stable_count >= 2:
                    return current_text
            else:
                stable_count = 0
                last_text = current_text

            if first_seen_at and (now - first_seen_at) >= return_after_first_seen:
                logger.info("Returning response candidate after grace period")
                return last_text.strip()

        await asyncio.sleep(poll_interval)

    return last_text.strip()


# ═══════════════════════════════════════════════════════════════
#  Handlers
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "🤖 *Gratisfy Ultra* — بث مباشر + دردشة نصية\n\n"
        "📌 /stream — بدء جلسة جديدة \n"
        "📌 /stop  — إيقاف الجلسة\n\n"
        "⚡️ أرسل أي رسالة بعد بدء البث للحصول على رد نصي.",
        parse_mode="Markdown",
    )


async def stream(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    lock = await get_streams_lock()

    async with lock:
        old_session = streams.get(chat_id)
        if old_session and old_session.get("active"):
            await update.message.reply_text(
                "⚠️ هناك بث نشط بالفعل. أرسل /stop لإيقافه أولاً."
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

    await update.message.reply_text("⏳ جاري تهيئة المتصفح...")
    task = asyncio.create_task(stream_worker(chat_id, context))

    async with lock:
        if chat_id in streams:
            streams[chat_id]["task"] = task


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    lock = await get_streams_lock()

    async with lock:
        session = streams.get(chat_id)
        if not session or not session.get("active"):
            await update.message.reply_text("❌ لا يوجد بث نشط حالياً.")
            return
        session["active"] = False
        task = session.get("task")

    if task and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    await update.message.reply_text("⏹️ تم إيقاف البث وإغلاق المتصفح.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """يستقبل الرسائل أثناء البث النشط ويرد بالنص."""
    if not update.message or not update.effective_chat:
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    chat_id = update.effective_chat.id
    streams_guard = await get_streams_lock()

    async with streams_guard:
        session = streams.get(chat_id)
        if not session or not session.get("active") or not session.get("ready"):
            return
        session_lock = session.get("lock")
        page = session.get("page")

    if not session_lock or not page:
        return

    async with session_lock:
        status_msg: Optional[Message] = None
        try:
            status_msg = await update.message.reply_text("⏳ جاري إرسال السؤال...")
            before_snapshot = await get_visible_text_snapshot(page)

            await submit_prompt(page, text)
            await edit_status(status_msg, "⏳ تم الإرسال، بانتظار الرد...")

            response = await extract_response(
                page,
                text,
                before_lines=before_snapshot,
                timeout_sec=120,
            )

            with suppress(TelegramError):
                await status_msg.delete()

            if not response:
                await update.message.reply_text(
                    "⚠️ لم أتمكن من استخراج رد نصي من الموقع."
                )
                return

            await send_long_text(update.message, response)

        except Exception as exc:
            logger.exception("[handle_message] Error")
            error_text = f"⚠️ خطأ: {str(exc)[:200]}"
            if status_msg:
                await edit_status(status_msg, error_text)
            else:
                await update.message.reply_text(error_text)


# ═══════════════════════════════════════════════════════════════
#  Stream Worker
# ═══════════════════════════════════════════════════════════════

async def stream_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    pw = None
    browser_ctx = None
    page = None

    try:
        os.makedirs(PERSISTENT_DIR, exist_ok=True)
        pw = await async_playwright().start()

        browser_ctx = await pw.chromium.launch_persistent_context(
            PERSISTENT_DIR,
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--no-first-run",
                "--no-zygote",
            ],
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            locale="en-US",
        )
        browser_ctx.set_default_timeout(15000)
        browser_ctx.set_default_navigation_timeout(60000)

        page = browser_ctx.pages[0] if browser_ctx.pages else await browser_ctx.new_page()

        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = window.chrome || { runtime: {} };
            """
        )

        await snap(page, context, chat_id, "🌐 جاري فتح الموقع...", first=True)
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        with suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=10000)
        await snap(page, context, chat_id, "🌐 تم الوصول إلى الموقع")

        # إغلاق النوافذ المنبثقة إن وجدت.
        for selector in [
            'button:has-text("Close")',
            '[aria-label="Close"]',
            'button.close',
        ]:
            with suppress(Exception):
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=1000):
                    await locator.click(timeout=2000)
                    await page.wait_for_timeout(300)

        if LOGIN_EMAIL and LOGIN_PASSWORD:
            await snap(page, context, chat_id, "🔍 التحقق من حالة تسجيل الدخول...")
            if await is_login_visible(page):
                await snap(page, context, chat_id, "🔐 تسجيل الدخول مطلوب...")
                try:
                    await perform_login(page)
                    await snap(page, context, chat_id, "✅ تم تسجيل الدخول")
                except Exception as exc:
                    logger.warning("Login error: %s", exc)
                    await snap(page, context, chat_id, f"⚠️ خطأ في الدخول: {str(exc)[:100]}")
            else:
                await snap(page, context, chat_id, "✅ الجلسة محفوظة مسبقاً")

            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            with suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=10000)

        if TARGET_MODEL:
            await snap(page, context, chat_id, f"🔽 اختيار النموذج: {TARGET_MODEL}...")
            if await select_model(page, TARGET_MODEL):
                await snap(page, context, chat_id, f"✅ النموذج: {TARGET_MODEL}")
            else:
                await snap(page, context, chat_id, "ℹ️ لم يتم العثور على قائمة النماذج")

        lock = await get_streams_lock()
        async with lock:
            if chat_id in streams and streams[chat_id].get("active"):
                streams[chat_id]["page"] = page
                streams[chat_id]["ready"] = True

        await snap(
            page,
            context,
            chat_id,
            "✅ جاهز! أرسل أي رسالة الآن.\nأرسل /stop لإيقاف البث.",
        )

        while True:
            async with lock:
                if chat_id not in streams or not streams[chat_id].get("active"):
                    break
            await snap(page, context, chat_id, f"📡 بث مباشر · تحديث كل {STREAM_INTERVAL}s")
            await asyncio.sleep(STREAM_INTERVAL)

    except asyncio.CancelledError:
        logger.info("[worker] Cancelled for chat %s", chat_id)
        raise
    except (PlaywrightTimeout, PlaywrightError) as exc:
        logger.exception("Playwright stream error")
        with suppress(Exception):
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ توقف البث بسبب خطأ في المتصفح: {str(exc)[:300]}",
            )
    except Exception as exc:
        logger.exception("Stream worker error")
        with suppress(Exception):
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ توقف البث: {str(exc)[:300]}",
            )
    finally:
        if page:
            with suppress(Exception):
                await page.close()
        if browser_ctx:
            with suppress(Exception):
                await browser_ctx.close()
        if pw:
            with suppress(Exception):
                await pw.stop()

        lock = await get_streams_lock()
        async with lock:
            streams.pop(chat_id, None)


# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════

def build_webhook_url(domain: str, secret: str) -> str:
    domain = domain.strip().rstrip("/")
    if domain.startswith(("http://", "https://")):
        return f"{domain}/{secret}"
    return f"https://{domain}/{secret}"


def main() -> None:
    if not TOKEN:
        raise RuntimeError("❌ BOT_TOKEN غير موجود!")

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .concurrent_updates(False)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stream", stream))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if RAILWAY_DOMAIN:
        secret = TOKEN.split(":")[-1]
        webhook_url = build_webhook_url(RAILWAY_DOMAIN, secret)
        logger.info("🚀 Webhook: %s", webhook_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=secret,
            webhook_url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.warning("⚠️ Polling mode active")
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )


if __name__ == "__main__":
    main()

import os
import asyncio
import logging
import re
from io import BytesIO
from urllib.parse import urlparse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

TOKEN = os.environ.get("TOKEN")
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

def normalize_url(text: str) -> str | None:
    text = text.strip()
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme in ('http', 'https') and bool(parsed.netloc):
        return text
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت التصفح الذكي جاهز!\n\n"
        "• وضع المراقبة: أرسل رابط فقط\n"
        "• وضع البرومبت:\n"
        "   السطر 1: الرابط\n"
        "   السطر 2: البرومبت/الرسالة\n\n"
        "إذا كان الموقع يتطلب تسجيل دخول، سأطلب منك البريد وكلمة المرور تلقائياً.\n\n"
        "سألتقط صوراً لكي ترى ما يحدث!"
    )

async def find_and_click_login_button(page):
    """يضغط على زر Log in / Sign in / Login العلوي إذا وجده"""
    selectors = [
        'a:has-text("Log in")',
        'a:has-text("Login")',
        'a:has-text("Sign in")',
        'button:has-text("Log in")',
        'button:has-text("Login")',
        'button:has-text("Sign in")',
    ]
    for sel in selectors:
        btn = page.locator(sel).first
        try:
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(2500)
                return True
        except Exception:
            continue
    return False

async def fill_login_form(page, email: str, password: str):
    """يملأ نموذج تسجيل الدخول ويضغط الزر الحقيقي"""
    # ── حقل Email ──
    email_selectors = [
        'input[type="email"]',
        'input[name="email"]',
        'input[id="email"]',
        'input[placeholder*="email" i]',
        'input[placeholder*="e-mail" i]',
        'input[aria-label*="email" i]',
        'input[inputmode="email"]',
    ]
    email_input = None
    for sel in email_selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                email_input = loc
                break
        except Exception:
            continue

    # ── حقل Password ──
    pass_selectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[id="password"]',
        'input[placeholder*="password" i]',
        'input[aria-label*="password" i]',
    ]
    pass_input = None
    for sel in pass_selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                pass_input = loc
                break
        except Exception:
            continue

    if not email_input or not pass_input:
        return False

    await email_input.scroll_into_view_if_needed()
    await email_input.click()
    await email_input.fill(email)
    await page.wait_for_timeout(500)

    await pass_input.scroll_into_view_if_needed()
    await pass_input.click()
    await pass_input.fill(password)
    await page.wait_for_timeout(500)

    # ── النقر على زر الإرسال (استراتيجيات متعددة) ──
    clicked = False

    # 1) Playwright get_by_role مع regex (الأدق للنصوص المرئية)
    try:
        submit_btn = page.get_by_role(
            "button",
            name=re.compile(r"sign\s*in|log\s*in|login|submit", re.IGNORECASE)
        ).first
        if await submit_btn.count() > 0 and await submit_btn.is_visible():
            await submit_btn.click(force=True)
            clicked = True
    except Exception:
        pass

    # 2) locator :has-text (يبحث في النص المباشر)
    if not clicked:
        for txt in ["Sign in", "Log in", "Login", "Submit"]:
            try:
                btn = page.locator(f'button:has-text("{txt}")').first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(force=True)
                    clicked = True
                    break
            except Exception:
                pass

    # 3) جميع أزرار <button> المرئية (inner_text يعطي النص المرئي فقط)
    if not clicked:
        all_btns = await page.locator("button").all()
        for btn in all_btns:
            try:
                if not await btn.is_visible():
                    continue
                txt = await btn.inner_text()
                if txt and any(k in txt.strip().lower() for k in ["sign in", "log in", "login", "submit"]):
                    await btn.click(force=True)
                    clicked = True
                    break
            except Exception:
                continue

    # 4) input[type="submit"]
    if not clicked:
        try:
            sub = page.locator('input[type="submit"]').first
            if await sub.count() > 0 and await sub.is_visible():
                await sub.click()
                clicked = True
        except Exception:
            pass

    # 5) Fallback مضمون: الضغط على Enter في حقل كلمة المرور
    if not clicked:
        await pass_input.press("Enter")
        clicked = True

    # ── انتظر الانتقال ──
    await page.wait_for_timeout(3500)
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    await page.wait_for_timeout(2000)

    # ── تحقق: هل اختفت حقول التسجيل؟ ──
    still_visible = False
    try:
        still_visible = await email_input.count() > 0 and await email_input.is_visible()
    except Exception:
        pass

    return not still_visible

async def run_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, prompt: str | None, email: str | None = None, password: str | None = None):
    chat_id = update.effective_chat.id
    browser = None

    try:
        await context.bot.send_message(chat_id=chat_id, text=f"⏳ جاري فتح المتصفح...\n{url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page_context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            page = await page_context.new_page()

            await page.goto(url, wait_until="networkidle", timeout=30000)

            # ─── تسجيل الدخول إذا توفرت بيانات ───
            if email and password:
                await context.bot.send_message(chat_id=chat_id, text="🔐 جاري تسجيل الدخول...")
                await page.wait_for_timeout(1500)

                # إذا لم تكن حقول التسجيل ظاهرة مباشرةً، اضغط على زر Log in أولاً
                has_email = await page.locator('input[type="email"], input[name="email"]').first.count() > 0
                has_pass = await page.locator('input[type="password"]').first.count() > 0

                if not (has_email and has_pass):
                    await find_and_click_login_button(page)

                success = await fill_login_form(page, email, password)

                if success:
                    await context.bot.send_message(chat_id=chat_id, text="✅ تم تسجيل الدخول.")
                else:
                    ss = await page.screenshot(type="png", full_page=False)
                    buf = BytesIO(ss)
                    buf.name = "login_check.png"
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=buf,
                        caption="⚠️ ما زلت هنا؟ إذا ظهرت صفحة تسجيل الدخول، قد تحتاج لتحديث بياناتك أو يتطلب الموقع خطوة إضافية."
                    )

            # ─── وضع البرومبت ───
            if prompt:
                await context.bot.send_message(chat_id=chat_id, text="⌨️ جاري البحث عن حقل الكتابة...")
                await page.wait_for_timeout(1500)

                input_box = None
                locators_to_try = [
                    page.locator("textarea").first,
                    page.locator('input[type="text"]').last,
                    page.locator('[contenteditable="true"]').first,
                ]
                for keyword in ["message", "chat", "ask", "search", "prompt", "say something", "type here", "write"]:
                    locators_to_try.append(page.locator(f'[placeholder*="{keyword}" i]').first)
                    locators_to_try.append(page.locator(f'[aria-label*="{keyword}" i]').first)

                for loc in locators_to_try:
                    try:
                        if await loc.count() > 0:
                            await loc.wait_for(state="visible", timeout=3000)
                            input_box = loc
                            break
                    except Exception:
                        continue

                if not input_box:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ لم أجد حقل كتابة تلقائياً. سألتقط صور للحالة الحالية."
                    )
                else:
                    try:
                        await input_box.click()
                        await input_box.fill(prompt)
                        await page.wait_for_timeout(500)
                        await input_box.press("Enter")
                        await page.wait_for_timeout(500)

                        try:
                            send_btn = page.locator('button:has-text("Send"), button[type="submit"]').first
                            if await send_btn.count() > 0 and await send_btn.is_visible():
                                await send_btn.click()
                        except Exception:
                            pass

                        await context.bot.send_message(chat_id=chat_id, text="✅ تم إرسال البرومبت. جاري التقاط الصور...")
                    except Exception as e:
                        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطأ أثناء الكتابة: {e}")

                for i in range(1, 6):
                    await asyncio.sleep(4)
                    screenshot_bytes = await page.screenshot(type="png", full_page=False)
                    photo_buffer = BytesIO(screenshot_bytes)
                    photo_buffer.name = f"shot_{i:02d}.png"
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_buffer,
                        caption=f"📸 لقطة {i}/5 بعد البرومبت"
                    )
                await context.bot.send_message(chat_id=chat_id, text="✅ انتهى التصوير.")

            # ─── وضع المراقبة (رابط فقط) ───
            else:
                await context.bot.send_message(chat_id=chat_id, text="⏳ جاري التقاط صورة كل 3 ثوانٍ (10 لقطات)...")
                for i in range(1, 11):
                    await asyncio.sleep(3)
                    screenshot_bytes = await page.screenshot(type="png", full_page=False)
                    photo_buffer = BytesIO(screenshot_bytes)
                    photo_buffer.name = f"screenshot_{i:02d}.png"
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_buffer,
                        caption=f"📸 لقطة {i}/10 من {url}"
                    )
                await context.bot.send_message(chat_id=chat_id, text="✅ تم الانتهاء.")

    except Exception as e:
        logging.error(f"Monitor Error: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ حدث خطأ:\n<code>{e}</code>",
            parse_mode="HTML"
        )
    finally:
        if browser:
            await browser.close()

async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, prompt: str | None):
    chat_id = update.effective_chat.id
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page_context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            page = await page_context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)

            needs_login = False
            for sel in [
                'a:has-text("Log in")', 'a:has-text("Login")', 'a:has-text("Sign in")',
                'button:has-text("Log in")', 'button:has-text("Login")', 'button:has-text("Sign in")',
            ]:
                btn = page.locator(sel).first
                try:
                    if await btn.count() > 0 and await btn.is_visible():
                        needs_login = True
                        break
                except Exception:
                    continue

            if needs_login:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🔐 هذا الموقع يتطلب تسجيل الدخول.\n\n"
                         "الخطوة 1/2: أرسل بريدك الإلكتروني (Email)."
                )
                context.user_data['pending_auth'] = {
                    'url': url,
                    'prompt': prompt,
                    'step': 'email'
                }
            else:
                asyncio.create_task(run_monitor(update, context, url, prompt))

    except Exception as e:
        logging.error(f"Check login error: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ خطأ أثناء فحص الموقع:\n<code>{e}</code>", parse_mode="HTML")
    finally:
        if browser:
            await browser.close()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return

    pending = context.user_data.get('pending_auth')
    if pending:
        step = pending.get('step')
        if step == 'email':
            context.user_data['pending_auth']['email'] = text
            context.user_data['pending_auth']['step'] = 'password'
            await update.message.reply_text("🔑 الخطوة 2/2: أرسل كلمة المرور (Password).")
            return
        elif step == 'password':
            email = pending.get('email')
            password = text
            url = pending.get('url')
            prompt = pending.get('prompt')
            if 'pending_auth' in context.user_data:
                del context.user_data['pending_auth']
            await update.message.reply_text("⏳ جاري تسجيل الدخول والتنفيذ...")
            asyncio.create_task(run_monitor(update, context, url, prompt, email, password))
            return

    lines = text.split("\n", 1)
    first_line = lines[0].strip()
    url = normalize_url(first_line)

    if url:
        prompt = lines[1].strip() if len(lines) > 1 else None
        asyncio.create_task(process_url(update, context, url, prompt))
    else:
        await update.message.reply_text(
            "❌ لم أتعرف على رابط.\n\n"
            "أرسل الرابط في السطر الأول:\n\n"
            "• رابط فقط:\n`youtube.com`\n\n"
            "• رابط + برومبت:\n"
            "`https://gratisfy.xyz/chat`\n"
            "`اكتب لي قصيدة`"
        )

def main():
    if not TOKEN:
        raise ValueError("❌ متغير البيئة TOKEN غير موجود!")

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

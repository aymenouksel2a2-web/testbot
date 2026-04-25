async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال الرسائل أثناء البث"""
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

            # استخراج الرد
            response = await extract_response(page, text, timeout_sec=120)

            try:
                await status_msg.delete()
            except Exception:
                pass

            if not response:
                await update.message.reply_text("⚠️ لم أتمكن من استخراج رد نصي من الموقع.")
                return

            # تحسين الأسلوب حسب RESPONSE_STYLE
            if RESPONSE_STYLE == "friendly":
                cleaned = f"😊 {response}"
            elif RESPONSE_STYLE == "formal":
                cleaned = f"💼 {response}"
            elif RESPONSE_STYLE == "sarcastic":
                cleaned = f"😏 {response}"
            else:
                cleaned = response

            # إرسال للمستخدم
            max_len = 4000
            if len(cleaned) <= max_len:
                await update.message.reply_text(cleaned)
            else:
                parts = [cleaned[i:i+max_len] for i in range(0, len(cleaned), max_len)]
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
# Stream Worker
# ═══════════════════════════════════════════════════════════════
async def stream_worker(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    pw = None
    browser_ctx = None
    page = None

    try:
        pw = await async_playwright().start()

        # مجلد جلسة مستقل لكل مستخدم
        user_data_dir = os.path.join(PERSISTENT_BASE_DIR, str(chat_id))
        os.makedirs(user_data_dir, exist_ok=True)

        # متصفح مستمر
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

        await snap(page, context, chat_id, "🌐 جاري فتح المتصفح...", first=True)
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await snap(page, context, chat_id, "🌐 تم الوصول إلى الموقع")
        await asyncio.sleep(1.5)

        # إغلاق popups
        for sel in ["button:has-text('Close')", "[aria-label='Close']", "button.close"]:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1500):
                    await loc.click()
                    await page.wait_for_timeout(400)
            except Exception:
                pass

        # تسجيل الدخول
        if LOGIN_EMAIL and LOGIN_PASSWORD:
            await snap(page, context, chat_id, "🔍 التحقق من حالة الجلسة...")
            if await is_login_visible(page):
                await snap(page, context, chat_id, "🔐 تسجيل الدخول مطلوب...")
                try:
                    await perform_login(page)
                    await snap(page, context, chat_id, "✅ تم تسجيل الدخول!")
                except Exception as e:
                    logger.warning(f"Login error: {e}")
                    await snap(page, context, chat_id, f"⚠️ خطأ في الدخول: {str(e)[:100]}")
            else:
                await snap(page, context, chat_id, "✅ الجلسة محفوظة (مسجل مسبقاً)")

            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)

        # اختيار النموذج
        if TARGET_MODEL:
            await snap(page, context, chat_id, f"🔽 اختيار النموذج: {TARGET_MODEL}...")
            ok = await select_model(page, TARGET_MODEL)
            if ok:
                await snap(page, context, chat_id, f"✅ النموذج: {TARGET_MODEL}")
            else:
                await snap(page, context, chat_id, "ℹ️ لم يُعثر على قائمة النماذج")

        # جاهز للدردشة
        async with streams_lock:
            if chat_id in streams and streams[chat_id].get("active"):
                streams[chat_id]["page"] = page
                streams[chat_id]["ready"] = True

        await snap(page, context, chat_id, "✅ جاهز! أرسل أي رسالة الآن.\nأرسل /stop لإيقاف البث.")

        # حلقة البث
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
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ توقف البث: {str(e)[:300]}")
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
# Main
# ═══════════════════════════════════════════════════════════════
def main():
    if not TOKEN:
        raise RuntimeError("❌ BOT_TOKEN غير موجود!")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stream", lambda u, c: asyncio.create_task(stream(u, c))))
    app.add_handler(CommandHandler("stop", lambda u, c: asyncio.create_task(stop(u, c))))
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
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

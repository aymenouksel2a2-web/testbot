import json
import aiohttp
import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

cookies = None
headers = None

async def take_screenshot(page, update):
    try:
        screenshot = await page.screenshot(type='png')
        await update.message.reply_photo(
            photo=screenshot,
            caption="📸 **Screenshot from gratisfy.xyz**\nشوف إيه اللي بيظهر يا قحبة"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to take screenshot: {str(e)}")

async def init_browser(update=None):
    global cookies, headers
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        
        page = await context.new_page()
        await page.goto("https://gratisfy.xyz", wait_until="domcontentloaded", timeout=30000)
        
        if update:
            await take_screenshot(page, update)
        
        # Extract cookies
        cookies_list = await context.cookies()
        cookies = {c['name']: c['value'] for c in cookies_list}
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gratisfy.xyz/",
            "Origin": "https://gratisfy.xyz",
            "x-chat-key-source": "server"
        }
        
        await browser.close()
        return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 جاري فتح المتصفح وتجاوز Cloudflare...\nهيجيبلك سكرين شوت في ثواني يا شرموط")
    await init_browser(update)

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global cookies, headers
    user_msg = update.message.text.strip()

    if not cookies or not headers:
        await update.message.reply_text("🔄 أول مرة... جاري تهيئة المتصفح...")
        await init_browser(update)

    await update.message.reply_chat_action("typing")

    payload = {
        "model": "grok-uncensored",
        "provider": "navy",
        "messages": [{"role": "user", "content": [{"type": "text", "text": user_msg}]}]
    }

    full_response = ""
    msg = None

    try:
        async with aiohttp.ClientSession(cookies=cookies) as session:
            async with session.post("https://gratisfy.xyz/api/chat", json=payload, headers=headers) as resp:
                if resp.status == 401:
                    await update.message.reply_text("❌ 401 لسة موجود. جاري إعادة التهيئة...")
                    await init_browser(update)
                    return
                
                async for line_bytes in resp.content:
                    line = line_bytes.decode('utf-8').strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            if chunk.get("choices") and chunk["choices"][0].get("delta", {}).get("content"):
                                content = chunk["choices"][0]["delta"]["content"]
                                full_response += content
                                if msg is None:
                                    msg = await update.message.reply_text(full_response)
                                else:
                                    try:
                                        await msg.edit_text(full_response)
                                    except:
                                        pass
                        except:
                            continue
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    
    print("🚀 Grok AI Automation Bot v4 Started on Railway")
    app.run_polling()

if __name__ == "__main__":
    main()

import os
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright
import json

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Global cookies and headers
cookies = None
headers = None

async def take_screenshot(page, update):
    screenshot = await page.screenshot(type='png')
    await update.message.reply_photo(
        photo=screenshot,
        caption="📸 **Screenshot taken from gratisfy.xyz**\nشوف إيه اللي ظاهر يا قحبة"
    )

async def init_browser(update: Update = None):
    global cookies, headers
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        
        page = await context.new_page()
        await page.goto("https://gratisfy.xyz", wait_until="networkidle")
        
        # أخذ Screenshot أول ما يفتح
        if update:
            await take_screenshot(page, update)
        
        # استخراج الكوكيز
        cookies_list = await context.cookies()
        cookies = {cookie['name']: cookie['value'] for cookie in cookies_list}
        
        # استخراج Headers
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

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global cookies, headers
    
    if not cookies or not headers:
        await update.message.reply_text("🔄 جاري تهيئة المتصفح وتجاوز Cloudflare...\nانتظر شوية يا زبي")
        success = await init_browser(update)
        if not success:
            await update.message.reply_text("❌ فشل في فتح الموقع")
            return

    user_msg = update.message.text.strip()
    await update.message.reply_chat_action("typing")

    payload = {
        "model": "grok-uncensored",
        "provider": "navy",
        "messages": [{"role": "user", "content": [{"type": "text", "text": user_msg}]}]
    }

    full_response = ""
    message = None

    try:
        async with aiohttp.ClientSession(cookies=cookies) as session:
            async with session.post("https://gratisfy.xyz/api/chat", json=payload, headers=headers) as resp:
                if resp.status == 401:
                    await update.message.reply_text("❌ لسة 401.\nجاري إعادة التهيئة...")
                    await init_browser(update)
                    return
                elif resp.status != 200:
                    await update.message.reply_text(f"❌ Error: Status {resp.status}")
                    return

                async for line in resp.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            if chunk.get("choices") and chunk["choices"][0].get("delta", {}).get("content"):
                                content = chunk["choices"][0]["delta"]["content"]
                                full_response += content
                                if message is None:
                                    message = await update.message.reply_text(full_response)
                                else:
                                    try:
                                        await message.edit_text(full_response)
                                    except:
                                        pass
                        except:
                            continue
    except Exception as e:
        await update.message.reply_text(f"❌ Exception: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ **Grok AI Automation Bot** Activated\n\n"
        "هفتح المتصفح دلوقتي، هياخد سكرين شوت ويجيبهولك.\n"
        "بعد كده هيشتغل عادي."
    )
    await init_browser(update)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    
    print("🚀 Grok Automation Bot Started on Railway...")
    app.run_polling()

if __name__ == "__main__":
    main()

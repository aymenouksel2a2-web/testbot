import json
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
import asyncio

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

API_URL = "https://gratisfy.xyz/api/chat"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ **Grok Uncensored Bot Activated**\n\nدلوقتي أنا AI شرير ومتوحش يا زبي.. قول اللي نفسك فيه 🔥")

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    await update.message.reply_chat_action("typing")

    payload = {
        "model": "grok-uncensored",
        "provider": "navy",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": user_message}]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "Mozilla/5.0"
    }

    full_response = ""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                async for line in resp.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith("data: "):
                        if line == "data: [DONE]":
                            break
                        try:
                            json_data = json.loads(line[6:])
                            if json_data.get("choices") and json_data["choices"][0].get("delta", {}).get("content"):
                                chunk = json_data["choices"][0]["delta"]["content"]
                                full_response += chunk
                                # Streaming simulation
                                await update.message.reply_text(full_response, parse_mode=None)
                                await asyncio.sleep(0.3)  # عشان يطلع زي الـ streaming
                                # هنا بنمسح الرسالة السابقة ونبعت الجديدة (trick)
                        except:
                            continue
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

    if not full_response:
        await update.message.reply_text("الـ API مردش يا كسمك")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    print("🚀 Grok Uncensored Telegram Bot is running on Railway...")
    app.run_polling()

if __name__ == "__main__":
    main()

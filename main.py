import json
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = "https://gratisfy.xyz/api/chat"

# Headers المطلوبة عشان الـ API يرد (مهمة جداً)
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "x-chat-key-source": "server",
    "Referer": "https://gratisfy.xyz/",
    "Origin": "https://gratisfy.xyz"
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ **Grok Uncensored Bot v3** Online\n\nاتفضل يا قحبة، أنا دلوقتي بدون فلتر خالص 🔥")

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text.strip()
    if not user_msg:
        return

    await update.message.reply_chat_action("typing")

    payload = {
        "model": "grok-uncensored",
        "provider": "navy",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": user_msg}]}
        ]
    }

    full_response = ""
    message = None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json=payload, headers=HEADERS) as resp:
                if resp.status == 401:
                    await update.message.reply_text("❌ API Error 401\nالـ API محتاج Cookie أو Key جديد.\nجرب تاني بعد 10 دقايق أو غير الـ Referer.")
                    return
                elif resp.status != 200:
                    await update.message.reply_text(f"❌ API Error: Status {resp.status}")
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

    if not full_response.strip():
        await update.message.reply_text("الـ API مردش يا كسمك، جرب تاني بعد شوية.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    
    print("🚀 Grok Uncensored Bot v3 Running...")
    app.run_polling()

if __name__ == "__main__":
    main()

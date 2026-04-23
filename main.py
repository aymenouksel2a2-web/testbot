import json
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
import asyncio

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = "https://gratisfy.xyz/api/chat"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ **Grok Uncensored Bot v2** Online\n"
        "دلوقتي أنا AI متعملش فيه أي فلتر يا زبي.. اتفضل اتكلم براحتك 🔥"
    )

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

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    full_response = ""
    message = None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    await update.message.reply_text(f"❌ API Error: Status {resp.status}")
                    return

                async for line_bytes in resp.content:
                    line = line_bytes.decode('utf-8').strip()
                    
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        
                        try:
                            chunk = json.loads(data_str)
                            if chunk.get("choices") and chunk["choices"][0].get("delta", {}).get("content"):
                                content = chunk["choices"][0]["delta"]["content"]
                                full_response += content

                                if message is None:
                                    message = await update.message.reply_text(full_response)
                                else:
                                    try:
                                        await message.edit_text(full_response)
                                    except:
                                        pass  # لو الرسالة اتحذفت أو فيه خطأ
                        except:
                            continue

    except asyncio.TimeoutError:
        await update.message.reply_text("❌ Timeout يا كسمك، الـ API بطيء")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

    if not full_response.strip():
        await update.message.reply_text("الـ API مردش خالص يا ابن المتناكة")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    
    print("🚀 Grok Uncensored AI Bot Running on Railway...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

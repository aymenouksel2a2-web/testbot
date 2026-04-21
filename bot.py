# bot.py
import os
import logging
from dotenv import load_dotenv
import telebot

load_dotenv() # Load environment variables from .env file
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Bot token is missing. Ensure you have a '.env' file with 'BOT_TOKEN='.")

logging.basicConfig(level=logging.INFO)
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Welcome! I am your Python Telegram Bot.")

@bot.message_handler(commands=['ping'])
def ping_pong(message):
    bot.send_message(message.chat.id, "Pong!")


# Fallback handler for all text messages
@bot.message_handler(func=lambda message: True)
def echo_all(message):
    if message.text:
        bot.reply_to(message, f"You said: {message.text}")

if __name__ == "__main__":
    bot.infinity_polling()

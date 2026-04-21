import os
import logging
import time
from dotenv import load_dotenv
from telebot import TeleBot, types
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options

load_dotenv() # Load environment variables from .env file
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Bot token is missing. Ensure you have a '.env' file with 'BOT_TOKEN='.")

logging.basicConfig(level=logging.INFO)
bot = TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Welcome! I am your Python Telegram Bot.")

@bot.message_handler(commands=['ping'])
def ping_pong(message):
    bot.send_message(message.chat.id, "Pong!")


# Arena Command with Multi-Step Screenshot Logic
@bot.message_handler(commands=['arena'])
def arena_command(message):
    chat_id = message.chat.id

    # Configure Chrome Options for Headless Docker environment
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox") 
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")

    try:
        # Initialize WebDriver (points to chromedriver installed in Docker)
        driver = webdriver.Chrome(service=ChromeService(executable_path='/usr/bin/chromedriver'), options=chrome_options)

        bot.send_message(chat_id, "Initializing browser and capturing steps...")

        # Navigate to URL
        driver.get("https://arena.ai/image/direct")

        
        # Loop to capture 5 screenshots with 3 seconds delay between each
        for step in range(1, 6):
            time.sleep(3) # Wait interval

            # Take screenshot and save to temporary location (overwrite previous file)
            screenshot_path = "/tmp/step.png"
            driver.save_screenshot(screenshot_path)

            
            # Send image to user with caption indicating progress
            with open(screenshot_path, "rb") as photo:
                caption = f"Step {step}/5"
                bot.send_photo(chat_id, photo, caption=caption)

        driver.quit() # Clean up browser instance

    except Exception as e: # Catch-all for robustness in production
        logging.error(f"Error during arena command execution: {e}")
        bot.reply_to(message, f"An error occurred while processing the request. {e}")

if __name__ == "__main__":
    bot.infinity_polling()

# bot.py
import os
import logging
from dotenv import load_dotenv
from telebot import TeleBot, types
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException

load_dotenv() # Load environment variables from .env file
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Bot token is missing. Ensure you have a '.env' file with 'BOT_TOKEN='.")

logging.basicConfig(level=logging.INFO)
bot = TeleBot(BOT_TOKEN)

# Standard Commands
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Welcome! I am your Python Telegram Bot.")


@bot.message_handler(commands=['ping'])
def ping_pong(message):
    bot.send_message(message.chat.id, "Pong!")


# Arena Command (Selenium Integration)
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

        bot.send_message(chat_id, "Initializing browser...")
        
        # Navigate to URL
        driver.get("https://arena.ai/image/direct")
        
        # Take screenshot (wait for page to load)
        screenshot = driver.get_screenshot_as_file("/tmp/screenshot.png")

        if not screenshot:
            raise Exception("Screenshot generation failed.")

        driver.quit() # Crucial: Clean up browser instance to free Docker container memory

        # Send image to Telegram
        with open("/tmp/screenshot.png", "rb") as photo:
            bot.send_photo(chat_id, photo)
            
    except WebDriverException as e:
        logging.error(f"Selenium error occurred: {e}")
        bot.reply_to(message, f"Error navigating to the site. {e}")

if __name__ == "__main__":
    bot.infinity_polling()

import os
import logging
import time
from dotenv import load_dotenv
from telebot import TeleBot, types
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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


# Dynamic Prompt Generation Command (State Management + DOM Interaction)
@bot.message_handler(commands=['generate'])
def generate_command(message):
    chat_id = message.chat.id

    # Step 1: Ask the user for their prompt
    msg = bot.send_message(chat_id, "Please enter your prompt for the image:")
    bot.register_next_step_handler(msg, process_prompt)


def process_prompt(message):
    chat_id = message.chat.id
    user_prompt = message.text

    if not user_prompt:
        bot.send_message(chat_id, "You must provide a text description. Try again.")
        return

    
    # Step 2: Configure Selenium and navigate to URL
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox") 
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")

    try:
        driver = webdriver.Chrome(service=ChromeService(executable_path='/usr/bin/chromedriver'), options=chrome_options)

        
        # Notify user of processing time
        bot.send_message(chat_id, "Processing your prompt... Please wait about 15 seconds.")    
        
        driver.get("https://arena.ai/image/direct")
        
        # Step 3: Modal Bypass Logic (Click Agree)
        # Wait for the 'Agree' button to be clickable and click it
        agree_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Agree')]"))
        )
        agree_button.click()
        
        # Ensure modal animation finishes before proceeding (DOM settling)
        time.sleep(1)    

        
        # Step 4: Locate the textarea using WebDriverWait and XPath
        input_field = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//textarea[contains(@placeholder, 'Describe')]"))
        )
        
        # Step 5: Inject user prompt and submit form
        input_field.send_keys(user_prompt)
        input_field.send_keys(Keys.RETURN)
        
        # Step 6: Wait for image generation (Hardcoded delay)
        time.sleep(15)    
        
        # Step 7: Capture screenshot and send result
        screenshot_path = "/tmp/result.png"
        driver.save_screenshot(screenshot_path)

        with open(screenshot_path, "rb") as photo:
            bot.send_photo(chat_id, photo, caption="Your Result")

    except Exception as e:
        logging.error(f"Error during generation process: {e}")
        bot.reply_to(message, f"There was an error processing your prompt. {e}")

    finally:
        driver.quit() # Always clean up

if __name__ == "__main__":
    bot.infinity_polling()

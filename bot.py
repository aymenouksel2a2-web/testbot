import telebot
from playwright.sync_api import sync_playwright
import time
import threading

# ضع توكن البوت الخاص بك هنا (من BotFather)
BOT_TOKEN = "7444106461:AAFZCj0Xs-3qnMqKtQf6vxKp_Nh53BQMIqY"
bot = telebot.TeleBot(BOT_TOKEN)

# متغير للتحكم في حالة البث
is_streaming = False

def stream_browser(chat_id, url):
    global is_streaming
    is_streaming = True
    
    with sync_playwright() as p:
        # إطلاق المتصفح بصلاحيات تتناسب مع بيئة الحاويات
        browser = p.chromium.launch(
            headless=True, 
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        page = browser.new_page()
        
        try:
            bot.send_message(chat_id, f"[*] Establishing connection to: {url}")
            page.goto(url, timeout=60000)
            
            # حلقة البث المباشر
            while is_streaming:
                # التقاط الصورة وضغطها لتقليل استهلاك الشبكة
                screenshot_bytes = page.screenshot(type='jpeg', quality=60)
                bot.send_photo(chat_id, screenshot_bytes)
                time.sleep(3) # الانتظار 3 ثواني لتجنب حظر التلغرام (Rate Limit)
                
        except Exception as e:
            bot.send_message(chat_id, f"[!] Kernel/Network Error: {e}")
        finally:
            browser.close()
            bot.send_message(chat_id, "[*] Connection terminated. Browser closed.")

@bot.message_handler(commands=['start'])
def start_command(message):
    bot.reply_to(message, "AynX Recon Bot Active.\nUse: /visit <URL>\nUse: /stop to halt the stream.")

@bot.message_handler(commands=['visit'])
def visit_command(message):
    global is_streaming
    
    # استخراج الرابط من الرسالة
    command_parts = message.text.split(' ', 1)
    if len(command_parts) < 2:
        bot.reply_to(message, "Syntax: /visit https://example.com")
        return
        
    url = command_parts[1].strip()
    
    if is_streaming:
        bot.reply_to(message, "[!] Stream already active. Send /stop first.")
        return
        
    # تشغيل المتصفح في Thread منفصل لمنع تجميد البوت الأساسي
    threading.Thread(target=stream_browser, args=(message.chat.id, url)).start()

@bot.message_handler(commands=['stop'])
def stop_command(message):
    global is_streaming
    is_streaming = False
    bot.reply_to(message, "[*] Sending halt signal to browser thread...")

bot.polling(none_stop=True)

import logging
from os import getenv
from telegram import Update, ForceReply
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# 1. Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 2. Define Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        rf"Hi {user.mention_markdown_v2()}\!",
        reply_markup=ForceReply(selective=True),
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text('Help!')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    try:
        # Simulate processing
        response = f"Received: {update.message.text}"
        if len(response) > 4096:
            raise ValueError("Message too long")
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in echo handler: {e}")
        await update.message.reply_text("An error occurred while processing your request.")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unknown commands."""
    await update.message.reply_text(
        "Sorry, I didn't understand that command."
    )

# 3. Main Function (Entry Point)
def main() -> None:
    """Start the bot."""
    # Retrieve token from environment variables securely
    telegram_token = getenv('TELEGRAM_TOKEN')
    
    if not telegram_token:
        logger.critical("Telegram Token is missing!")
        return

    application = ApplicationBuilder().token(telegram_token).build()

    # Add Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Handle unknown commands/errors
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    # Start polling
    application.run_polling()

if __name__ == '__main__':
    main()

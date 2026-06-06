import logging
from os import environ
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from sqlite3 import connect

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(filename='~/watson/logs/error.log', level=logging.ERROR)

TOKEN = environ.get('TELEGRAM_BOT_TOKEN')

def start(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [InlineKeyboardButton("Files", callback_data='files')],
        [InlineKeyboardButton("Memory", callback_data='memory')]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    update.message.reply_text('Choose an option:', reply_markup=reply_markup)

def button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    
    if query.data == 'files':
        # Logic to open file sidebar
        pass  # Replace with actual implementation
    
    elif query.data == 'memory':
        # Open full screen panel showing the project memory file
        # Add notes functionality
        pass  # Replace with actual implementation

def error_handler(update, context):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

updater = Updater(TOKEN)
dispatcher = updater.dispatcher

start_handler = CommandHandler('start', start)
dispatcher.add_handler(start_handler)

button_handler = CallbackQueryHandler(button)
dispatcher.add_handler(button_handler)

dispatcher.add_error_handler(error_handler)

updater.start_polling()
logging.info("Bot started and listening for commands.")
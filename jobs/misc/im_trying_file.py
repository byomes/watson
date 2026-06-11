import os
from telegram import Update, Bot
from telegram.ext import Updater, MessageHandler, Filters, CallbackContext

# Load credentials from environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_PATH = os.path.expanduser("~/watson/data/watson.db")
LOG_PATH = os.path.expanduser("~/watson/logs/error.log")


def read_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()
    except Exception as e:
        log_error(f"Error reading file: {e}")
        return None


def log_error(message):
    with open(LOG_PATH, "a", encoding="utf-8") as log_file:
        log_file.write(f"{os.path.basename(__file__)}: {message}\n")


def handle_message(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.message.text

    if message.startswith("/read"):
        file_path = message.split(" ", 1)[1]
        content = read_file(file_path)
        if content:
            update.message.reply_text(f"File content:\n{content}")
        else:
            update.message.reply_text("Failed to read file.")


def main() -> None:
    updater = Updater(TELEGRAM_TOKEN)

    dispatcher = updater.dispatcher

    dispatcher.add_handler(
        MessageHandler(Filters.text & ~Filters.command, handle_message)
    )

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()


def run(message: str = None) -> str:
    return "I’m trying to give you a file to read that is longer than 8000 characters and yo"

# Telegram Bot — Handler Patterns

## Service
watson-bot.service — systemd on Beelink

## Key Files
bot/bot.py — main bot file

## Patterns
- CommandHandler for slash commands
- MessageHandler with filters for natural language
- CallbackQueryHandler for inline buttons
- Always answer callback queries to clear loading state
- Telegram is away interface — not primary at home

## Approval Flow Pattern
Watson proposes via Telegram message with APPROVE/REJECT inline buttons.
Handler catches callback, checks data prefix, executes or logs rejection.

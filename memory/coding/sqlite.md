# SQLite — Watson DB Patterns

## Location
~/watson/data/watson.db

## Tables (current)
- blog_drafts — scheduled blog posts
- facebook_queue — Facebook post queue
- connect_cards — parsed Sunday connect cards
- people — People Registry
- tasks — task manager
- reminders — reminders
- reading_list — reading list
- chat_sessions — dashboard chat sessions
- chat_messages — chat message history
- email_queue — (planned) triaged email inbox

## Patterns
- Create table if not exists on first run
- Always use parameterized queries
- Timestamps as ISO strings
- Status fields as text enums

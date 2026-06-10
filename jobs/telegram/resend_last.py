from core.database import get_connection

def init_db():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS telegram_last_message (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    finally:
        conn.close()

def store_message(text: str):
    if not text or text.strip().lower() == 'resend':
        return
    init_db()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO telegram_last_message (message_text) VALUES (?)',
            (text,)
        )
        conn.commit()
    finally:
        conn.close()

def get_last_message() -> str:
    init_db()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT message_text FROM telegram_last_message ORDER BY id DESC LIMIT 1'
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()

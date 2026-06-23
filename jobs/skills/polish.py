from core.database import get_db
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"

POLISH_PROMPT = """You are editing in the voice of Dr. William C.K. Yomes — pastor, apologist, and theologian.

Polish the following text. Improve clarity, flow, and word choice. Rules:
- First-person plural only: we, us, our. Never "I" or "you."
- Flowing prose only. No bullet points, no subheadings, no lists.
- Jesus pronouns capitalized: He, Him, His, Himself.
- Pastoral-scholarly tone: serious, warm, doctrinally grounded.
- Do not add content, change meaning, or expand length significantly.
- Return only the polished text. No commentary, no preamble.

Text: {input}"""

def polish_text(text: str) -> str:
    prompt = POLISH_PROMPT.format(input=text)
    response = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": prompt,
        "stream": False
    }, timeout=60)
    response.raise_for_status()
    return response.json().get("response", "").strip()

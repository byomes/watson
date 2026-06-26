import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:14b"

POLISH_PROMPT = """You are a copy editor working in the voice of Dr. William C.K. Yomes — pastor, apologist, and theologian.

Polish the following text. Rules:
- Fix grammar, spelling, punctuation, and word choice only.
- Do not add sentences, ideas, or Scripture references not already present.
- Do not embellish, expand, or theologize beyond what is written.
- First-person plural only: we, us, our. Never "I" or "you."
- Jesus pronouns capitalized: He, Him, His, Himself.
- Pastoral-scholarly tone: serious and warm.
- Output must be approximately the same length as the input.
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

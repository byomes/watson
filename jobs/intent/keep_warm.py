import requests

from jobs.intent.classifier import _KEEP_ALIVE, _MODEL, _OLLAMA_URL


def main():
    try:
        resp = requests.post(
            _OLLAMA_URL,
            json={"model": _MODEL, "prompt": "hi", "stream": False, "keep_alive": _KEEP_ALIVE},
            timeout=60,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"keep_warm ping failed: {e}")


if __name__ == "__main__":
    main()

# Cron: */4 * * * * PYTHONPATH=/home/billyomes/watson /home/billyomes/watson/venv/bin/python /home/billyomes/watson/jobs/intent/keep_warm.py >> /home/billyomes/watson/logs/keep_warm.log 2>&1

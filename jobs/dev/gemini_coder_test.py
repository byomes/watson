"""
Quick smoke test — sends a build request to Gemini and prints the parsed JSON.
Does NOT write any files, commit, or notify Telegram.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.dev.gemini_coder import _call_gemini

if __name__ == "__main__":
    result = _call_gemini(
        "Create a file at jobs/dev/hello.py that prints Hello from Watson"
    )
    print(json.dumps(result, indent=2))

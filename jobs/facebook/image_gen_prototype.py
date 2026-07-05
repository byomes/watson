#!/usr/bin/env python3
"""
Prototype ONLY — not a Watson job yet.
Test image generation before wiring into jobs/facebook/.

Run manually on the Beelink:
    python3 image_gen_prototype.py "a lighthouse on a stormy coast, oil painting style"

No API key required. Uses Pollinations.ai free text-to-image endpoint.
"""

import sys
import urllib.request
import urllib.parse
from pathlib import Path

def generate_image(prompt: str, out_path: str = "test_output.jpg", width=1080, height=1080):
    encoded = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true"

    print(f"Requesting: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Watson/1.0"})

    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()

    Path(out_path).write_bytes(data)
    print(f"Saved {len(data)} bytes to {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 image_gen_prototype.py \"<prompt text>\"")
        sys.exit(1)

    prompt = sys.argv[1]
    generate_image(prompt)

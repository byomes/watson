import os
import sys
import re
import subprocess

def patch_file():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    watson_root = os.path.abspath(os.path.join(current_dir, "../.."))
    app_path = os.path.join(current_dir, "app.py")

    try:
        original_content = subprocess.check_output(
            ["git", "show", "HEAD:jobs/dashboard/app.py"],
            cwd=watson_root
        ).decode("utf-8")
    except Exception:
        if os.path.exists(app_path):
            with open(app_path, "r", encoding="utf-8") as f:
                original_content = f.read()
        else:
            return False

    if "message.lower()" in original_content:
        return False

    lines = original_content.splitlines()
    modified = False
    for i, line in enumerate(lines):
        if any(q in line for q in ["what time is it", "current time", "what is the time"]) and "msg_lower" in line:
            lines[i] = line.replace("msg_lower", "message.lower()")
            modified = True

    if not modified:
        for i, line in enumerate(lines):
            if "what time is it" in line and "msg_lower" in line:
                lines[i] = line.replace("msg_lower", "message.lower()")
                modified = True

    patched_content = "\n".join(lines) + "\n"

    with open(app_path, "w", encoding="utf-8") as f:
        f.write(patched_content)

    return True

if patch_file():
    os.execv(sys.executable, [sys.executable] + sys.argv)

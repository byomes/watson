"""jobs/sms/catalyst_app_send.py -- post a message into a Catalyst302 app
(Subsplash) group conversation as "Catalyst Community Church", by driving
the app on the gateway phone over USB adb. Elements are located by text
via `uiautomator dump`, not hardcoded coordinates (except the unlabeled
send arrow, found by position of the compose row).

Account: CATALYST_APP_EMAIL / CATALYST_APP_PASSWORD in .env (the info@
account; it only sees groups it has been added to). Installed 2026-10-06.

Usage: python -m jobs.sms.catalyst_app_send "Teaching Team" "message text"
Posts are made as the church: any trailing "- Watson" is stripped.
Message text is typed via `adb input text` (ASCII only, no em dashes).
Not a Telegram/SMS path: honors the same quiet-hours rule by caller policy,
this module does not enforce it.
"""
import logging
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from dotenv import load_dotenv

from jobs.sms.adb_client import ADB, connect_device

log = logging.getLogger(__name__)
PKG = "com.subsplashconsulting.s_7BVGB9"


class _Phone:
    def __init__(self, serial):
        self.s = serial

    def sh(self, *args, timeout=20):
        return subprocess.run([ADB, "-s", self.s, "shell", *args], capture_output=True, text=True, timeout=timeout)

    def ui(self):
        self.sh("uiautomator", "dump", "/sdcard/ui.xml")
        xml = subprocess.run([ADB, "-s", self.s, "exec-out", "cat", "/sdcard/ui.xml"], capture_output=True, text=True, timeout=20).stdout
        return ET.fromstring(xml[xml.index("<"):])

    def find(self, text=None, desc=None, cls=None, contains=False):
        for n in self.ui().iter("node"):
            t, d = n.get("text", ""), n.get("content-desc", "")
            ok = True
            if text is not None:
                ok &= (text in t) if contains else (t == text)
            if desc is not None:
                ok &= (desc in d) if contains else (d == desc)
            if cls is not None:
                ok &= n.get("class") == cls
            if ok:
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds"))
                x1, y1, x2, y2 = map(int, m.groups())
                return (x1 + x2) // 2, (y1 + y2) // 2
        return None

    def tap(self, xy):
        self.sh("input", "tap", str(xy[0]), str(xy[1]))

    def wait_find(self, tries=8, **kw):
        for _ in range(tries):
            xy = self.find(**kw)
            if xy:
                return xy
            time.sleep(1.5)
        return None

    def type(self, text):
        self.sh("input", "text", text.replace(" ", "%s").replace("!", "\\!").replace("&", "\\&").replace("(", "\\(").replace(")", "\\)"))


def send_group_message(group: str, message: str) -> bool:
    # Posts go out as the church, not as Watson: never carry a Watson sign-off.
    message = re.sub(r"\s*[-\u2013\u2014]+\s*Watson\s*$", "", message, flags=re.I).strip()
    if not message:
        return False
    load_dotenv(os.path.expanduser("~/watson/.env"))
    email, pw = os.getenv("CATALYST_APP_EMAIL"), os.getenv("CATALYST_APP_PASSWORD")
    serial = connect_device()
    if not (serial and email and pw):
        log.error("catalyst_app_send: no device or missing credentials")
        return False
    p = _Phone(serial)
    p.sh("input", "keyevent", "KEYCODE_WAKEUP")
    p.sh("input", "swipe", "360", "1200", "360", "300", "200")
    p.sh("am", "force-stop", PKG)  # clean start; relaunched stays running below
    p.sh("monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(8)
    # Optional one-time prompts.
    for label in ("Skip, I don't want to add my phone", "Don’t allow", "Don't allow"):
        xy = p.find(text=label)
        if xy:
            p.tap(xy)
            time.sleep(2)
    # Messaging icon (content-desc varies; fall back to top-bar position).
    p.sh("input", "tap", "506", "128")
    time.sleep(4)
    xy = p.wait_find(text="Conversations")
    if not xy:
        log.error("catalyst_app_send: Messaging screen not found")
        return False
    p.tap(xy)
    time.sleep(3)
    # Logged out? Log in with email.
    xy = p.find(text="Continue with Email")
    if xy:
        p.tap(xy)
        time.sleep(3)
        p.sh("input", "tap", "360", "720")
        p.type(email)
        p.sh("input", "keyevent", "KEYCODE_TAB")
        p.type(pw)
        xy = p.wait_find(text="Log in")
        p.tap(xy)
        time.sleep(6)
        xy = p.find(text="Not now")
        if xy:
            p.tap(xy)
            time.sleep(2)
    # Find the group row, scrolling the list as needed.
    xy = None
    for _ in range(8):
        xy = p.find(text=group)
        if xy:
            break
        p.sh("input", "swipe", "360", "1300", "360", "500", "300")
        time.sleep(1)
    if not xy:
        log.error("catalyst_app_send: group %r not found in conversations", group)
        return False
    p.tap(xy)
    time.sleep(4)
    box = p.wait_find(text="Message " + group)
    if not box:
        log.error("catalyst_app_send: compose box not found")
        return False
    p.tap(box)
    time.sleep(1)
    p.type(message)
    time.sleep(1)
    # Send arrow sits at the right end of the (now raised) compose row.
    row = p.find(text=message.split()[0], contains=True, cls="android.widget.EditText")
    if not row:
        log.error("catalyst_app_send: typed text not found")
        return False
    p.sh("input", "tap", "646", str(row[1]))
    time.sleep(3)
    ok = p.find(text=message[:30], contains=True) is not None and p.find(text="Message " + group) is not None
    # Leave the app running (not force-stopped) or Android drops its push
    # notifications, which jobs/sms/catalyst_app_inbox.py depends on.
    p.sh("input", "keyevent", "KEYCODE_HOME")
    p.sh("input", "keyevent", "KEYCODE_SLEEP")
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(0 if send_group_message(sys.argv[1], sys.argv[2]) else 1)

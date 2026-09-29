"""jobs/sms/appium_client.py -- on-demand Appium session against the SMS
gateway phone, for real touch-interface automation (tap-by-element via
UiAutomator2) instead of blind `adb shell input tap x y` at hardcoded
coordinates (the approach call_forwarding_toggle.py's `_dial_mmi` still
uses -- see project_sms_gateway_app.md for why that's fragile).

No persistent Appium service runs on this box -- same "no standing
always-on automation surface" call already made for wireless adb on this
single-purpose device. `appium_session()` starts a fresh server per call
and tears it down after, so a cron job (e.g. a future call-forwarding
rewrite) pays a few seconds of startup, not an always-on daemon.

Requires (installed 2026-09-28, no root/sudo used):
  - JDK: portable Temurin 17 unpacked to ~/jdk (no system JDK existed)
  - "Android SDK": ~/Android/Sdk/platform-tools is a symlink to the
    existing ~/platform-tools -- NOT a real SDK install. This satisfies
    Appium's ANDROID_HOME check for real-device automation; it is NOT
    enough for emulator use (none needed here, phone is physical hardware).
  - `npm install -g appium` + `appium driver install uiautomator2`
  - `Appium-Python-Client` in ~/watson/venv (see requirements.txt)

APPIUM_BIN below is hardcoded to the current nvm node version's global
bin, same fragility class as adb_client.py's hardcoded ADB path -- an nvm
node upgrade will silently break this until the path is updated.
"""
import logging
import os
import subprocess
import time
from contextlib import contextmanager

import requests
from appium import webdriver
from appium.options.android import UiAutomator2Options

from jobs.sms.adb_client import connect_device

log = logging.getLogger(__name__)

JAVA_HOME = os.path.expanduser("~/jdk")
ANDROID_HOME = os.path.expanduser("~/Android/Sdk")
APPIUM_BIN = os.path.expanduser("~/.nvm/versions/node/v24.16.0/bin/appium")
APPIUM_PORT = 4723
APPIUM_URL = f"http://127.0.0.1:{APPIUM_PORT}"

_SERVER_START_TIMEOUT = 30


def _server_env() -> dict:
    env = os.environ.copy()
    env["JAVA_HOME"] = JAVA_HOME
    env["ANDROID_HOME"] = ANDROID_HOME
    env["PATH"] = f"{JAVA_HOME}/bin:{ANDROID_HOME}/platform-tools:" + env.get("PATH", "")
    return env


def _wait_for_ready(proc: subprocess.Popen, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False  # server process died before coming up
        try:
            resp = requests.get(f"{APPIUM_URL}/status", timeout=2)
            if resp.ok and resp.json().get("value", {}).get("ready"):
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False


@contextmanager
def appium_session(app_package: str | None = None, launch_timeout: int = 60):
    """Yields a connected Appium `driver` against the SMS gateway phone.

    Starts a fresh Appium server, connects (Tailscale IP preferred, LAN
    fallback -- via adb_client.connect_device), and tears both the
    session and server down on exit even if the caller raises.

    `app_package` optionally sets appPackage so the session attaches to
    (rather than resets) whatever app is already in the foreground --
    useful on this kiosk-locked phone where launching an arbitrary
    activity may not be allowed by the Headwind whitelist.
    """
    device = connect_device()
    if not device:
        raise RuntimeError("appium_session: phone unreachable via adb (Tailscale and LAN both failed)")

    server = subprocess.Popen(
        [APPIUM_BIN, "--address", "127.0.0.1", "--port", str(APPIUM_PORT), "--log", os.path.expanduser("~/appium.log")],
        env=_server_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_for_ready(server, _SERVER_START_TIMEOUT):
            raise RuntimeError("appium_session: Appium server did not become ready in time")

        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.automation_name = "UiAutomator2"
        options.udid = device
        options.no_reset = True
        options.new_command_timeout = launch_timeout
        if app_package:
            options.app_package = app_package
            options.dont_stop_app_on_reset = True

        driver = webdriver.Remote(APPIUM_URL, options=options)
        try:
            yield driver
        finally:
            driver.quit()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

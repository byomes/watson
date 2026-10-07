"""jobs/sms/call_forwarding_toggle.py — Friday Sabbath call-forwarding
toggle for Watson's SMS gateway phone (project_backlog id=39).

Carrier call forwarding (Verizon prepaid, set up 2026-09-26) means calls
to the work number ((302) 502-7928) ring Bill's personal phone. Bill
wants those silenced for his family Sabbath (Fridays, 12:00am-11:59pm).
There's no app-level hook into carrier call forwarding -- the only way to
change it is dialing the MMI codes *73 (disable) / *72+number (enable) on
the phone itself, which this drives via adb (launching the dialer intent)
plus a real Appium/UiAutomator2 session (jobs/sms/appium_client.py) to
find and tap the Dial/End call buttons -- migrated 2026-09-29 off the
original blind `adb shell input tap x y` approach (parsing a raw
uiautomator XML dump for element bounds by hand), which worked but broke
silently on any layout shift. Still a best-effort signal, not a confirmed
carrier-side result -- outcome should be watched.

Run via cron:
  0 0 * * 5  ... call_forwarding_toggle.py disable   # Friday 12:00am
  0 0 * * 6  ... call_forwarding_toggle.py enable    # Saturday 12:00am (i.e. Friday 11:59pm elapsed)

Also respects vacation mode (jobs.sms.settings) -- if vacation mode is on,
forwarding stays enabled every day (no Sabbath-only disable) since Bill
presumably wants normal reachability rules suspended differently during
vacation, not compounded with the weekly Sabbath rule. Re-evaluate this
assumption if it doesn't match what Bill actually wants once vacation
mode gets used for real.
"""
import logging
import os
import subprocess
import sys
import time

from appium.webdriver.common.appiumby import AppiumBy
from dotenv import load_dotenv
from selenium.common.exceptions import NoSuchElementException

from jobs.sms.adb_client import ADB, connect_device
from jobs.sms.appium_client import appium_session

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

log = logging.getLogger(__name__)


def _dial_mmi(device: str, code: str) -> bool:
    """Dials an MMI code via the real dialer (ACTION_DIAL, not ACTION_CALL --
    the latter is blocked for non-default-dialer callers), taps the visible
    dial button via a real Appium session, waits, then ends the call.
    Returns True if it got through the whole sequence without an obvious
    failure; this is a best-effort signal, not a confirmed carrier-side
    result -- see module docstring."""
    subprocess.run(
        [ADB, "-s", device, "shell", "am", "start", "-a", "android.intent.action.DIAL", "-d", f"tel:{code}"],
        capture_output=True, text=True, timeout=20,
    )
    time.sleep(1.5)
    try:
        with appium_session() as driver:
            driver.implicitly_wait(5)
            try:
                driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="dial").click()
            except NoSuchElementException:
                log.warning("call_forwarding_toggle: dial button not found")
                return False
            time.sleep(15)
            try:
                driver.find_element(by=AppiumBy.ACCESSIBILITY_ID, value="End call").click()
            except NoSuchElementException:
                pass  # MMI codes often don't leave a persistent call screen to end
    except Exception:
        log.exception("call_forwarding_toggle: Appium session failed")
        return False
    return True


def run(action: str) -> None:
    from jobs.sms import settings as sms_settings  # local import, avoids a circular import at module load

    if sms_settings.get_vacation_mode():
        log.info("call_forwarding_toggle: vacation mode is on, skipping Sabbath forwarding change")
        return

    device = connect_device()
    if not device:
        _alert(f"Sabbath call-forwarding {action} failed: phone unreachable via adb (USB reconnect may be needed).")
        return

    owner_phone = os.getenv("WATSON_OWNER_PHONE", "")
    code = "*73" if action == "disable" else f"*72{owner_phone}"
    ok = _dial_mmi(device, code)
    if not ok:
        _alert(f"Sabbath call-forwarding {action} may have failed (dial automation didn't complete cleanly) -- please verify manually.")
    else:
        log.info("call_forwarding_toggle: dialed %s for action=%s", code, action)


def _alert(text: str) -> None:
    try:
        from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        import requests

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": f"{text}"},
            timeout=10,
        )
    except Exception as exc:
        log.error("call_forwarding_toggle: alert send failed: %s", exc)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("enable", "disable"):
        print("Usage: call_forwarding_toggle.py enable|disable")
        sys.exit(1)
    run(sys.argv[1])

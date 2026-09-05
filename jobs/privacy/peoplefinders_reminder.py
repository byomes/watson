"""jobs/privacy/peoplefinders_reminder.py — Privacy Guard's PeopleFinders channel
is phone-only: peoplefinders.com/robots.txt explicitly disallows /opt-out, so
goto_safe() refuses it (standing policy, see jobs/browser/browser_service.py) and
this broker stays active=0 in privacy_brokers with no automated path at all.
The only compliant channel is calling one of the two numbers listed on its
allowed /do-not-sell page. Rather than leave that a permanent dead end, this
sends a quarterly Telegram nudge -- same send_telegram()/vacation_gate() as
every other Privacy Guard notification, just on a much longer cadence.

Cron: 0 9 1 1,4,7,10 * (quarterly, 1st of Jan/Apr/Jul/Oct). On-demand:
`python -m jobs.privacy.peoplefinders_reminder`.
"""
import argparse
import logging

from jobs.privacy import send_telegram

log = logging.getLogger(__name__)


def run() -> None:
    send_telegram(
        "📞 Privacy Guard quarterly reminder: PeopleFinders can't be opted out "
        "automatically (its /opt-out page is robots.txt-disallowed, so Watson "
        "won't touch it). The only channels are by phone: (877) 551-9688 "
        "(opt-out specific) or (800) 718-8997 (general) -- worth a call if you "
        "have a few minutes."
    )
    print("Privacy Guard: PeopleFinders phone reminder sent.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    argparse.ArgumentParser(description="Privacy Guard quarterly PeopleFinders phone-opt-out reminder.").parse_args()
    run()

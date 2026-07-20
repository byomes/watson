import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

from jobs.sms.carrier_lookup import get_sms_address, normalize_phone, save_carrier

load_dotenv(os.path.join(os.path.dirname(__file__), '../../.env'))


def send_sms(to_name: str, to_phone: str, carrier: str, message: str) -> dict:
    """
    Send an SMS via email-to-SMS gateway, resolving the carrier through the
    shared phone_carriers cache (jobs.sms.carrier_lookup).

    If `carrier` is given, it's saved as a confirmed override before lookup —
    this both trusts an explicitly-known carrier and backfills the cache for
    future sends to the same number.

    Returns {'success': True, 'to': str} or
            {'success': False, 'error': str, 'needs_carrier': bool, 'phone': str}
            (needs_carrier/phone are only present when no gateway could be resolved,
            for callers that want to trigger a manual-confirm flow)
    """
    phone_digits = normalize_phone(to_phone)
    if not phone_digits:
        return {
            'success': False,
            'error': f"Invalid phone number for {to_name}: {to_phone}",
        }

    if carrier and carrier.lower().strip() not in ('', 'unknown', 'other'):
        save_carrier(phone_digits, carrier, source='manual', confirmed=True)

    sms_address = get_sms_address(phone_digits)
    if not sms_address:
        return {
            'success': False,
            'error': f"No carrier on file for {to_name}.",
            'needs_carrier': True,
            'phone': phone_digits,
        }

    smtp_host = os.getenv('WATSON_SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.getenv('WATSON_SMTP_PORT', 587))
    smtp_user = os.getenv('WATSON_GMAIL_ADDRESS')
    smtp_pass = os.getenv('WATSON_GMAIL_APP_PASSWORD')
    from_addr = os.getenv('WATSON_FROM_ADDRESS', smtp_user)

    if len(message) > 150:
        message = message[:147] + '...'

    msg = MIMEText(message)
    msg['From'] = from_addr
    msg['To'] = sms_address
    msg['Subject'] = ''

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, [sms_address], msg.as_string())
        return {'success': True, 'to': sms_address}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def send_sms_to_contact(contact: dict, message: str) -> dict:
    """
    Send SMS to a People Registry / congregation contact dict.
    Contact must have 'name' and 'phone'; a legacy 'carrier' field (if present
    on the source row) is treated as a confirmed override and backfilled into
    the phone_carriers cache.
    """
    name = contact.get('name', 'Unknown')
    phone = contact.get('phone', '')
    carrier = contact.get('carrier', '')

    if not phone:
        return {'success': False, 'error': f"No phone number on file for {name}."}

    return send_sms(name, phone, carrier, message)

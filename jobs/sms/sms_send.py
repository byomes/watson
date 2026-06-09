import os
import re
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '../../.env'))

CARRIER_GATEWAYS = {
    'att':         'txt.att.net',
    'at&t':        'txt.att.net',
    'verizon':     'vtext.com',
    'tmobile':     'tmomail.net',
    't-mobile':    'tmomail.net',
    'sprint':      'messaging.sprintpcs.com',
    'uscellular':  'email.uscc.net',
    'us cellular': 'email.uscc.net',
    'cricket':     'sms.cricketwireless.net',
    'boost':       'sms.myboostmobile.com',
    'metropcs':    'mymetropcs.com',
    'metro pcs':   'mymetropcs.com',
}


def get_gateway(carrier: str) -> str | None:
    """Return SMS gateway domain for a carrier name, or None if unknown."""
    if not carrier:
        return None
    return CARRIER_GATEWAYS.get(carrier.lower().strip())


def clean_phone(phone: str) -> str:
    """Strip all non-digit characters from phone number."""
    return re.sub(r'\D', '', phone)


def send_sms(to_name: str, to_phone: str, carrier: str, message: str) -> dict:
    """
    Send an SMS via email-to-SMS gateway.
    Returns {'success': True} or {'success': False, 'error': str}
    """
    gateway = get_gateway(carrier)
    if not gateway:
        return {
            'success': False,
            'error': f"Unknown carrier '{carrier}' for {to_name}. Update their contact card with the correct carrier.",
        }

    phone_digits = clean_phone(to_phone)
    if len(phone_digits) == 11 and phone_digits.startswith('1'):
        phone_digits = phone_digits[1:]
    if len(phone_digits) != 10:
        return {
            'success': False,
            'error': f"Invalid phone number for {to_name}: {to_phone}",
        }

    sms_address = f"{phone_digits}@{gateway}"

    smtp_host = os.getenv('WATSON_SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.getenv('WATSON_SMTP_PORT', 587))
    smtp_user = os.getenv('WATSON_GMAIL_ADDRESS')
    smtp_pass = os.getenv('WATSON_GMAIL_APP_PASSWORD')
    from_addr = os.getenv('WATSON_FROM_ADDRESS', smtp_user)

    if len(message) > 160:
        message = message[:157] + '...'

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
    Send SMS to a People Registry contact dict.
    Contact must have 'name', 'phone', 'carrier' fields.
    """
    name = contact.get('name', 'Unknown')
    phone = contact.get('phone', '')
    carrier = contact.get('carrier', '')

    if not phone:
        return {'success': False, 'error': f"No phone number on file for {name}."}
    if not carrier or carrier.lower() in ('', 'unknown', 'other'):
        return {'success': False, 'error': f"No carrier set for {name}. Update their contact card."}

    return send_sms(name, phone, carrier, message)

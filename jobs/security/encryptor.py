"""jobs/security/encryptor.py — encrypt and decrypt text and files using Fernet."""
import logging
from pathlib import Path

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
KEY_PATH = REPO / "config" / "watson.key"


def _load_key() -> bytes:
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()
    return generate_key().encode()


def generate_key() -> str:
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(key)
    log.info("New Fernet key written to %s", KEY_PATH)
    return key.decode()


def encrypt_text(text: str) -> str:
    from cryptography.fernet import Fernet
    f = Fernet(_load_key())
    return f.encrypt(text.encode()).decode()


def decrypt_text(encrypted: str) -> str:
    from cryptography.fernet import Fernet
    f = Fernet(_load_key())
    return f.decrypt(encrypted.encode()).decode()


def encrypt_file(path: str) -> bool:
    p = Path(path)
    if not p.exists():
        log.error("encrypt_file: file not found: %s", path)
        return False
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_load_key())
        data = p.read_bytes()
        p.write_bytes(f.encrypt(data))
        log.info("Encrypted: %s", path)
        return True
    except Exception as exc:
        log.error("encrypt_file failed: %s", exc)
        return False


def run(message: str = None) -> str:
    return "Encryption tools ready. Use encrypt_text(), decrypt_text(), or encrypt_file()."

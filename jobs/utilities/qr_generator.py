import qrcode
import os
import uuid
import re
import base64
from datetime import datetime

# Constants
WATSON_ROOT = os.path.expanduser('~/watson')
DATA_DIR = os.path.join(WATSON_ROOT, 'data')
EXPORTS_DIR = os.path.join(DATA_DIR, 'exports')

# Ensure export directory exists
os.makedirs(EXPORTS_DIR, exist_ok=True)

def run(message: str) -> str:
    """
    Generates a QR code from the given message content and returns it as a base64 data URL.
    Strips any leading job slug (e.g., "qr_generator") from the message.
    """
    
    # 1. Strip leading job slug from the message if present
    # The slug pattern matches a word consisting of letters and underscores followed by a space at the start.
    slug_pattern = r"^[a-zA-Z_]+\s+"
    match = re.match(slug_pattern, message)
    if match:
        content_to_encode = message[match.end():].strip()
    else:
        content_to_encode = message.strip()

    if not content_to_encode:
        return "Error: No content provided for QR code generation after stripping slug."

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(content_to_encode)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Save to a temporary file, then read and encode
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    filename = f"qr_{timestamp}_{unique_id}.png"
    filepath = os.path.join(EXPORTS_DIR, filename)
    
    try:
        img.save(filepath)

        # Read the file bytes
        with open(filepath, 'rb') as f:
            png_bytes = f.read()
        
        # 2. Encode to base64 and return as data URL
        base64_encoded_string = base64.b64encode(png_bytes).decode('utf-8')
        return f"data:image/png;base64,{base64_encoded_string}"
    except Exception as e:
        return f"Error generating or encoding QR code: {e}"
    finally:
        # Clean up the temporary file
        if os.path.exists(filepath):
            os.remove(filepath)

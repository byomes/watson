import qrcode
import base64
from io import BytesIO

def run(message=None):
    """
    Generates a QR code for the given message and returns it as a base64 encoded PNG image.
    
    Args:
        message (str, optional): The data to encode in the QR code. Defaults to None.

    Returns:
        str: A base64 encoded PNG image string, suitable for embedding in HTML (data:image/png;base64,...).
             Returns an error message string if message is empty.
    """
    if not message:
        return "Error: No data provided to generate QR code."

    # Generate QR code
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(message)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Save image to a BytesIO object
    buffered = BytesIO()
    img.save(buffered, format="PNG")

    # Encode to base64
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")

    return f"data:image/png;base64,{img_str}"

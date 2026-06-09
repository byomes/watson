import qrcode
from io import BytesIO
import base64


def run(data: str) -> str:
    """
    Generates a QR code for the given data and returns it as a base64 encoded data URL.
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save image to a BytesIO object
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    
    # Return as data URL
    return f"data:image/png;base64,{img_str}"

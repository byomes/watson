import qrcode
import io
import base64

def run(message):
    # Bug fix 1: Strip the skill slug prefix "qr_generator " from the message
    # so only the actual content gets encoded in the QR code.
    if message.lower().startswith('qr_generator '):
        content_to_encode = message.split(' ', 1)[1]
    else:
        # Fallback if the prefix is unexpectedly missing, though for this skill
        # it's generally expected to be present when invoked via Watson.
        content_to_encode = message
    
    # Generate the QR code for the processed content
    img = qrcode.make(content_to_encode)

    # Bug fix 2: Save the QR image to an in-memory buffer as PNG
    # and convert it to a base64 data URL for inline rendering.
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0) # Rewind the buffer to the beginning

    # Encode the image data to base64
    qr_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

    # Construct the base64 data URL
    data_url = f"data:image/png;base64,{qr_base64}"

    return data_url

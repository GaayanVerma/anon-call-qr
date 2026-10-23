import io

import qrcode
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

TAGLINE = "Scan to call the owner. Your number stays private."


def tel_uri(twilio_number, code):
    # ",," makes the dialer pause before sending the extension. "#" is percent-encoded
    # because a literal "#" starts a URI fragment and many Android dialers drop it.
    return f"tel:{twilio_number},,{code}%23"


def fallback_text(twilio_number, code):
    return f"Or call {twilio_number} and enter code {code}#"


def qr_image(data, box_size=12):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def _png_bytes(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def qr_png(twilio_number, code):
    return _png_bytes(qr_image(tel_uri(twilio_number, code)))


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def sticker_png(twilio_number, code):
    width, height = 900, 1200
    sticker = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sticker)

    qr = qr_image(tel_uri(twilio_number, code)).resize((700, 700), Image.NEAREST)
    sticker.paste(qr, ((width - 700) // 2, 80))

    lines = [
        (TAGLINE, _font(30)),
        (f"Call {twilio_number}", _font(44)),
        (f"Enter code {code}#", _font(56)),
    ]
    y = 820
    for text, font in lines:
        text_width = draw.textlength(text, font=font)
        draw.text(((width - text_width) / 2, y), text, fill="black", font=font)
        y += font.size + 40 if hasattr(font, "size") else 60
    draw.rectangle([10, 10, width - 10, height - 10], outline="black", width=4)
    return _png_bytes(sticker)


def sticker_pdf(twilio_number, code):
    buf = io.BytesIO()
    page_w, page_h = A6
    pdf = canvas.Canvas(buf, pagesize=A6)
    pdf.setTitle("Anonymous call sticker")

    qr_size = 80 * mm
    qr = ImageReader(io.BytesIO(qr_png(twilio_number, code)))
    pdf.drawImage(qr, (page_w - qr_size) / 2, page_h - qr_size - 10 * mm, qr_size, qr_size)

    y = page_h - qr_size - 18 * mm
    pdf.setFont("Helvetica", 8.5)
    pdf.drawCentredString(page_w / 2, y, TAGLINE)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawCentredString(page_w / 2, y - 8 * mm, f"Call {twilio_number}")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(page_w / 2, y - 16 * mm, f"Enter code {code}#")
    pdf.rect(4 * mm, 4 * mm, page_w - 8 * mm, page_h - 8 * mm)
    pdf.showPage()
    pdf.save()
    return buf.getvalue()

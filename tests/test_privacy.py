"""The owner's real number may only appear inside <Dial><Number> and admin pages."""

import base64
import logging
import re
import zlib

from app.services import qr as qr_service

from .conftest import CALLER_PHONE, OWNER_PHONE, TWILIO_NUMBER


def leaks(text, phone=OWNER_PHONE):
    return phone in text or phone.lstrip("+") in text or phone[-10:] in text


def full_flow(voice, owner):
    responses = {}
    responses["incoming"] = voice("/voice/incoming")
    responses["retry"] = voice("/voice/incoming?attempt=1")
    responses["code"] = voice("/voice/incoming?attempt=1", Digits=owner.code)
    responses["menu_bad"] = voice("/voice/menu?attempt=0", Digits="7")
    responses["dial"] = voice("/voice/menu?attempt=1", Digits="1")
    responses["whisper"] = voice("/voice/whisper?reason=vehicle")
    responses["status"] = voice("/voice/status", DialCallStatus="no-answer")
    return {k: v.get_data(as_text=True) for k, v in responses.items()}


def test_number_only_inside_dial_number(app, voice, owner, caplog):
    caplog.set_level(logging.DEBUG)
    bodies = full_flow(voice, owner)

    for step, body in bodies.items():
        if step == "dial":
            continue
        assert not leaks(body), f"owner number leaked in {step}"

    dial = bodies["dial"]
    without_number_element = re.sub(r"<Number[^>]*>[^<]*</Number>", "", dial)
    assert not leaks(without_number_element)
    assert re.search(rf"<Dial[^>]*><Number[^>]*>{re.escape(OWNER_PHONE)}</Number></Dial>", dial)

    for record in caplog.records:
        assert not leaks(record.getMessage())
        assert not leaks(record.getMessage(), CALLER_PHONE)


def test_caller_number_never_in_responses(voice, owner):
    for body in full_flow(voice, owner).values():
        assert not leaks(body, CALLER_PHONE)


def test_error_responses_do_not_leak(voice, owner):
    voice("/voice/incoming")
    for body in (
        voice("/voice/incoming?attempt=0", Digits="000000").get_data(as_text=True),
        voice("/voice/menu").get_data(as_text=True),
        voice("/voice/status").get_data(as_text=True),
    ):
        assert not leaks(body)


def test_public_pages_do_not_leak(client, owner):
    for path in ("/admin/login", "/healthz", "/admin/", "/admin/calls", f"/admin/owners/{owner.id}/edit"):
        assert not leaks(client.get(path, follow_redirects=True).get_data(as_text=True))


def test_qr_and_sticker_content(owner):
    uri = qr_service.tel_uri(TWILIO_NUMBER, owner.code)
    assert uri == f"tel:{TWILIO_NUMBER},,{owner.code}%23"
    assert not leaks(uri)
    assert not leaks(qr_service.fallback_text(TWILIO_NUMBER, owner.code))

    pdf = qr_service.sticker_pdf(TWILIO_NUMBER, owner.code)
    assert pdf.startswith(b"%PDF")
    text = pdf_text(pdf)
    assert TWILIO_NUMBER in text and owner.code in text
    assert "Your number stays private." in text
    assert not leaks(text)

    png = qr_service.sticker_png(TWILIO_NUMBER, owner.code)
    assert png.startswith(b"\x89PNG")


def pdf_text(pdf):
    """Concatenate raw and ASCII85/Flate-decoded streams so content checks work either way."""
    chunks = [pdf.decode("latin-1")]
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        data = match.group(1)
        try:
            data = data.strip()
            if data.endswith(b"~>"):
                data = base64.a85decode(data[:-2])
            chunks.append(zlib.decompress(data).decode("latin-1"))
        except (ValueError, zlib.error):
            pass
    return "\n".join(chunks)

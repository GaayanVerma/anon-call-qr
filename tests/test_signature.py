import pytest
from twilio.request_validator import RequestValidator

from .conftest import TEST_CONFIG

ENDPOINTS = ["/voice/incoming", "/voice/menu", "/voice/whisper?reason=vehicle", "/voice/status"]
FORM = {"CallSid": "CA123", "From": "+919900011122"}


@pytest.mark.parametrize("path", ENDPOINTS)
def test_unsigned_requests_are_rejected(client, path):
    assert client.post(path, data=FORM).status_code == 403


@pytest.mark.parametrize("path", ENDPOINTS)
def test_bad_signatures_are_rejected(client, path):
    resp = client.post(path, data=FORM, headers={"X-Twilio-Signature": "forged"})
    assert resp.status_code == 403


def test_get_is_not_allowed(client):
    assert client.get("/voice/incoming").status_code == 405


def test_real_signature_over_public_url_is_accepted(client):
    url = TEST_CONFIG["PUBLIC_BASE_URL"] + "/voice/incoming?attempt=1"
    signature = RequestValidator(TEST_CONFIG["TWILIO_AUTH_TOKEN"]).compute_signature(url, FORM)
    resp = client.post("/voice/incoming?attempt=1", data=FORM, headers={"X-Twilio-Signature": signature})
    assert resp.status_code == 200
    assert "<Gather" in resp.get_data(as_text=True)


def test_signature_for_tampered_params_is_rejected(client):
    url = TEST_CONFIG["PUBLIC_BASE_URL"] + "/voice/incoming"
    signature = RequestValidator(TEST_CONFIG["TWILIO_AUTH_TOKEN"]).compute_signature(url, FORM)
    tampered = {**FORM, "Digits": "482913"}
    resp = client.post("/voice/incoming", data=tampered, headers={"X-Twilio-Signature": signature})
    assert resp.status_code == 403


def test_validator_receives_public_url(client, valid_signature):
    client.post("/voice/incoming?attempt=2", data=FORM, headers={"X-Twilio-Signature": "sig"})
    url, params, sig = valid_signature.return_value.validate.call_args.args
    assert url == "https://example.ngrok.app/voice/incoming?attempt=2"
    assert params == FORM and sig == "sig"

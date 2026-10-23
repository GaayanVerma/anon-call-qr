import re
from unittest.mock import patch

import pytest
from flask import g

from app import create_app
from app.extensions import db
from app.models import Owner

TWILIO_NUMBER = "+15005550006"
OWNER_PHONE = "+919812345678"
CALLER_PHONE = "+919900011122"
OWNER_CODE = "482913"

TEST_CONFIG = {
    "TESTING": True,
    "TWILIO_ACCOUNT_SID": "ACtest",
    "TWILIO_AUTH_TOKEN": "test-auth-token",
    "TWILIO_NUMBER": TWILIO_NUMBER,
    "PUBLIC_BASE_URL": "https://example.ngrok.app",
    "SECRET_KEY": "test-secret",
    "ADMIN_PASSWORD": "test-admin-password",
    "HASH_SALT": "test-salt",
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    "SESSION_COOKIE_SECURE": False,
}


@pytest.fixture
def app():
    app = create_app(TEST_CONFIG)
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def owner(app):
    owner = Owner(name="Asha", real_phone=OWNER_PHONE, code=OWNER_CODE, active=True)
    db.session.add(owner)
    db.session.commit()
    return owner


@pytest.fixture
def valid_signature():
    """Mock the Twilio request validator so every signed request is accepted."""
    with patch("app.security.RequestValidator") as validator_cls:
        validator_cls.return_value.validate.return_value = True
        yield validator_cls


@pytest.fixture
def voice(client, valid_signature):
    def post(path, sid="CA0001", caller=CALLER_PHONE, **form):
        data = {"CallSid": sid, "From": caller, "To": TWILIO_NUMBER, **form}
        return client.post(path, data=data, headers={"X-Twilio-Signature": "sig"})

    return post


def csrf_token(client, path):
    # The app fixture keeps one app context pushed, so `g` outlives requests; drop
    # Flask-WTF's cached token so it is re-read from the current session.
    g.pop("csrf_token", None)
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


@pytest.fixture
def admin(client):
    token = csrf_token(client, "/admin/login")
    resp = client.post("/admin/login", data={"password": TEST_CONFIG["ADMIN_PASSWORD"], "csrf_token": token})
    assert resp.status_code == 302
    return client

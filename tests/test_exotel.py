import logging
from datetime import timedelta

import pytest

from app import create_app
from app.exotel.routes import normalize_indian_number
from app.extensions import db
from app.models import Blocklist, CallLog, Owner, utcnow
from app.security import caller_hash

from .conftest import OWNER_CODE, OWNER_PHONE, TEST_CONFIG

SECRET = "s" * 32
EXOPHONE = "+918047112233"
CALLER_RAW = "09900011122"  # Exotel's format for +919900011122
BASE = f"/exotel/{SECRET}"

EXOTEL_CONFIG = {
    **{k: v for k, v in TEST_CONFIG.items() if not k.startswith("TWILIO") and k != "PUBLIC_BASE_URL"},
    "VOICE_PROVIDER": "exotel",
    "EXOTEL_NUMBER": EXOPHONE,
    "EXOTEL_WEBHOOK_SECRET": SECRET,
}


@pytest.fixture
def app(monkeypatch):
    for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_NUMBER", "PUBLIC_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    app = create_app(EXOTEL_CONFIG)
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def owner(app):
    owner = Owner(name="Asha", real_phone=OWNER_PHONE, code=OWNER_CODE, active=True)
    db.session.add(owner)
    db.session.commit()
    return owner


@pytest.fixture
def exotel(client):
    def get(step, sid="CAex1", caller=CALLER_RAW, **params):
        query = {"CallSid": sid, "CallFrom": caller, "CallTo": "08047112233", "Direction": "incoming", **params}
        return client.get(f"{BASE}/{step}", query_string=query)

    return get


def call_through_to_connect(exotel, owner, reason='"1"', sid="CAex1"):
    assert exotel("start", sid=sid).status_code == 200
    assert exotel("code", sid=sid, digits=f'"{owner.code}"').status_code == 200
    assert exotel("reason", sid=sid, digits=reason).status_code == 200
    return exotel("connect", sid=sid)


def test_config_requires_exotel_settings(monkeypatch):
    for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_NUMBER", "PUBLIC_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="EXOTEL_WEBHOOK_SECRET"):
        create_app({**EXOTEL_CONFIG, "EXOTEL_WEBHOOK_SECRET": "short"})
    with pytest.raises(RuntimeError, match="EXOTEL_NUMBER"):
        create_app({**EXOTEL_CONFIG, "EXOTEL_NUMBER": ""})


def test_twilio_webhooks_not_exposed(client):
    assert client.post("/voice/incoming").status_code == 404


@pytest.mark.parametrize("step", ["start", "gather-code", "code", "gather-reason", "reason", "connect", "status"])
def test_wrong_secret_is_404(client, step):
    assert client.get(f"/exotel/{'x' * 32}/{step}", query_string={"CallSid": "CA1"}).status_code == 404
    assert client.get(f"/exotel/{step}").status_code == 404


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("09900011122", "+919900011122"),
        ("9900011122", "+919900011122"),
        ("919900011122", "+919900011122"),
        ("+919900011122", "+919900011122"),
        ("+14155550100", "+14155550100"),
        ("", ""),
    ],
)
def test_normalize_indian_number(raw, expected):
    assert normalize_indian_number(raw) == expected


def test_start_logs_call_with_hash_of_normalized_number(app, exotel):
    assert exotel("start").status_code == 200
    call = CallLog.query.one()
    assert call.status == "started"
    assert call.caller_hash == caller_hash("+919900011122")


def test_start_retry_repeats_decision(app, exotel):
    assert exotel("start").status_code == 200
    assert exotel("start").status_code == 200
    assert CallLog.query.count() == 1


def test_blocked_caller_rejected(app, exotel):
    # Admins block by typing +91…; Exotel's 0-prefixed number must still match.
    db.session.add(Blocklist(caller_hash=caller_hash("+919900011122")))
    db.session.commit()
    assert exotel("start").status_code == 302
    assert CallLog.query.one().status == "blocked"


def test_caller_rate_limit(app, exotel):
    for i in range(3):
        assert exotel("start", sid=f"CA{i}").status_code == 200
    assert exotel("start", sid="CA3").status_code == 302
    assert CallLog.query.filter_by(twilio_call_sid="CA3").one().status == "rate_limited"


def test_old_calls_do_not_count(app, exotel):
    for i in range(5):
        db.session.add(
            CallLog(
                twilio_call_sid=f"CAold{i}",
                caller_hash=caller_hash("+919900011122"),
                status="completed",
                created_at=utcnow() - timedelta(hours=2),
            )
        )
    db.session.commit()
    assert exotel("start").status_code == 200


def test_gather_code_json(exotel):
    resp = exotel("gather-code")
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    body = resp.get_json()
    assert "Enter the 6-digit code printed on the sticker" in body["gather_prompt"]["text"]
    assert body["max_input_digits"] == 6 and body["finish_on_key"] == "#"
    assert body["repeat_menu"] == 2


def test_valid_code_with_quotes_and_hash(app, exotel, owner):
    exotel("start")
    assert exotel("code", digits=f'"{owner.code}#"').status_code == 200
    call = CallLog.query.one()
    assert call.owner_id == owner.id and call.status == "menu"


@pytest.mark.parametrize("value, status", [('"999999"', "invalid_code"), ('""', "no_code"), (None, "no_code")])
def test_bad_or_missing_code_rejected(app, exotel, owner, value, status):
    exotel("start")
    params = {} if value is None else {"digits": value}
    assert exotel("code", **params).status_code == 302
    assert CallLog.query.one().status == status


def test_one_guess_per_call(app, exotel, owner):
    exotel("start")
    exotel("code", digits='"999999"')
    assert exotel("code", digits=f'"{owner.code}"').status_code == 302


def test_inactive_owner_rejected(app, exotel, owner):
    owner.active = False
    db.session.commit()
    exotel("start")
    assert exotel("code", digits=f'"{owner.code}"').status_code == 302


def test_code_without_start_rejected(app, exotel, owner):
    assert exotel("code", digits=f'"{owner.code}"').status_code == 302


def test_owner_rate_limit(app, exotel, owner):
    for i in range(5):
        sid, caller = f"CA{i}", f"0990000000{i}"
        exotel("start", sid=sid, caller=caller)
        assert exotel("code", sid=sid, caller=caller, digits=f'"{owner.code}"').status_code == 200
    exotel("start", sid="CA9", caller="09900000009")
    assert exotel("code", sid="CA9", caller="09900000009", digits=f'"{owner.code}"').status_code == 302
    assert CallLog.query.filter_by(twilio_call_sid="CA9").one().status == "owner_rate_limited"


def test_gather_reason_json(exotel):
    body = exotel("gather-reason").get_json()
    assert "Press 1 for a vehicle problem" in body["gather_prompt"]["text"]
    assert body["max_input_digits"] == 1


def test_connect_returns_owner_and_whisper(app, exotel, owner):
    resp = call_through_to_connect(exotel, owner)
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    body = resp.get_json()
    assert body["destination"] == {"numbers": [OWNER_PHONE]}
    assert body["max_ringing_duration"] == 30 and body["record"] is False
    assert body["start_call_playback"]["playback_to"] == "callee"
    assert "about a vehicle problem" in body["start_call_playback"]["value"]
    call = CallLog.query.one()
    assert call.reason == "vehicle" and call.status == "dialing"


@pytest.mark.parametrize("value, reason", [('"2"', "lost_item"), ('"3"', "other"), ('"7"', "other"), ('""', "other")])
def test_connect_reason_mapping(app, exotel, owner, value, reason):
    call_through_to_connect(exotel, owner, reason=value)
    assert CallLog.query.one().reason == reason


def test_connect_defaults_to_other_when_reason_step_skipped(app, exotel, owner):
    exotel("start")
    exotel("code", digits=f'"{owner.code}"')
    assert "another matter" in exotel("connect").get_json()["start_call_playback"]["value"]


def test_reason_ignored_before_code_accepted(app, exotel, owner):
    exotel("start")
    assert exotel("reason", digits='"1"').status_code == 200
    assert CallLog.query.one().reason is None


def test_connect_refused_without_valid_code(app, exotel, owner):
    exotel("start")
    exotel("code", digits='"999999"')
    assert exotel("connect").status_code == 302


def test_connect_refused_if_owner_deactivated_mid_call(app, exotel, owner):
    exotel("start")
    exotel("code", digits=f'"{owner.code}"')
    owner.active = False
    db.session.commit()
    assert exotel("connect").status_code == 302


def test_connect_cannot_be_replayed(app, exotel, owner):
    call_through_to_connect(exotel, owner)
    assert exotel("connect").status_code == 302


def test_status_records_result(app, exotel, owner):
    call_through_to_connect(exotel, owner)
    assert exotel("status", DialCallStatus="completed", DialCallDuration="42").status_code == 200
    call = CallLog.query.one()
    assert call.status == "completed" and call.duration == 42


def test_status_no_answer(app, exotel, owner):
    call_through_to_connect(exotel, owner)
    exotel("status", DialCallStatus="no-answer", DialCallDuration="0")
    assert CallLog.query.one().status == "no-answer"


def test_internal_error_rejects(app, exotel, owner, monkeypatch):
    exotel("start")
    monkeypatch.setattr("app.exotel.routes.find_active_owner", lambda code: 1 / 0)
    assert exotel("code", digits=f'"{owner.code}"').status_code == 302


def test_owner_number_only_in_connect_destination(app, exotel, owner, caplog):
    caplog.set_level(logging.DEBUG)
    bodies = {
        "start": exotel("start"),
        "gather-code": exotel("gather-code"),
        "code": exotel("code", digits=f'"{owner.code}"'),
        "gather-reason": exotel("gather-reason"),
        "reason": exotel("reason", digits='"1"'),
        "connect": exotel("connect"),
        "status": exotel("status", DialCallStatus="no-answer"),
    }
    texts = {k: v.get_data(as_text=True) for k, v in bodies.items()}
    for step, text in texts.items():
        if step != "connect":
            assert OWNER_PHONE not in text and OWNER_PHONE[-10:] not in text, step
    connect = bodies["connect"].get_json()
    connect.pop("destination")
    assert OWNER_PHONE[-10:] not in str(connect)
    for record in caplog.records:
        message = record.getMessage()
        assert OWNER_PHONE[-10:] not in message and "9900011122" not in message


def test_sticker_uses_exophone(app, client, owner):
    from .conftest import csrf_token

    token = csrf_token(client, "/admin/login")
    client.post("/admin/login", data={"password": TEST_CONFIG["ADMIN_PASSWORD"], "csrf_token": token})
    page = client.get("/admin/").get_data(as_text=True)
    assert EXOPHONE in page
    assert client.get(f"/admin/owners/{owner.id}/sticker.pdf").status_code == 200

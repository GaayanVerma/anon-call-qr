from datetime import timedelta

from app.abuse import caller_rate_exceeded, is_blocked, owner_rate_exceeded
from app.extensions import db
from app.models import Blocklist, CallLog, utcnow
from app.security import caller_hash

from .conftest import CALLER_PHONE


def add_calls(n, caller="h" * 64, owner_id=None, age=timedelta(minutes=5), prefix="CAold"):
    for i in range(n):
        db.session.add(
            CallLog(
                twilio_call_sid=f"{prefix}{i}",
                caller_hash=caller,
                owner_id=owner_id,
                status="completed",
                created_at=utcnow() - age,
            )
        )
    db.session.commit()


def test_caller_hash_is_salted_and_stable(app):
    hashed = caller_hash(CALLER_PHONE)
    assert hashed == caller_hash(CALLER_PHONE)
    assert CALLER_PHONE not in hashed and len(hashed) == 64
    app.config["HASH_SALT"] = "other-salt"
    assert caller_hash(CALLER_PHONE) != hashed


def test_caller_limit_counts_only_last_hour(app):
    add_calls(2)
    add_calls(5, age=timedelta(hours=2), prefix="CAancient")
    assert not caller_rate_exceeded("h" * 64)
    add_calls(1, prefix="CAnew")
    assert caller_rate_exceeded("h" * 64)


def test_caller_limit_excludes_current_call(app):
    add_calls(3)
    assert not caller_rate_exceeded("h" * 64, exclude_sid="CAold2")
    assert caller_rate_exceeded("h" * 64, exclude_sid="CAother")


def test_owner_limit(app, owner):
    add_calls(4, owner_id=owner.id)
    assert not owner_rate_exceeded(owner.id)
    add_calls(1, owner_id=owner.id, prefix="CAfifth")
    assert owner_rate_exceeded(owner.id)


def test_blocklist_lookup(app):
    hashed = caller_hash(CALLER_PHONE)
    assert not is_blocked(hashed)
    db.session.add(Blocklist(caller_hash=hashed, note="spam"))
    db.session.commit()
    assert is_blocked(hashed)


def test_blocklisted_caller_gets_polite_rejection(app, voice):
    db.session.add(Blocklist(caller_hash=caller_hash(CALLER_PHONE)))
    db.session.commit()
    body = voice("/voice/incoming").get_data(as_text=True)
    assert "unable to connect calls from this number" in body
    assert "<Hangup />" in body and "<Gather" not in body
    assert CallLog.query.filter_by(twilio_call_sid="CA0001").one().status == "blocked"


def test_fourth_call_in_an_hour_is_rejected(app, owner, voice):
    for i in range(3):
        assert "<Gather" in voice("/voice/incoming", sid=f"CA{i}").get_data(as_text=True)
    body = voice("/voice/incoming", sid="CA3").get_data(as_text=True)
    assert "too many calls" in body and "<Hangup />" in body


def test_other_callers_are_not_affected(app, owner, voice):
    for i in range(3):
        voice("/voice/incoming", sid=f"CA{i}")
    assert "<Gather" in voice("/voice/incoming", sid="CAx", caller="+919811111111").get_data(as_text=True)


def test_sixth_call_for_an_owner_is_rejected(app, owner, voice):
    for i in range(5):
        sid = f"CA{i}"
        voice("/voice/incoming", sid=sid, caller=f"+91990000000{i}")
        body = voice("/voice/incoming", sid=sid, caller=f"+91990000000{i}", Digits=owner.code).get_data(as_text=True)
        assert "Press 1" in body
    voice("/voice/incoming", sid="CA9", caller="+919900000009")
    body = voice("/voice/incoming", sid="CA9", caller="+919900000009", Digits=owner.code).get_data(as_text=True)
    assert "owner has received too many calls" in body and "<Hangup />" in body
    assert "<Dial" not in body

import xml.etree.ElementTree as ET
from unittest.mock import patch

from app.extensions import db
from app.models import CallLog

from .conftest import OWNER_PHONE, TWILIO_NUMBER


def parse(resp):
    assert resp.status_code == 200
    assert resp.mimetype == "text/xml"
    return ET.fromstring(resp.get_data(as_text=True))


def says(root):
    return " ".join(el.text or "" for el in root.iter("Say"))


def test_initial_gather(voice):
    root = parse(voice("/voice/incoming"))
    gather = root.find("Gather")
    assert gather.attrib == {
        "input": "dtmf",
        "numDigits": "6",
        "finishOnKey": "#",
        "timeout": "6",
        "action": "/voice/incoming?attempt=0",
        "method": "POST",
    }
    assert root.find("Redirect").text == "/voice/incoming?attempt=1"
    assert CallLog.query.filter_by(twilio_call_sid="CA0001").one().status == "started"


def test_no_digits_prompts_for_code_then_hangs_up(voice):
    voice("/voice/incoming")
    for attempt in (1, 2):
        root = parse(voice(f"/voice/incoming?attempt={attempt}"))
        assert "Enter the 6-digit code printed on the sticker" in says(root)
        assert root.find("Gather").attrib["action"] == f"/voice/incoming?attempt={attempt}"
    root = parse(voice("/voice/incoming?attempt=3"))
    assert root.find("Gather") is None and root.find("Hangup") is not None
    assert "did not receive a code" in says(root)
    assert CallLog.query.one().status == "no_code"


def test_unknown_code(voice, owner):
    voice("/voice/incoming")
    root = parse(voice("/voice/incoming?attempt=0", Digits="999999"))
    assert "This code is not valid" in says(root)
    assert root.find("Hangup") is not None and root.find("Dial") is None
    assert CallLog.query.one().status == "invalid_code"


def test_wrong_code_ends_the_call_so_no_more_guesses(voice, owner):
    voice("/voice/incoming")
    voice("/voice/incoming?attempt=0", Digits="999999")
    root = parse(voice("/voice/incoming?attempt=0", Digits=owner.code))
    assert root.find("Hangup") is not None and root.find("Gather") is None


def test_inactive_owner(voice, owner):
    owner.active = False
    db.session.commit()
    voice("/voice/incoming")
    root = parse(voice("/voice/incoming?attempt=0", Digits=owner.code))
    assert "This code is not valid" in says(root)
    assert root.find("Hangup") is not None


def test_valid_code_plays_reason_menu(voice, owner):
    voice("/voice/incoming")
    root = parse(voice("/voice/incoming?attempt=0", Digits=owner.code + "#"))
    gather = root.find("Gather")
    assert gather.attrib["numDigits"] == "1"
    assert gather.attrib["action"] == "/voice/menu?attempt=0"
    text = says(root)
    assert "Press 1 for a vehicle problem" in text and "Press 2 for a lost item" in text and "Press 3" in text
    call = CallLog.query.one()
    assert call.owner_id == owner.id and call.status == "menu"


def test_invalid_menu_choice_reprompts_then_defaults_to_other(voice, owner):
    voice("/voice/incoming")
    voice("/voice/incoming?attempt=0", Digits=owner.code)
    root = parse(voice("/voice/menu?attempt=0", Digits="9"))
    assert "not a valid choice" in says(root)
    assert root.find("Gather").attrib["action"] == "/voice/menu?attempt=1"
    root = parse(voice("/voice/menu?attempt=2"))
    assert root.find("Dial") is not None
    assert CallLog.query.one().reason == "other"


def dial_response(voice, owner, choice="1"):
    voice("/voice/incoming")
    voice("/voice/incoming?attempt=0", Digits=owner.code)
    return parse(voice("/voice/menu?attempt=0", Digits=choice))


def test_menu_dials_owner_with_whisper(voice, owner):
    root = dial_response(voice, owner)
    assert "Connecting you to the owner" in says(root)
    dial = root.find("Dial")
    assert dial.attrib == {"callerId": TWILIO_NUMBER, "timeout": "30", "action": "/voice/status", "method": "POST"}
    number = dial.find("Number")
    assert number.text == OWNER_PHONE
    assert number.attrib == {"url": "/voice/whisper?reason=vehicle", "method": "POST"}
    call = CallLog.query.one()
    assert call.reason == "vehicle" and call.status == "dialing"


def test_menu_reason_lost_item(voice, owner):
    number = dial_response(voice, owner, "2").find("Dial/Number")
    assert number.attrib["url"] == "/voice/whisper?reason=lost_item"


def test_menu_without_code_step_is_refused(voice, owner):
    root = parse(voice("/voice/menu?attempt=0", Digits="1"))
    assert root.find("Dial") is None and root.find("Hangup") is not None


def test_menu_when_owner_deactivated_mid_call(voice, owner):
    voice("/voice/incoming")
    voice("/voice/incoming?attempt=0", Digits=owner.code)
    owner.active = False
    db.session.commit()
    root = parse(voice("/voice/menu?attempt=0", Digits="1"))
    assert root.find("Dial") is None and "not valid" in says(root)


def test_whisper(voice):
    root = parse(voice("/voice/whisper?reason=vehicle"))
    assert "anonymous call from your QR sticker about a vehicle problem" in says(root)
    assert root.find("Hangup") is None
    assert "another matter" in says(parse(voice("/voice/whisper?reason=bogus")))


def test_status_completed(voice, owner):
    dial_response(voice, owner)
    root = parse(voice("/voice/status", DialCallStatus="completed", DialCallDuration="42"))
    assert root.find("Hangup") is not None and says(root) == ""
    call = CallLog.query.one()
    assert call.status == "completed" and call.duration == 42


def test_status_no_answer(voice, owner):
    dial_response(voice, owner)
    root = parse(voice("/voice/status", DialCallStatus="no-answer"))
    assert "owner is not available" in says(root) and root.find("Hangup") is not None
    assert CallLog.query.one().status == "no-answer"


def test_status_failed(voice, owner):
    dial_response(voice, owner)
    root = parse(voice("/voice/status", DialCallStatus="failed"))
    assert "could not connect your call" in says(root) and root.find("Hangup") is not None


def test_internal_error_plays_message_and_hangs_up(voice, owner):
    voice("/voice/incoming")
    with patch("app.voice.routes.find_active_owner", side_effect=RuntimeError("db down")):
        root = parse(voice("/voice/incoming?attempt=0", Digits=owner.code))
    assert "something went wrong" in says(root) and root.find("Hangup") is not None


def test_missing_call_sid(voice):
    root = parse(voice("/voice/incoming", sid=""))
    assert root.find("Hangup") is not None

"""Twilio Voice webhooks.

Call state lives in the call_log row keyed by CallSid, so owner ids and phone
numbers never appear in callback URLs. The owner's real number is only ever
emitted inside the <Dial><Number> instruction.
"""

import logging

from flask import Blueprint, Response, current_app, request, url_for
from twilio.twiml.voice_response import Dial, Gather, VoiceResponse
from werkzeug.exceptions import HTTPException

from ..abuse import caller_rate_exceeded, is_blocked, owner_rate_exceeded
from ..extensions import db
from ..models import CallLog
from ..prompts import (
    REASONS,
    REASON_LABELS,
    MSG_GREETING,
    MSG_ENTER_CODE,
    MSG_NO_CODE,
    MSG_INVALID_CODE,
    MSG_BLOCKED,
    MSG_CALLER_LIMIT,
    MSG_OWNER_LIMIT,
    MSG_REASON,
    MSG_BAD_CHOICE,
    MSG_CONNECTING,
    MSG_NO_ANSWER,
    MSG_FAILED,
    MSG_ERROR,
)
from ..security import caller_hash, twilio_signature_required
from ..services.codes import find_active_owner

log = logging.getLogger(__name__)

bp = Blueprint("voice", __name__, url_prefix="/voice")

MAX_CODE_RETRIES = 2
MAX_MENU_RETRIES = 2


def twiml(response):
    return Response(str(response), mimetype="text/xml")


def say(target, text):
    target.say(text, voice=current_app.config["TTS_VOICE"], language=current_app.config["TTS_LANGUAGE"])


def say_and_hangup(text):
    response = VoiceResponse()
    say(response, text)
    response.hangup()
    return twiml(response)


def bounded_int(value, upper):
    try:
        return max(0, min(int(value), upper + 1))
    except (TypeError, ValueError):
        return 0


def current_call():
    sid = request.form.get("CallSid", "")
    return CallLog.query.filter_by(twilio_call_sid=sid).first() if sid else None


@bp.errorhandler(Exception)
def handle_error(error):
    if isinstance(error, HTTPException):
        return error
    db.session.rollback()
    log.exception("Voice webhook error for call %s", request.form.get("CallSid", "?"))
    return say_and_hangup(MSG_ERROR)


def gather_code(attempt):
    response = VoiceResponse()
    gather = Gather(
        input="dtmf",
        num_digits=6,
        finish_on_key="#",
        timeout=6,
        action=url_for("voice.incoming", attempt=attempt),
        method="POST",
    )
    say(gather, MSG_GREETING if attempt == 0 else MSG_ENTER_CODE)
    response.append(gather)
    # Reached only when no digits arrive before the timeout.
    response.redirect(url_for("voice.incoming", attempt=attempt + 1), method="POST")
    return twiml(response)


def gather_reason(attempt, prefix=None):
    response = VoiceResponse()
    gather = Gather(
        input="dtmf",
        num_digits=1,
        timeout=6,
        action=url_for("voice.menu", attempt=attempt),
        method="POST",
    )
    if prefix:
        say(gather, prefix)
    say(gather, MSG_REASON)
    response.append(gather)
    response.redirect(url_for("voice.menu", attempt=attempt + 1), method="POST")
    return twiml(response)


@bp.route("/incoming", methods=["POST"])
@twilio_signature_required
def incoming():
    sid = request.form.get("CallSid", "")
    if not sid:
        return say_and_hangup(MSG_ERROR)
    attempt = bounded_int(request.args.get("attempt"), MAX_CODE_RETRIES)
    digits = (request.form.get("Digits") or "").strip().rstrip("#")

    call = current_call()
    if call is None:
        hashed = caller_hash(request.form.get("From"))
        call = CallLog(twilio_call_sid=sid, caller_hash=hashed, status="started")
        db.session.add(call)
        if is_blocked(hashed):
            call.status = "blocked"
            db.session.commit()
            log.info("Blocked caller on call %s", sid)
            return say_and_hangup(MSG_BLOCKED)
        if caller_rate_exceeded(hashed, exclude_sid=sid):
            call.status = "rate_limited"
            db.session.commit()
            log.info("Caller rate limit hit on call %s", sid)
            return say_and_hangup(MSG_CALLER_LIMIT)
        db.session.commit()
    elif call.status != "started":
        # Replayed or out-of-order callback: the code step is already finished.
        return say_and_hangup(MSG_ERROR)

    if digits:
        return handle_code(call, digits)

    if attempt > MAX_CODE_RETRIES:
        call.status = "no_code"
        db.session.commit()
        return say_and_hangup(MSG_NO_CODE)
    return gather_code(attempt)


def handle_code(call, digits):
    owner = find_active_owner(digits)
    if owner is None:
        call.status = "invalid_code"
        db.session.commit()
        log.info("Invalid or inactive code on call %s", call.twilio_call_sid)
        return say_and_hangup(MSG_INVALID_CODE)

    call.owner_id = owner.id
    if owner_rate_exceeded(owner.id, exclude_sid=call.twilio_call_sid):
        call.status = "owner_rate_limited"
        db.session.commit()
        log.info("Owner %s rate limit hit on call %s", owner.id, call.twilio_call_sid)
        return say_and_hangup(MSG_OWNER_LIMIT)

    call.status = "menu"
    db.session.commit()
    log.info("Code accepted for owner %s on call %s", owner.id, call.twilio_call_sid)
    return gather_reason(0)


@bp.route("/menu", methods=["POST"])
@twilio_signature_required
def menu():
    call = current_call()
    if call is None or call.status != "menu" or call.owner is None:
        return say_and_hangup(MSG_ERROR)
    owner = call.owner
    if not owner.active:
        call.status = "invalid_code"
        db.session.commit()
        return say_and_hangup(MSG_INVALID_CODE)

    attempt = bounded_int(request.args.get("attempt"), MAX_MENU_RETRIES)
    reason = REASONS.get((request.form.get("Digits") or "").strip())
    if reason is None:
        if attempt < MAX_MENU_RETRIES:
            prefix = MSG_BAD_CHOICE if request.form.get("Digits") else None
            return gather_reason(attempt + 1 if prefix else attempt, prefix=prefix)
        reason = "other"

    call.reason = reason
    call.status = "dialing"
    db.session.commit()

    response = VoiceResponse()
    say(response, MSG_CONNECTING)
    dial = Dial(
        caller_id=current_app.config["TWILIO_NUMBER"],
        timeout=30,
        action=url_for("voice.status"),
        method="POST",
    )
    dial.number(owner.real_phone, url=url_for("voice.whisper", reason=reason), method="POST")
    response.append(dial)
    return twiml(response)


@bp.route("/whisper", methods=["POST"])
@twilio_signature_required
def whisper():
    """Played to the owner only, before the two legs are bridged."""
    label = REASON_LABELS.get(request.args.get("reason", ""), REASON_LABELS["other"])
    response = VoiceResponse()
    say(response, f"You have an anonymous call from your QR sticker about {label}. Connecting now.")
    return twiml(response)


@bp.route("/status", methods=["POST"])
@twilio_signature_required
def status():
    dial_status = (request.form.get("DialCallStatus") or "failed").strip()
    call = current_call()
    if call is not None:
        call.status = dial_status[:32]
        call.duration = bounded_int(request.form.get("DialCallDuration"), 10**7)
        db.session.commit()
    log.info("Dial finished with %s on call %s", dial_status, request.form.get("CallSid", "?"))

    if dial_status == "completed":
        response = VoiceResponse()
        response.hangup()
        return twiml(response)
    if dial_status in ("busy", "no-answer", "canceled"):
        return say_and_hangup(MSG_NO_ANSWER)
    return say_and_hangup(MSG_FAILED)

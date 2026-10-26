"""Exotel call-flow webhooks.

Exotel call flows are built in the Exotel dashboard (App Bazaar). The quick flow
needs only two applets that call the app:

    Gather (URL)     /gather-code   prompt for the 6-digit code
    Connect (URL)    /direct        all checks + owner's number, as JSON; non-200 = don't dial

The full flow adds a reason menu and separate rejection messages:

    Passthru (sync)  /start         blocklist + caller rate limit   200 = continue, 302 = reject
    Gather (URL)     /gather-code   prompt for the 6-digit code
    Passthru (sync)  /code          code lookup + owner rate limit  200 = continue, 302 = reject
    Gather (URL)     /gather-reason reason menu
    Passthru (sync)  /reason        stores the reason digit         always 200
    Connect (URL)    /connect       owner's number + whisper, as JSON
    Passthru (async) /status        records the dial result

Exotel does not sign its requests, so every URL contains EXOTEL_WEBHOOK_SECRET as a
path segment and requests with the wrong secret get a 404. Call state lives in the
call_log row keyed by CallSid. The owner's real number only ever appears in the
/connect response's destination list.
"""

import hmac
import logging
import re

from flask import Blueprint, abort, current_app, jsonify, request, url_for
from werkzeug.exceptions import HTTPException

from ..abuse import caller_rate_exceeded, is_blocked, owner_rate_exceeded
from ..extensions import db
from ..models import CallLog
from ..prompts import MSG_ENTER_CODE, MSG_REASON, REASON_LABELS, REASONS
from ..security import caller_hash
from ..services.codes import find_active_owner

log = logging.getLogger(__name__)

bp = Blueprint("exotel", __name__, url_prefix="/exotel/<secret>")

RING_SECONDS = 30
FINAL_DIAL_STATUSES = {"completed", "busy", "no-answer", "failed", "canceled"}
CODE_PROMPT_REPEATS = 2
REASON_PROMPT_REPEATS = 2


@bp.url_value_preprocessor
def check_secret(endpoint, values):
    secret = (values or {}).pop("secret", "")
    expected = current_app.config["EXOTEL_WEBHOOK_SECRET"]
    if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
        log.warning("Rejected Exotel request with a wrong secret")
        abort(404)


@bp.errorhandler(Exception)
def handle_error(error):
    if isinstance(error, HTTPException):
        return error
    db.session.rollback()
    log.exception("Exotel webhook error for call %s", param("CallSid") or "?")
    # Non-200 sends Passthru to its reject branch and Connect to "We didn't dial anyone".
    return reject()


def param(name):
    # Exotel sends GET query parameters; accept form posts too.
    return (request.values.get(name) or "").strip()


def digits():
    # Exotel wraps Gather input in double quotes, e.g. "482913".
    return param("digits").strip('"').strip().rstrip("#")


def normalize_indian_number(raw):
    """Exotel sends numbers like 09876543210; hash them in the same +91 form admins type."""
    digits_only = re.sub(r"\D", "", raw or "")
    if len(digits_only) == 10:
        return "+91" + digits_only
    if len(digits_only) == 11 and digits_only.startswith("0"):
        return "+91" + digits_only[1:]
    if len(digits_only) == 12 and digits_only.startswith("91"):
        return "+" + digits_only
    return ("+" + digits_only) if digits_only else ""


def current_call():
    sid = param("CallSid")
    return CallLog.query.filter_by(twilio_call_sid=sid).first() if sid else None


def proceed():
    return "OK", 200


def reject():
    # Exotel only looks at the status code; the Location header keeps it valid HTTP.
    return "", 302, {"Location": "/"}


@bp.route("/start", methods=["GET", "POST"])
def start():
    sid = param("CallSid")
    if not sid:
        return reject()
    existing = current_call()
    if existing is not None:
        # Exotel may retry a slow request; repeat the original decision.
        return proceed() if existing.status == "started" else reject()

    hashed = caller_hash(normalize_indian_number(param("CallFrom") or param("From")))
    call = CallLog(twilio_call_sid=sid, caller_hash=hashed, status="started")
    db.session.add(call)
    if is_blocked(hashed):
        call.status = "blocked"
    elif caller_rate_exceeded(hashed, exclude_sid=sid):
        call.status = "rate_limited"
    db.session.commit()

    if call.status != "started":
        log.info("Rejected call %s: %s", sid, call.status)
        return reject()
    return proceed()


@bp.route("/gather-code", methods=["GET", "POST"])
def gather_code():
    return jsonify(
        gather_prompt={"text": f"Hello. {MSG_ENTER_CODE}"},
        max_input_digits=6,
        finish_on_key="#",
        input_timeout=6,
        repeat_menu=CODE_PROMPT_REPEATS,
        repeat_gather_prompt={"text": MSG_ENTER_CODE},
    )


@bp.route("/code", methods=["GET", "POST"])
def code():
    call = current_call()
    if call is None or call.status != "started":
        return reject()

    owner = find_active_owner(digits())
    if owner is None:
        call.status = "invalid_code" if digits() else "no_code"
        db.session.commit()
        log.info("Invalid, inactive or missing code on call %s", call.twilio_call_sid)
        return reject()

    call.owner_id = owner.id
    if owner_rate_exceeded(owner.id, exclude_sid=call.twilio_call_sid):
        call.status = "owner_rate_limited"
        db.session.commit()
        log.info("Owner %s rate limit hit on call %s", owner.id, call.twilio_call_sid)
        return reject()

    call.status = "menu"
    db.session.commit()
    log.info("Code accepted for owner %s on call %s", owner.id, call.twilio_call_sid)
    return proceed()


@bp.route("/gather-reason", methods=["GET", "POST"])
def gather_reason():
    return jsonify(
        gather_prompt={"text": MSG_REASON},
        max_input_digits=1,
        finish_on_key="",
        input_timeout=6,
        repeat_menu=REASON_PROMPT_REPEATS,
    )


@bp.route("/reason", methods=["GET", "POST"])
def reason():
    # Exotel only passes `digits` to the applet right after a Gather, so the reason is
    # stored here, leaving the flow free to play a greeting before Connect.
    call = current_call()
    if call is not None and call.status == "menu":
        call.reason = REASONS.get(digits(), "other")
        db.session.commit()
    return proceed()


@bp.route("/connect", methods=["GET", "POST"])
def connect():
    call = current_call()
    if call is None or call.status != "menu" or call.owner is None or not call.owner.active:
        return reject()

    reason = call.reason if call.reason in REASON_LABELS else "other"
    call.reason = reason
    call.status = "dialing"
    db.session.commit()

    return dial_response(call)


def dial_response(call):
    about = f" about {REASON_LABELS[call.reason]}" if call.reason in REASON_LABELS else ""
    whisper = f"You have an anonymous call from your QR sticker{about}. Connecting now."
    status_url = url_for(
        "exotel.status", secret=current_app.config["EXOTEL_WEBHOOK_SECRET"], _external=True, _scheme="https"
    )
    return jsonify(
        destination={"numbers": [call.owner.real_phone]},
        max_ringing_duration=RING_SECONDS,
        record=False,
        start_call_playback={"playback_to": "callee", "type": "text", "value": whisper},
        # Exotel reports dial start/end here, so the quick flow needs no status applet.
        dial_passthru_event_url=status_url,
    )


@bp.route("/direct", methods=["GET", "POST"])
def direct():
    """Quick flow: Connect applet right after the code Gather. Runs every check at once."""
    sid = param("CallSid")
    if not sid:
        return reject()
    call = current_call()
    if call is not None:
        # Exotel retries via the fallback URL if the first response was slow.
        if call.status == "dialing" and call.owner is not None and call.owner.active:
            return dial_response(call)
        return reject()

    hashed = caller_hash(normalize_indian_number(param("CallFrom") or param("From")))
    call = CallLog(twilio_call_sid=sid, caller_hash=hashed, status="started")
    db.session.add(call)
    if is_blocked(hashed):
        call.status = "blocked"
    elif caller_rate_exceeded(hashed, exclude_sid=sid):
        call.status = "rate_limited"
    else:
        owner = find_active_owner(digits())
        if owner is None:
            call.status = "invalid_code" if digits() else "no_code"
        else:
            call.owner_id = owner.id
            if owner_rate_exceeded(owner.id, exclude_sid=sid):
                call.status = "owner_rate_limited"
            else:
                call.status = "dialing"
    db.session.commit()

    if call.status != "dialing":
        log.info("Rejected call %s: %s", sid, call.status)
        return reject()
    log.info("Dialling owner %s for call %s", call.owner_id, sid)
    return dial_response(call)


@bp.route("/status", methods=["GET", "POST"])
def status():
    # Called by the full flow's async Passthru, and by Connect's dial events (quick flow).
    # Only final results are recorded; dial-start events are ignored.
    dial_status = (param("DialCallStatus") or param("Status") or param("CallStatus")).lower()
    if dial_status not in FINAL_DIAL_STATUSES:
        return proceed()
    call = current_call()
    if call is not None and call.status == "dialing":
        call.status = dial_status
        try:
            call.duration = max(0, int(param("DialCallDuration") or param("ConversationDuration") or 0))
        except ValueError:
            call.duration = 0
        db.session.commit()
    log.info("Dial finished with %s on call %s", dial_status, param("CallSid") or "?")
    return proceed()

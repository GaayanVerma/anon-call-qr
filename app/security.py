import hashlib
import hmac
import logging
import re
from functools import wraps

from flask import abort, current_app, request
from twilio.request_validator import RequestValidator

log = logging.getLogger(__name__)


def caller_hash(phone):
    """Salted HMAC of the caller's number, so abuse can be tracked without storing it."""
    salt = current_app.config["HASH_SALT"].encode()
    return hmac.new(salt, (phone or "anonymous").strip().encode(), hashlib.sha256).hexdigest()


def public_url():
    """The URL Twilio signed: PUBLIC_BASE_URL + path + query (works behind ngrok/proxies)."""
    full_path = request.full_path
    if full_path.endswith("?"):
        full_path = full_path[:-1]
    return current_app.config["PUBLIC_BASE_URL"] + full_path


def twilio_signature_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        signature = request.headers.get("X-Twilio-Signature", "")
        if not signature:
            log.warning("Rejected unsigned request to %s", request.path)
            abort(403)
        validator = RequestValidator(current_app.config["TWILIO_AUTH_TOKEN"])
        if not validator.validate(public_url(), request.form.to_dict(), signature):
            log.warning("Rejected request with invalid Twilio signature to %s", request.path)
            abort(403)
        return view(*args, **kwargs)

    return wrapper


class PhoneRedactionFilter(logging.Filter):
    """Defence in depth: mask anything that looks like a phone number in log output."""

    _pattern = re.compile(r"\+?\d[\d\s-]{7,}\d")

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = self._pattern.sub("[redacted]", message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True

from datetime import timedelta

from flask import current_app

from .extensions import db
from .models import Blocklist, CallLog, utcnow

WINDOW = timedelta(hours=1)


def is_blocked(caller_hash):
    return db.session.get(Blocklist, caller_hash) is not None


def _recent(query, exclude_sid):
    query = query.filter(CallLog.created_at >= utcnow() - WINDOW)
    if exclude_sid:
        query = query.filter(CallLog.twilio_call_sid != exclude_sid)
    return query.count()


def caller_rate_exceeded(caller_hash, exclude_sid=None):
    limit = current_app.config["MAX_CALLS_PER_CALLER_PER_HOUR"]
    return _recent(CallLog.query.filter_by(caller_hash=caller_hash), exclude_sid) >= limit


def owner_rate_exceeded(owner_id, exclude_sid=None):
    limit = current_app.config["MAX_CALLS_PER_OWNER_PER_HOUR"]
    return _recent(CallLog.query.filter_by(owner_id=owner_id), exclude_sid) >= limit

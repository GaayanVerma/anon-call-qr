from datetime import datetime, timezone

from .extensions import db


def utcnow():
    # Naive UTC timestamps keep comparisons simple on SQLite.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Owner(db.Model):
    __tablename__ = "owners"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    real_phone = db.Column(db.String(20), nullable=False)
    code = db.Column(db.String(6), unique=True, nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    calls = db.relationship("CallLog", back_populates="owner", lazy="dynamic")

    @property
    def masked_phone(self):
        return mask_phone(self.real_phone)

    def __repr__(self):
        # Never include real_phone in reprs: they end up in logs and tracebacks.
        return f"<Owner id={self.id} code={self.code} active={self.active}>"


class CallLog(db.Model):
    __tablename__ = "call_log"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("owners.id"), nullable=True, index=True)
    caller_hash = db.Column(db.String(64), nullable=False, index=True)
    twilio_call_sid = db.Column(db.String(64), unique=True, nullable=False, index=True)
    reason = db.Column(db.String(32), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="started")
    duration = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    owner = db.relationship("Owner", back_populates="calls")

    def __repr__(self):
        return f"<CallLog id={self.id} sid={self.twilio_call_sid} status={self.status}>"


class Blocklist(db.Model):
    __tablename__ = "blocklist"

    caller_hash = db.Column(db.String(64), primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    note = db.Column(db.String(255), nullable=True)


def mask_phone(phone):
    if not phone:
        return ""
    return phone[:3] + "•" * max(len(phone) - 7, 0) + phone[-4:]

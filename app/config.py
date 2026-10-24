import os
from datetime import timedelta

REQUIRED_ENV = ("SECRET_KEY", "ADMIN_PASSWORD", "HASH_SALT")
PROVIDER_ENV = {
    "twilio": ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_NUMBER", "PUBLIC_BASE_URL"),
    "exotel": ("EXOTEL_NUMBER", "EXOTEL_WEBHOOK_SECRET"),
}
MIN_WEBHOOK_SECRET_LENGTH = 24


def database_url():
    """DATABASE_URL, rewritten so SQLAlchemy uses the psycopg 3 driver for Postgres."""
    url = os.environ.get("DATABASE_URL", "sqlite:///app.db")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Config:
    """All configuration comes from the environment. Secrets have no defaults."""

    def __init__(self):
        # Which telephony provider answers calls: "twilio" or "exotel".
        self.VOICE_PROVIDER = os.environ.get("VOICE_PROVIDER", "twilio").strip().lower()

        self.TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self.TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
        self.TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER", "")
        self.PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        self.SECRET_KEY = os.environ.get("SECRET_KEY", "")
        self.ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
        self.HASH_SALT = os.environ.get("HASH_SALT", "")

        # Exotel does not sign webhook requests, so its URLs carry this secret instead.
        self.EXOTEL_NUMBER = os.environ.get("EXOTEL_NUMBER", "")
        self.EXOTEL_WEBHOOK_SECRET = os.environ.get("EXOTEL_WEBHOOK_SECRET", "")

        self.SQLALCHEMY_DATABASE_URI = database_url()
        self.SQLALCHEMY_TRACK_MODIFICATIONS = False
        if not self.SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
            # Serverless instances sit idle for long periods; drop dead connections.
            self.SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}
        # Vercel functions have no persistent disk, so SQLite would silently lose data.
        self.ON_VERCEL = bool(os.environ.get("VERCEL"))

        # Text-to-speech settings (Indian English by default)
        self.TTS_VOICE = os.environ.get("TTS_VOICE", "Polly.Raveena")
        self.TTS_LANGUAGE = os.environ.get("TTS_LANGUAGE", "en-IN")

        # Abuse limits
        self.MAX_CALLS_PER_CALLER_PER_HOUR = int(os.environ.get("MAX_CALLS_PER_CALLER_PER_HOUR", 3))
        self.MAX_CALLS_PER_OWNER_PER_HOUR = int(os.environ.get("MAX_CALLS_PER_OWNER_PER_HOUR", 5))

        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = "Lax"
        self.SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"
        self.PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
        self.TESTING = False

    @property
    def public_number(self):
        """The number printed on stickers and encoded in QR codes."""
        return self.EXOTEL_NUMBER if self.VOICE_PROVIDER == "exotel" else self.TWILIO_NUMBER

    def missing(self):
        if self.VOICE_PROVIDER not in PROVIDER_ENV:
            return [f"VOICE_PROVIDER (must be one of: {', '.join(PROVIDER_ENV)})"]
        required = REQUIRED_ENV + PROVIDER_ENV[self.VOICE_PROVIDER]
        missing = [name for name in required if not getattr(self, name)]
        if self.VOICE_PROVIDER == "exotel" and 0 < len(self.EXOTEL_WEBHOOK_SECRET) < MIN_WEBHOOK_SECRET_LENGTH:
            missing.append(f"EXOTEL_WEBHOOK_SECRET (at least {MIN_WEBHOOK_SECRET_LENGTH} characters)")
        if self.ON_VERCEL and self.SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
            missing.append("DATABASE_URL (Postgres; SQLite does not persist on Vercel)")
        return missing

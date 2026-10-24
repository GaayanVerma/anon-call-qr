import logging

from dotenv import load_dotenv
from flask import Flask, redirect, url_for

from .config import Config
from .extensions import csrf, db
from .security import PhoneRedactionFilter


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    redactor = PhoneRedactionFilter()
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, PhoneRedactionFilter) for f in handler.filters):
            handler.addFilter(redactor)


def create_app(overrides=None):
    load_dotenv()
    config = Config()
    for key, value in (overrides or {}).items():
        setattr(config, key, value)

    missing = config.missing()
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    app = Flask(__name__)
    app.config.from_object(config)
    app.config["PUBLIC_NUMBER"] = config.public_number
    configure_logging()

    db.init_app(app)
    csrf.init_app(app)

    from . import models  # noqa: F401  (register models before create_all)
    from .admin.routes import bp as admin_bp

    # Only the selected provider's webhooks are exposed. They are authenticated by
    # Twilio's signature or Exotel's secret URL instead of CSRF tokens.
    if config.VOICE_PROVIDER == "exotel":
        from .exotel.routes import bp as voice_bp
    else:
        from .voice.routes import bp as voice_bp
    csrf.exempt(voice_bp)
    app.register_blueprint(voice_bp)
    app.register_blueprint(admin_bp)

    @app.route("/")
    def index():
        return redirect(url_for("admin.owners"))

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    with app.app_context():
        db.create_all()

    return app

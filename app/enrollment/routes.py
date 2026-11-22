"""Two-step public enrollment, private activation, and public QR scan.

The QR URL is NOT an activation credential. A signed, HttpOnly Flask session
created during Phase 1 authorizes Phase 2 (same browser). No OTP is used.
"""
import hashlib
import hmac
import io
import re
from datetime import timedelta
from uuid import UUID

from flask import (
    Blueprint, abort, current_app, jsonify, redirect, render_template,
    request, send_file, session, url_for,
)
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Owner, utcnow
from ..services.codes import generate_unique_code
from ..services.qr import qr_image, tel_uri
from .models import EnrollmentUser, Vehicle, VehicleEnrollment
from .validation import REGIONS, SERVICES, ValidationError, parse_form

bp = Blueprint("enrollment", __name__)


@bp.after_request
def privacy_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def enrollment_or_404(enrollment_id):
    try:
        # Accept only canonical UUIDs; eliminates malformed lookups and log noise.
        if str(UUID(enrollment_id)) != enrollment_id:
            abort(404)
    except (ValueError, AttributeError, TypeError):
        abort(404)
    return db.get_or_404(VehicleEnrollment, enrollment_id)


def require_claim(enrollment_id):
    if enrollment_id not in session.get("enrollment_claims", []):
        abort(403, description="Private enrollment session required; use your original browser.")


def current_primary(vehicle_id, excluding=None):
    query = VehicleEnrollment.query.filter_by(
        vehicle_id=vehicle_id, status="ACTIVE", role="PRIMARY"
    )
    if excluding:
        query = query.filter(VehicleEnrollment.id != excluding)
    return query.first()


def public_base_url():
    # Use the explicitly configured canonical URL, not attacker-controlled Host.
    value = current_app.config.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not value:
        if not current_app.config.get("TESTING"):
            raise RuntimeError("Set PUBLIC_BASE_URL to enable QR generation.")
        value = "https://example.test"
    if not value.startswith("https://") and not current_app.config.get("TESTING"):
        raise RuntimeError("PUBLIC_BASE_URL must be HTTPS in production.")
    return value


def qr_url(record):
    return f"{public_base_url()}/v/{record.id}"


def response_data(record, *, private=False):
    value = {
        "enrollment_id": record.id, "status": record.status,
        "review_state": record.review_state, "role": record.role,
        "requested_action": record.requested_action,
        "qr_url": qr_url(record), "created_at": record.created_at.isoformat() + "Z",
        "activated_at": record.activated_at.isoformat() + "Z" if record.activated_at else None,
        "duplicate_plate": current_primary(record.vehicle_id, record.id) is not None,
    }
    if private:
        value.update({
            "full_name": record.user.full_name,
            "phone": record.user.phone_e164,
            "phone_country": "IN" if record.user.phone_e164.startswith("+91") else "US",
            "country": record.vehicle.country, "region": record.vehicle.region,
            "plate": record.vehicle.plate_normalized,
            "vehicle_type": record.vehicle.vehicle_type,
            "make": record.vehicle.make, "model": record.vehicle.model,
            "color": record.vehicle.color,
            "services": record.service_preferences,
            "channel": record.user.communication_channel,
            "consent": record.user.consent_given,
        })
    return value


def client_hash():
    # Privacy-preserving abuse throttle. Never store raw client IPs.
    address = request.remote_addr or "unknown"
    salt = current_app.config["HASH_SALT"].encode("utf-8")
    return hmac.new(salt, address.encode("utf-8"), hashlib.sha256).hexdigest()


@bp.route("/enroll")
def enroll_page():
    return render_template("enrollment/flow.html", enrollment_id=None, regions=REGIONS,
                           service_options=sorted(SERVICES))


@bp.route("/enroll/<enrollment_id>/activate")
def activation_page(enrollment_id):
    enrollment_or_404(enrollment_id)
    if enrollment_id not in session.get("enrollment_claims", []):
        return render_template("enrollment/unclaimed.html"), 403
    return render_template("enrollment/flow.html", enrollment_id=enrollment_id,
                           regions=REGIONS, service_options=sorted(SERVICES))


@bp.route("/api/enrollments", methods=["POST"])
def create_enrollment():
    try:
        data = parse_form(request.get_json(silent=True))
    except ValidationError as exc:
        return jsonify(error=str(exc)), 400

    fingerprint = client_hash()
    recent = utcnow() - timedelta(hours=1)
    if VehicleEnrollment.query.filter_by(creator_ip_hash=fingerprint).filter(
        VehicleEnrollment.created_at >= recent
    ).count() >= 8:
        return jsonify(error="Too many registrations. Please try again later."), 429

    for attempt in range(2):
        try:
            vehicle = Vehicle.query.filter_by(
                country=data["country"], region=data["region"],
                plate_normalized=data["plate_normalized"],
            ).first()
            if vehicle is None:
                vehicle = Vehicle(**{key: data[key] for key in (
                    "country", "region", "plate_normalized", "vehicle_type", "make", "model", "color"
                )})
                db.session.add(vehicle)
                db.session.flush()
            user = EnrollmentUser(
                full_name=data["full_name"], phone_e164=data["phone_e164"],
                communication_channel=data["channel"], consent_given=True,
                consent_at=utcnow(),
            )
            db.session.add(user)
            db.session.flush()
            record = VehicleEnrollment(user_id=user.id, vehicle_id=vehicle.id,
                                       service_preferences=data["services"], creator_ip_hash=fingerprint)
            db.session.add(record)
            db.session.commit()
            break
        except IntegrityError:
            db.session.rollback()
            if attempt == 1:
                return jsonify(error="Could not save; please retry."), 409
    # Do not expose private data in QR. A Flask-signed session is the activation capability.
    claims = session.get("enrollment_claims", [])
    session["enrollment_claims"] = (claims + [record.id])[-12:]
    session.modified = True
    return jsonify(response_data(record, private=True)), 201


@bp.route("/api/enrollments/<enrollment_id>")
def get_enrollment(enrollment_id):
    record = enrollment_or_404(enrollment_id)
    require_claim(enrollment_id)
    return jsonify(response_data(record, private=True))


@bp.route("/api/enrollments/<enrollment_id>/duplicate-request", methods=["POST"])
def duplicate_request(enrollment_id):
    record = enrollment_or_404(enrollment_id)
    require_claim(enrollment_id)
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    if action not in ("SECONDARY", "TRANSFER"):
        return jsonify(error="Choose SECONDARY or TRANSFER."), 400
    if record.status != "INACTIVE" or record.review_state != "NONE":
        return jsonify(error="This request cannot be changed."), 409
    if not current_primary(record.vehicle_id, record.id):
        return jsonify(error="There is no active registration for this plate."), 409
    record.role = "SECONDARY" if action == "SECONDARY" else "PRIMARY"
    record.requested_action = action
    record.review_state = "PENDING_APPROVAL"
    db.session.commit()
    return jsonify(response_data(record, private=True)), 200


@bp.route("/api/enrollments/<enrollment_id>/activate", methods=["POST"])
def activate_enrollment(enrollment_id):
    record = enrollment_or_404(enrollment_id)
    require_claim(enrollment_id)
    if record.status == "ACTIVE":
        return jsonify(response_data(record, private=True)), 200
    if record.review_state in ("PENDING_APPROVAL", "TRANSFERRED", "REJECTED"):
        return jsonify(error="This enrollment needs approval or is no longer eligible."), 409

    try:
        # Lock the shared vehicle row on Postgres to serialize duplicate activation.
        db.session.query(Vehicle).filter_by(id=record.vehicle_id).with_for_update().one()
        prior = current_primary(record.vehicle_id, record.id)
        if prior and record.review_state != "APPROVED":
            db.session.rollback()
            return jsonify(error="This plate has an active primary registration.",
                           code="DUPLICATE", options=["SECONDARY", "TRANSFER"]), 409
        if record.role == "SECONDARY" and record.review_state != "APPROVED":
            db.session.rollback()
            return jsonify(error="Secondary drivers require approval."), 409
        if prior and record.role == "PRIMARY":
            # Transfer only after explicit protected admin approval.
            if record.review_state != "APPROVED" or record.requested_action != "TRANSFER":
                db.session.rollback()
                return jsonify(error="An administrator must approve the transfer."), 409
            prior.status = "INACTIVE"
            prior.review_state = "TRANSFERRED"
            if prior.legacy_owner:
                prior.legacy_owner.active = False
        owner = Owner(
            name=record.user.full_name,
            real_phone=record.user.phone_e164,
            code=generate_unique_code(), active=True,
        )
        db.session.add(owner)
        db.session.flush()
        record.legacy_owner_id = owner.id
        record.status = "ACTIVE"
        record.activated_at = utcnow()
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error="Another enrollment was activated first. Please refresh."), 409
    return jsonify(response_data(record, private=True)), 200


def render_qr(record, download):
    buf = io.BytesIO()
    qr_image(qr_url(record), box_size=10).save(buf, format="PNG")
    buf.seek(0)
    result = send_file(buf, mimetype="image/png", as_attachment=download,
                       download_name=f"vehicle-qr-{record.id[:8]}.png")
    return result


@bp.route("/api/enrollments/<enrollment_id>/preview.png")
def preview_qr(enrollment_id):
    record = enrollment_or_404(enrollment_id)
    require_claim(enrollment_id)
    return render_qr(record, download=False)


@bp.route("/api/enrollments/<enrollment_id>/download.png")
def download_qr(enrollment_id):
    record = enrollment_or_404(enrollment_id)
    require_claim(enrollment_id)
    if record.status != "ACTIVE":
        return jsonify(error="Activate this QR before downloading it."), 409
    return render_qr(record, download=True)


@bp.route("/v/<enrollment_id>")
def scan_qr(enrollment_id):
    record = enrollment_or_404(enrollment_id)
    active = record.status == "ACTIVE" and record.legacy_owner and record.legacy_owner.active
    call_href = None
    if active:
        call_href = tel_uri(current_app.config["PUBLIC_NUMBER"], record.legacy_owner.code)
    return render_template("enrollment/scan.html", active=active, record=record,
                           call_href=call_href), (200 if active else 403)


@bp.route("/admin/enrollment-requests")
def admin_requests():
    if not session.get("admin"):
        return redirect(url_for("admin.login", next=request.path))
    requests = VehicleEnrollment.query.filter_by(review_state="PENDING_APPROVAL").order_by(
        VehicleEnrollment.created_at.asc()).all()
    incumbents = {r.vehicle_id: current_primary(r.vehicle_id) for r in requests}
    return render_template("enrollment/admin_requests.html", requests=requests, incumbents=incumbents)


@bp.route("/admin/enrollment-requests/<enrollment_id>/<action>", methods=["POST"])
def admin_decide(enrollment_id, action):
    if not session.get("admin"):
        abort(403)
    if action not in ("approve", "reject"):
        abort(404)
    record = enrollment_or_404(enrollment_id)
    if record.status != "INACTIVE" or record.review_state != "PENDING_APPROVAL":
        abort(409)
    record.review_state = "APPROVED" if action == "approve" else "REJECTED"
    db.session.commit()
    return redirect(url_for("enrollment.admin_requests"))


@bp.route("/api/enrollments/<enrollment_id>", methods=["PATCH"])
def edit_enrollment(enrollment_id):
    """Phase 2 corrections preserve the QR UUID, and do not edit other drivers' vehicles."""
    record = enrollment_or_404(enrollment_id)
    require_claim(enrollment_id)
    if record.status != "INACTIVE" or record.review_state != "NONE":
        return jsonify(error="This enrollment can no longer be edited."), 409
    try:
        data = parse_form(request.get_json(silent=True))
    except ValidationError as exc:
        return jsonify(error=str(exc)), 400

    try:
        key = {k: data[k] for k in ("country", "region", "plate_normalized")}
        vehicle = Vehicle.query.filter_by(**key).first()
        if vehicle is None:
            vehicle = Vehicle(**{k: data[k] for k in (
                "country", "region", "plate_normalized", "vehicle_type", "make", "model", "color"
            )})
            db.session.add(vehicle)
            db.session.flush()
        elif vehicle.id == record.vehicle_id and len(vehicle.enrollments) == 1:
            # Never update a shared vehicle's canonical attributes.
            for k in ("vehicle_type", "make", "model", "color"):
                setattr(vehicle, k, data[k])
        record.vehicle_id = vehicle.id
        record.user.full_name = data["full_name"]
        record.user.phone_e164 = data["phone_e164"]
        record.user.communication_channel = data["channel"]
        record.user.consent_given = True
        record.user.consent_at = utcnow()
        record.service_preferences = data["services"]
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error="A registration changed while saving. Please retry."), 409
    return jsonify(response_data(record, private=True))

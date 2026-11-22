"""Additive schema for vehicle enrollment; leaves existing Owner/CallLog intact."""
from uuid import uuid4

from sqlalchemy import Index, and_
from ..extensions import db
from ..models import utcnow


class EnrollmentUser(db.Model):
    __tablename__ = "enrollment_users"
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    phone_e164 = db.Column(db.String(20), nullable=False, index=True)
    communication_channel = db.Column(db.String(16), nullable=False)
    consent_given = db.Column(db.Boolean, nullable=False, default=False)
    consent_at = db.Column(db.DateTime)
    consent_version = db.Column(db.String(20), nullable=False, default="2026-11")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    enrollments = db.relationship("VehicleEnrollment", back_populates="user")


class Vehicle(db.Model):
    __tablename__ = "enrollment_vehicles"
    id = db.Column(db.Integer, primary_key=True)
    country = db.Column(db.String(2), nullable=False)
    region = db.Column(db.String(12), nullable=False)
    plate_normalized = db.Column(db.String(12), nullable=False)
    vehicle_type = db.Column(db.String(20), nullable=False)
    make = db.Column(db.String(60), nullable=False)
    model = db.Column(db.String(60), nullable=False)
    color = db.Column(db.String(30), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    __table_args__ = (db.UniqueConstraint("country", "region", "plate_normalized", name="uq_vehicle_plate"),)
    enrollments = db.relationship("VehicleEnrollment", back_populates="vehicle")


class VehicleEnrollment(db.Model):
    __tablename__ = "vehicle_enrollments"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("enrollment_users.id"), nullable=False, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("enrollment_vehicles.id"), nullable=False, index=True)
    legacy_owner_id = db.Column(db.Integer, db.ForeignKey("owners.id"), unique=True, nullable=True)
    status = db.Column(db.String(10), nullable=False, default="INACTIVE", index=True)
    role = db.Column(db.String(12), nullable=False, default="PRIMARY")
    review_state = db.Column(db.String(20), nullable=False, default="NONE")
    requested_action = db.Column(db.String(10), nullable=True)  # SECONDARY or TRANSFER
    service_preferences = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    activated_at = db.Column(db.DateTime)
    creator_ip_hash = db.Column(db.String(64), nullable=False, index=True)
    user = db.relationship("EnrollmentUser", back_populates="enrollments")
    vehicle = db.relationship("Vehicle", back_populates="enrollments")
    legacy_owner = db.relationship("Owner")
    __table_args__ = (
        db.CheckConstraint("status IN ('INACTIVE','ACTIVE')", name="ck_enrollment_status"),
        db.CheckConstraint("role IN ('PRIMARY','SECONDARY')", name="ck_enrollment_role"),
        db.CheckConstraint("requested_action IS NULL OR requested_action IN ('SECONDARY','TRANSFER')", name="ck_enrollment_action"),
        # Enforce at most one ACTIVE primary enrollment per vehicle at the DB layer.
        Index(
            "uq_active_primary_per_vehicle", "vehicle_id", unique=True,
            sqlite_where=and_(status == "ACTIVE", role == "PRIMARY"),
            postgresql_where=and_(status == "ACTIVE", role == "PRIMARY"),
        ),
    )

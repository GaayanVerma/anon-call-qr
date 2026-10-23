import re

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(value):
    return re.sub(r"[\s\-()]", "", value or "")


def validate_e164(form, field):
    field.data = normalize_phone(field.data)
    if not E164.match(field.data):
        raise ValidationError("Use international format, e.g. +9198XXXXXXXX.")


class LoginForm(FlaskForm):
    password = PasswordField("Password", validators=[DataRequired()])


class OwnerForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    real_phone = StringField("Owner's real phone (E.164)", validators=[DataRequired(), validate_e164])
    active = BooleanField("Active", default=True)


class BlockForm(FlaskForm):
    phone = StringField("Caller phone number (hashed, never stored)", validators=[Optional()])
    caller_hash = StringField("…or caller hash", validators=[Optional(), Length(min=64, max=64)])
    note = StringField("Note", validators=[Optional(), Length(max=255)])

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        if not (self.phone.data or self.caller_hash.data):
            self.phone.errors.append("Enter a phone number or a caller hash.")
            return False
        if self.phone.data:
            self.phone.data = normalize_phone(self.phone.data)
            if not E164.match(self.phone.data):
                self.phone.errors.append("Use international format, e.g. +9198XXXXXXXX.")
                return False
        return True


class ActionForm(FlaskForm):
    """Empty form used only for CSRF on POST buttons."""

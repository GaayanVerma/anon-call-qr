"""Input validation shared by all write endpoints; no external dependencies."""
import re

SERVICES = {"Parking Alerts", "Roadside Assistance", "Maintenance Reminders"}
CHANNELS = {"sms", "whatsapp", "call"}
TYPES = {"Sedan", "SUV", "Truck", "EV", "Two-Wheeler"}
COUNTRIES = {"US": "+1", "IN": "+91"}
REGIONS = {
    "US": "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split(),
    "IN": "AN AP AR AS BR CH CG DD DL GA GJ HR HP JK JH KA KL LA LD MP MH MN ML MZ NL OD PB PY RJ SK TN TS TR UP UK WB".split(),
}
COLORS = {"Black", "White", "Gray", "Silver", "Blue", "Red", "Green", "Yellow", "Brown", "Other"}


class ValidationError(ValueError):
    pass


def normalized_plate(value):
    result = re.sub(r"[\s-]+", "", str(value or "")).upper()
    if not re.fullmatch(r"[A-Z0-9]{4,12}", result):
        raise ValidationError("Plate must contain 4–12 letters/numbers (spaces and hyphens are ignored).")
    return result


def clean_text(value, label, max_len=60):
    result = str(value or "").strip()
    if not 1 <= len(result) <= max_len or any(ord(c) < 32 or ord(c) == 127 for c in result):
        raise ValidationError(f"{label} is required (maximum {max_len} characters).")
    return result


def parse_form(payload):
    if not isinstance(payload, dict):
        raise ValidationError("A JSON object is required.")
    country = str(payload.get("country", "")).upper()
    if country not in COUNTRIES:
        raise ValidationError("Select a vehicle registration country.")
    phone_country = str(payload.get("phone_country", "")).upper()
    if phone_country not in COUNTRIES:
        raise ValidationError("Select a phone country code.")
    region = str(payload.get("region", "")).upper()
    if region not in REGIONS[country]:
        raise ValidationError("Select a valid state/region.")
    raw_phone = str(payload.get("phone", "")).strip()
    digits = re.sub(r"[\s().-]", "", raw_phone)
    if not re.fullmatch(r"[0-9]{10}", digits):
        raise ValidationError("Enter exactly 10 digits after the country code.")
    if phone_country == "IN" and digits[0] not in "6789":
        raise ValidationError("Indian mobile numbers must start with 6, 7, 8, or 9.")
    channel = payload.get("channel")
    if channel not in CHANNELS:
        raise ValidationError("Select SMS, WhatsApp, or automated call.")
    vehicle_type = payload.get("vehicle_type")
    if vehicle_type not in TYPES:
        raise ValidationError("Select a vehicle type.")
    color = payload.get("color")
    if color not in COLORS:
        raise ValidationError("Select a vehicle color.")
    services = payload.get("services")
    if not isinstance(services, list) or not services or not set(services) <= SERVICES or len(services) != len(set(services)):
        raise ValidationError("Select one or more valid services.")
    if payload.get("consent") is not True:
        raise ValidationError("You must explicitly consent to service communications.")
    return {
        "full_name": clean_text(payload.get("full_name"), "Full name", 120),
        "phone_e164": COUNTRIES[phone_country] + digits,
        "country": country,
        "phone_country": phone_country,
        "region": region,
        "plate_normalized": normalized_plate(payload.get("plate")),
        "vehicle_type": vehicle_type,
        "make": clean_text(payload.get("make"), "Vehicle make"),
        "model": clean_text(payload.get("model"), "Vehicle model"),
        "color": color,
        "services": services,
        "channel": channel,
    }

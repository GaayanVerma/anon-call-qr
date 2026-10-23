import secrets

from ..models import Owner

MAX_ATTEMPTS = 1000


def is_trivial(code):
    """Reject codes that are easy to guess: repeated digits or straight runs."""
    digits = [int(c) for c in code]
    if len(set(digits)) <= 2:
        return True
    steps = {b - a for a, b in zip(digits, digits[1:])}
    return steps in ({1}, {-1})


def random_code():
    return f"{100000 + secrets.randbelow(900000)}"


def generate_unique_code():
    """Random (non-sequential) 6-digit code not used by any owner."""
    for _ in range(MAX_ATTEMPTS):
        code = random_code()
        if is_trivial(code):
            continue
        if Owner.query.filter_by(code=code).first() is None:
            return code
    raise RuntimeError("Could not generate a unique code")


def find_active_owner(code):
    if not code or len(code) != 6 or not code.isdigit():
        return None
    return Owner.query.filter_by(code=code, active=True).first()

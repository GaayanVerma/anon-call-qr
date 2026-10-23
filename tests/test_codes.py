from app.extensions import db
from app.models import Owner
from app.services import codes
from app.services.codes import find_active_owner, generate_unique_code, is_trivial


def test_generated_codes_are_six_digits_unique_and_not_trivial(app):
    seen = set()
    for i in range(200):
        code = generate_unique_code()
        assert len(code) == 6 and code.isdigit() and code[0] != "0"
        assert not is_trivial(code)
        assert code not in seen
        seen.add(code)
        db.session.add(Owner(name=f"o{i}", real_phone="+919800000000", code=code))
    db.session.commit()


def test_generated_codes_are_not_sequential(app):
    values = [int(generate_unique_code()) for _ in range(20)]
    diffs = {b - a for a, b in zip(values, values[1:])}
    assert len(diffs) > 1


def test_generation_skips_codes_already_taken(app, owner, monkeypatch):
    sequence = iter([owner.code, "111111", "735182"])
    monkeypatch.setattr(codes, "random_code", lambda: next(sequence))
    assert generate_unique_code() == "735182"


def test_trivial_patterns():
    for code in ("111111", "123456", "654321", "121212", "345678"):
        assert is_trivial(code)
    assert not is_trivial("482913")


def test_lookup(app, owner):
    assert find_active_owner(owner.code).id == owner.id
    assert find_active_owner("999999") is None
    assert find_active_owner("48291") is None
    assert find_active_owner("abcdef") is None
    assert find_active_owner("") is None


def test_lookup_ignores_inactive_owner(app, owner):
    owner.active = False
    db.session.commit()
    assert find_active_owner(owner.code) is None

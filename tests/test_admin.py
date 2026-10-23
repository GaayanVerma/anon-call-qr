from app.extensions import db
from app.models import Blocklist, CallLog, Owner
from app.security import caller_hash

from .conftest import CALLER_PHONE, OWNER_PHONE, csrf_token


def test_admin_requires_login(client, owner):
    for path in ("/admin/", "/admin/calls", "/admin/blocklist", f"/admin/owners/{owner.id}/qr.png"):
        resp = client.get(path)
        assert resp.status_code == 302 and "/admin/login" in resp.location


def test_wrong_password(client):
    token = csrf_token(client, "/admin/login")
    resp = client.post("/admin/login", data={"password": "nope", "csrf_token": token})
    assert resp.status_code == 200 and "Wrong password" in resp.get_data(as_text=True)
    assert client.get("/admin/").status_code == 302


def test_login_without_csrf_is_rejected(client):
    resp = client.post("/admin/login", data={"password": "test-admin-password"})
    assert resp.status_code == 400


def test_create_owner(admin):
    token = csrf_token(admin, "/admin/owners/new")
    resp = admin.post(
        "/admin/owners/new",
        data={"name": "Ravi", "real_phone": "+91 98123 45678", "active": "y", "csrf_token": token},
    )
    assert resp.status_code == 302
    owner = Owner.query.one()
    assert owner.real_phone == OWNER_PHONE and len(owner.code) == 6 and owner.active


def test_create_owner_rejects_bad_phone(admin):
    token = csrf_token(admin, "/admin/owners/new")
    resp = admin.post("/admin/owners/new", data={"name": "Ravi", "real_phone": "98123", "csrf_token": token})
    assert resp.status_code == 200 and "international format" in resp.get_data(as_text=True)
    assert Owner.query.count() == 0


def test_owner_list_masks_phone(admin, owner):
    body = admin.get("/admin/").get_data(as_text=True)
    assert owner.code in body and OWNER_PHONE not in body and OWNER_PHONE[-4:] in body


def test_deactivate_requires_csrf(admin, owner):
    admin.post(f"/admin/owners/{owner.id}/toggle")
    assert db.session.get(Owner, owner.id).active
    token = csrf_token(admin, "/admin/")
    admin.post(f"/admin/owners/{owner.id}/toggle", data={"csrf_token": token})
    assert not db.session.get(Owner, owner.id).active


def test_regenerate_code(admin, owner):
    old = owner.code
    token = csrf_token(admin, "/admin/")
    admin.post(f"/admin/owners/{owner.id}/regenerate", data={"csrf_token": token})
    assert db.session.get(Owner, owner.id).code != old


def test_downloads(admin, owner):
    assert admin.get(f"/admin/owners/{owner.id}/qr.png").mimetype == "image/png"
    assert admin.get(f"/admin/owners/{owner.id}/sticker.png").mimetype == "image/png"
    pdf = admin.get(f"/admin/owners/{owner.id}/sticker.pdf")
    assert pdf.mimetype == "application/pdf" and pdf.data.startswith(b"%PDF")
    assert pdf.headers["Cache-Control"] == "no-store"


def test_block_by_phone_stores_only_hash(admin):
    token = csrf_token(admin, "/admin/blocklist")
    admin.post("/admin/blocklist", data={"phone": CALLER_PHONE, "note": "prank", "csrf_token": token})
    entry = Blocklist.query.one()
    assert entry.caller_hash == caller_hash(CALLER_PHONE) and entry.note == "prank"
    assert CALLER_PHONE not in admin.get("/admin/blocklist").get_data(as_text=True)


def test_block_from_call_log_and_unblock(admin, owner):
    call = CallLog(twilio_call_sid="CA1", caller_hash="a" * 64, owner_id=owner.id, status="completed")
    db.session.add(call)
    db.session.commit()
    token = csrf_token(admin, "/admin/calls")
    admin.post(f"/admin/calls/{call.id}/block", data={"csrf_token": token})
    assert db.session.get(Blocklist, "a" * 64) is not None
    admin.post(f"/admin/blocklist/{'a' * 64}/remove", data={"csrf_token": token})
    assert db.session.get(Blocklist, "a" * 64) is None


def test_open_redirect_blocked(client):
    token = csrf_token(client, "/admin/login")
    resp = client.post(
        "/admin/login?next=https://evil.example/",
        data={"password": "test-admin-password", "csrf_token": token},
    )
    assert resp.location.endswith("/admin/")

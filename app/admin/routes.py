import hmac
import io
import logging
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from ..extensions import db
from ..models import Blocklist, CallLog, Owner
from ..security import caller_hash
from ..services import qr as qr_service
from ..services.codes import generate_unique_code
from .forms import ActionForm, BlockForm, LoginForm, OwnerForm

log = logging.getLogger(__name__)

bp = Blueprint("admin", __name__, url_prefix="/admin")


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


@bp.after_request
def no_store(response):
    # Admin pages contain owners' real numbers: keep them out of caches.
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.app_context_processor
def inject_action_form():
    return {"action_form": ActionForm()}


@bp.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        expected = current_app.config["ADMIN_PASSWORD"]
        if expected and hmac.compare_digest(form.password.data.encode(), expected.encode()):
            session.clear()
            session["admin"] = True
            session.permanent = True
            target = request.args.get("next", "")
            if not target.startswith("/admin") or target.startswith("//"):
                target = url_for("admin.owners")
            return redirect(target)
        log.warning("Failed admin login attempt")
        flash("Wrong password.", "error")
    return render_template("admin/login.html", form=form)


@bp.route("/logout", methods=["POST"])
def logout():
    if ActionForm().validate_on_submit():
        session.clear()
    return redirect(url_for("admin.login"))


@bp.route("/")
@login_required
def owners():
    rows = Owner.query.order_by(Owner.created_at.desc()).all()
    return render_template("admin/owners.html", owners=rows, twilio_number=current_app.config["TWILIO_NUMBER"])


@bp.route("/owners/new", methods=["GET", "POST"])
@login_required
def owner_new():
    form = OwnerForm()
    if form.validate_on_submit():
        owner = Owner(
            name=form.name.data.strip(),
            real_phone=form.real_phone.data,
            active=form.active.data,
            code=generate_unique_code(),
        )
        db.session.add(owner)
        db.session.commit()
        log.info("Created owner %s", owner.id)
        flash(f"Owner created with code {owner.code}.", "ok")
        return redirect(url_for("admin.owners"))
    return render_template("admin/owner_form.html", form=form, owner=None)


@bp.route("/owners/<int:owner_id>/edit", methods=["GET", "POST"])
@login_required
def owner_edit(owner_id):
    owner = db.get_or_404(Owner, owner_id)
    form = OwnerForm(obj=owner)
    if form.validate_on_submit():
        owner.name = form.name.data.strip()
        owner.real_phone = form.real_phone.data
        owner.active = form.active.data
        db.session.commit()
        log.info("Updated owner %s", owner.id)
        flash("Owner updated.", "ok")
        return redirect(url_for("admin.owners"))
    return render_template("admin/owner_form.html", form=form, owner=owner)


@bp.route("/owners/<int:owner_id>/toggle", methods=["POST"])
@login_required
def owner_toggle(owner_id):
    owner = db.get_or_404(Owner, owner_id)
    if ActionForm().validate_on_submit():
        owner.active = not owner.active
        db.session.commit()
        flash(f"Owner {'activated' if owner.active else 'deactivated'}.", "ok")
    return redirect(url_for("admin.owners"))


@bp.route("/owners/<int:owner_id>/regenerate", methods=["POST"])
@login_required
def owner_regenerate(owner_id):
    owner = db.get_or_404(Owner, owner_id)
    if ActionForm().validate_on_submit():
        owner.code = generate_unique_code()
        db.session.commit()
        flash(f"New code {owner.code}. Print a new sticker; the old one no longer works.", "ok")
    return redirect(url_for("admin.owners"))


def _image_response(data, mimetype, filename, download):
    return send_file(io.BytesIO(data), mimetype=mimetype, as_attachment=download, download_name=filename)


@bp.route("/owners/<int:owner_id>/qr.png")
@login_required
def owner_qr(owner_id):
    owner = db.get_or_404(Owner, owner_id)
    data = qr_service.qr_png(current_app.config["TWILIO_NUMBER"], owner.code)
    return _image_response(data, "image/png", f"qr-{owner.code}.png", request.args.get("download") == "1")


@bp.route("/owners/<int:owner_id>/sticker.png")
@login_required
def owner_sticker_png(owner_id):
    owner = db.get_or_404(Owner, owner_id)
    data = qr_service.sticker_png(current_app.config["TWILIO_NUMBER"], owner.code)
    return _image_response(data, "image/png", f"sticker-{owner.code}.png", True)


@bp.route("/owners/<int:owner_id>/sticker.pdf")
@login_required
def owner_sticker_pdf(owner_id):
    owner = db.get_or_404(Owner, owner_id)
    data = qr_service.sticker_pdf(current_app.config["TWILIO_NUMBER"], owner.code)
    return _image_response(data, "application/pdf", f"sticker-{owner.code}.pdf", True)


@bp.route("/calls")
@login_required
def calls():
    rows = CallLog.query.order_by(CallLog.created_at.desc()).limit(500).all()
    blocked = {b.caller_hash for b in Blocklist.query.all()}
    return render_template("admin/calls.html", calls=rows, blocked=blocked)


@bp.route("/calls/<int:call_id>/block", methods=["POST"])
@login_required
def block_call(call_id):
    call = db.get_or_404(CallLog, call_id)
    if ActionForm().validate_on_submit() and db.session.get(Blocklist, call.caller_hash) is None:
        db.session.add(Blocklist(caller_hash=call.caller_hash, note=f"From call {call.id}"))
        db.session.commit()
        flash("Caller blocked.", "ok")
    return redirect(url_for("admin.calls"))


@bp.route("/blocklist", methods=["GET", "POST"])
@login_required
def blocklist():
    form = BlockForm()
    if form.validate_on_submit():
        hashed = caller_hash(form.phone.data) if form.phone.data else form.caller_hash.data.lower()
        if db.session.get(Blocklist, hashed) is None:
            db.session.add(Blocklist(caller_hash=hashed, note=(form.note.data or "").strip() or None))
            db.session.commit()
            flash("Caller blocked.", "ok")
        else:
            flash("Caller is already blocked.", "ok")
        return redirect(url_for("admin.blocklist"))
    rows = Blocklist.query.order_by(Blocklist.created_at.desc()).all()
    return render_template("admin/blocklist.html", form=form, rows=rows)


@bp.route("/blocklist/<caller_hash_value>/remove", methods=["POST"])
@login_required
def unblock(caller_hash_value):
    entry = db.session.get(Blocklist, caller_hash_value)
    if entry is None:
        abort(404)
    if ActionForm().validate_on_submit():
        db.session.delete(entry)
        db.session.commit()
        flash("Caller unblocked.", "ok")
    return redirect(url_for("admin.blocklist"))

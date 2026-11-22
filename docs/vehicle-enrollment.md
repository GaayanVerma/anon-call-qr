# QuietCall — Two-phase vehicle enrollment

An **additive feature patch** for [GaayanVerma/anon-call-qr](https://github.com/GaayanVerma/anon-call-qr). It extends the existing Flask + Flask-SQLAlchemy + QR library stack. No React, Node, migrations to legacy tables, or new production dependencies required.

## Install

The archive is a patch, not a copy of the upstream repository. From a machine with Git and Python 3.11+:

```bash
git clone https://github.com/GaayanVerma/anon-call-qr.git
unzip vehicle-enrollment-patch.zip   # expands to vehicle-enrollment-patch/
python vehicle-enrollment-patch/apply_to_repo.py anon-call-qr
cd anon-call-qr
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                 # configure existing secrets / provider
# Set PUBLIC_BASE_URL=http(s)://your-canonical-domain (HTTPS required in prod)
pytest -q tests/test_enrollment.py
flask --app wsgi run --debug
```

Open `http://127.0.0.1:5000/enroll`. For local development, `PUBLIC_BASE_URL` must be an externally reachable URL when testing real scans (for example, a tunnel pointing to Flask). Production must use `https://`.

The installer copies only the new feature files and updates `app/__init__.py` to import new models, register the blueprint, and point `/` to `/enroll`. `/admin` and all existing voice routes remain intact. New tables are created by the repository's existing `db.create_all()` when Flask starts. **Back up your production DB** before deploying, and review the generated schema/index against your DB's migration strategy.

## Main routes

| Method | Path | Who can use it | Purpose |
|---|---|---|---|
| GET | `/enroll` | Public | Phase 1 form |
| POST | `/api/enrollments` | Public + CSRF | Validate + save **INACTIVE** record, return opaque UUID and private summary |
| GET | `/enroll/{uuid}/activate` | Same enrollment browser | Phase 2 review/activation screen |
| GET | `/api/enrollments/{uuid}` | Enrollment session | Read private summary/status |
| PATCH | `/api/enrollments/{uuid}` | Enrollment session + CSRF | Correct details before activation |
| POST | `/api/enrollments/{uuid}/activate` | Enrollment session + CSRF | Atomic INACTIVE → ACTIVE; create legacy Owner |
| POST | `/api/enrollments/{uuid}/duplicate-request` | Enrollment session + CSRF | Request secondary access or transfer |
| GET | `/api/enrollments/{uuid}/preview.png` | Enrollment session | Inactive/active QR preview |
| GET | `/api/enrollments/{uuid}/download.png` | Enrollment session, ACTIVE only | Download printable PNG |
| GET | `/v/{uuid}` | Public | QR scan: block inactive / render anonymous-call entry active |
| GET | `/admin/enrollment-requests` | Existing admin session | Review duplicate requests |
| POST | `/admin/enrollment-requests/{uuid}/approve` | Existing admin session + CSRF | Approve duplicate request |
| POST | `/admin/enrollment-requests/{uuid}/reject` | Existing admin session + CSRF | Reject duplicate request |

### Status graph

```
POST /api/enrollments
     ↓
 INACTIVE ──────────── ordinary primary ────→ activate ─→ ACTIVE
     │                                           ↑
     └─ conflict ─→ SECONDARY / TRANSFER ─→ PENDING_APPROVAL
                                           │
                               admin approve / reject
                                           │
                                       APPROVED
                                           ↓
                                      activate
```

A transfer only deactivates the old primary **inside the final activation transaction**, after admin approval. Secondary drivers receive their own independent QR and private call routing, and do not deactivate the primary.

### Database tables

- `enrollment_users`: name, E.164 phone, channel, consent, consent timestamp/version, created timestamp.
- `enrollment_vehicles`: unique `(country, region, plate_normalized)`, vehicle type, make/model/color, created timestamp.
- `vehicle_enrollments`: UUID, user/vehicle references, services JSON, status, role, review state, timestamps, hashed client-address fingerprint, optional `legacy_owner_id` foreign key to the existing `owners` table.
- `owners`: **existing table**; new record created at activation so Twilio/Exotel voice routes work unchanged.

A unique filtered database index guarantees at most **one ACTIVE PRIMARY** enrollment per vehicle. See `app/enrollment/models.py`.

## Security and product decisions

1. **Public QR never authorizes activation.** An HttpOnly, signed Flask session from Phase 1 contains the activation claim; only the original browser can review private details or activate. The QR itself contains only `PUBLIC_BASE_URL/v/{UUID4}`. If the session is lost, use administrator-assisted recovery or register again. Do not put activation secrets in the public QR or activation query string.
2. **No identity verification = no verified ownership.** This meets the explicitly requested no-OTP/no-document flow, but **does not prevent first-claim plate squatting, false phone registrations, or fraudulent service requests**. Admin approval guards duplicates; it does not verify the first claimant. Production should add an abuse/dispute and ownership recovery policy before scaling.
3. **No unauthenticated transfer.** Transfer and secondary registration require a decision by a logged-in administrator, who should independently check ownership/driver authority before approval.
4. **Privacy.** Public scan pages never render name, direct phone, consent, or plate. Flask request logging should be configured to avoid logging personal data; the existing repository provides phone-redaction logging. Calls use the existing provider's masked caller-id route, not a direct call to the real phone.
5. **CSRF and session protection.** Flask-WTF CSRF remains enforced for all POST/PATCH endpoints. In production set `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE=Lax`, use a strong `SECRET_KEY`, and serve HTTPS. `Cache-Control: no-store` prevents private-page caching.
6. **Validation.** Uppercase 4–12 alphanumeric normalized plates, allowlisted country/region/type/services/channel, independent vehicle-country and phone-country selectors, 10-digit national number, required explicit consent; Jinja autoescaping + DOM `textContent` avoid untrusted HTML injection.
7. **Abuse limit.** The demo limits registrations per `request.remote_addr` hash to eight/hour in the database. This is intentionally simple but **not sufficient as a production rate limiter** behind Vercel/shared proxies: configure trusted edge-origin client-IP handling, a real atomic rate limiter, monitoring, and bot defenses. Never rely on caller-provided `X-Forwarded-For` without a trusted proxy.
8. **Consent compliance.** A checked box alone does **not** establish TCPA, Indian telecom, or WhatsApp policy compliance. Before sending messages, supply clear sender identity, service-specific language, records of consent/opt-outs, required STOP handling, rate limits, time-of-day restrictions, and applicable template/business registrations. No message dispatch is implemented here.
9. **Service integration scope.** The active QR enables the repository's existing anonymous calling. Parking alerts, roadside assistance, maintenance reminders and channel preferences are stored for future provider integrations; messages or service workflows are **not** sent automatically by this patch.
10. **Database migrations.** Current upstream uses `db.create_all()`; this is suitable for creating new tables but cannot evolve existing columns. For ongoing production schema evolution, add Alembic/Flask-Migrate and run reviewed migrations.

## Validation

`python -m compileall app/enrollment` checks Python syntax, and `node --check app/static/enrollment/enroll.js` checks JavaScript syntax. Integration tests are supplied in `tests/test_enrollment.py`; they must be run in the actual cloned repository with its dependencies installed. Scenarios cover inactive scan, activation, privacy isolation, input validation, editing, duplicate driver approval and transfers.

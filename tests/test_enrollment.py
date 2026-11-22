"""Run in the original repository after applying the overlay: pytest -q tests/test_enrollment.py"""
import re
from uuid import UUID

from app.extensions import db
from app.enrollment.models import VehicleEnrollment


def token(client):
    html = client.get('/enroll').get_data(as_text=True)
    found = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    assert found
    return found.group(1)


def sample(**changes):
    value = {
        'full_name': 'Alex Morgan', 'country': 'IN', 'phone_country': 'IN', 'phone': '9876543210',
        'plate': 'dl-01 ab 1234', 'region': 'DL', 'vehicle_type': 'Sedan',
        'make': 'Honda', 'model': 'City', 'color': 'White',
        'services': ['Parking Alerts', 'Roadside Assistance'],
        'channel': 'sms', 'consent': True,
    }
    value.update(changes)
    return value


def post(client, path, data, csrf):
    return client.post(path, json=data, headers={'X-CSRFToken': csrf})


def create(client, csrf, **changes):
    result = post(client, '/api/enrollments', sample(**changes), csrf)
    assert result.status_code == 201, result.get_json()
    return result.get_json()


def test_two_phase_activation_and_private_scan(client):
    csrf = token(client)
    created = create(client, csrf)
    UUID(created['enrollment_id'])
    assert created['plate'] == 'DL01AB1234'
    assert created['status'] == 'INACTIVE'
    assert created['activated_at'] is None
    assert '/v/' in created['qr_url']
    id_ = created['enrollment_id']
    scan = client.get(f'/v/{id_}')
    assert scan.status_code == 403
    assert b'not active yet' in scan.data
    assert b'9876543210' not in scan.data
    assert b'Alex Morgan' not in scan.data
    assert client.get(f'/api/enrollments/{id_}/download.png').status_code == 409
    response = post(client, f'/api/enrollments/{id_}/activate', {}, csrf)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['status'] == 'ACTIVE'
    assert response.get_json()['activated_at'] is not None
    assert client.get(f'/v/{id_}').status_code == 200
    qr = client.get(f'/api/enrollments/{id_}/download.png')
    assert qr.status_code == 200 and qr.data[:8] == b'\x89PNG\r\n\x1a\n'


def test_public_qr_cannot_activate_or_read_private_details(client):
    csrf = token(client)
    created = create(client, csrf)
    id_ = created['enrollment_id']
    outsider = client.application.test_client()
    assert outsider.get(f'/api/enrollments/{id_}').status_code == 403
    assert outsider.get(f'/enroll/{id_}/activate').status_code == 403
    assert outsider.get(f'/api/enrollments/{id_}/preview.png').status_code == 403
    assert outsider.get(f'/v/{id_}').status_code == 403
    # Valid CSRF on outsider session is NOT sufficient for activation.
    outsider_csrf = token(outsider)
    assert post(outsider, f'/api/enrollments/{id_}/activate', {}, outsider_csrf).status_code == 403


def test_invalid_phone_and_consent_are_rejected(client):
    csrf = token(client)
    assert post(client, '/api/enrollments', sample(phone='123abc4567'), csrf).status_code == 400
    assert post(client, '/api/enrollments', sample(phone='987654321'), csrf).status_code == 400
    assert post(client, '/api/enrollments', sample(consent=False), csrf).status_code == 400
    assert post(client, '/api/enrollments', sample(plate='<script>'), csrf).status_code == 400


def test_edit_preserves_uuid_and_changes_plate(client):
    csrf = token(client)
    created = create(client, csrf)
    id_ = created['enrollment_id']
    response = client.patch(f'/api/enrollments/{id_}', json=sample(
        plate='DL-02-CD-5678', phone='9988776655',
    ), headers={'X-CSRFToken': csrf})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['enrollment_id'] == id_
    assert response.get_json()['plate'] == 'DL02CD5678'
    assert response.get_json()['phone'] == '+919988776655'
    assert response.get_json()['status'] == 'INACTIVE'


def test_duplicate_plate_requires_admin_and_does_not_transfer_automatically(client, app):
    csrf = token(client)
    primary = create(client, csrf)
    p1 = primary['enrollment_id']
    assert post(client, f'/api/enrollments/{p1}/activate', {}, csrf).status_code == 200
    competing = create(client, csrf, phone='9998887776')
    p2 = competing['enrollment_id']
    assert competing['duplicate_plate'] is True
    attempt = post(client, f'/api/enrollments/{p2}/activate', {}, csrf)
    assert attempt.status_code == 409
    assert attempt.get_json()['code'] == 'DUPLICATE'
    request_secondary = post(client, f'/api/enrollments/{p2}/duplicate-request', {'action': 'SECONDARY'}, csrf)
    assert request_secondary.status_code == 200
    assert request_secondary.get_json()['review_state'] == 'PENDING_APPROVAL'
    assert request_secondary.get_json()['requested_action'] == 'SECONDARY'
    assert post(client, f'/api/enrollments/{p2}/activate', {}, csrf).status_code == 409
    with client.session_transaction() as sess:
        sess['admin'] = True
    admin_response = client.post(f'/admin/enrollment-requests/{p2}/approve', data={'csrf_token': csrf})
    assert admin_response.status_code == 302
    assert post(client, f'/api/enrollments/{p2}/activate', {}, csrf).status_code == 200
    with app.app_context():
        assert db.session.get(VehicleEnrollment, p1).status == 'ACTIVE'
        assert db.session.get(VehicleEnrollment, p2).role == 'SECONDARY'


def test_approved_transfer_disables_previous_qr(client, app):
    csrf = token(client)
    first = create(client, csrf, plate='MH01AB5555', region='MH')
    second = create(client, csrf, plate='MH01AB5555', region='MH', phone='9998887776')
    assert post(client, f'/api/enrollments/{first["enrollment_id"]}/activate', {}, csrf).status_code == 200
    p2 = second['enrollment_id']
    assert post(client, f'/api/enrollments/{p2}/duplicate-request', {'action': 'TRANSFER'}, csrf).status_code == 200
    with client.session_transaction() as sess:
        sess['admin'] = True
    assert client.post(f'/admin/enrollment-requests/{p2}/approve', data={'csrf_token': csrf}).status_code == 302
    assert post(client, f'/api/enrollments/{p2}/activate', {}, csrf).status_code == 200
    assert client.get(f'/v/{first["enrollment_id"]}').status_code == 403
    assert client.get(f'/v/{p2}').status_code == 200
    with app.app_context():
        assert db.session.get(VehicleEnrollment, first['enrollment_id']).review_state == 'TRANSFERRED'


def test_phone_code_is_independent_from_vehicle_plate_country(client):
    csrf = token(client)
    created = create(client, csrf, phone_country='US', phone='4155550123')
    assert created['country'] == 'IN'
    assert created['phone_country'] == 'US'
    assert created['phone'] == '+14155550123'
    assert created['region'] == 'DL'

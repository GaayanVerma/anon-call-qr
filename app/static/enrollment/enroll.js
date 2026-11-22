/* Lightweight progressive enrollment UI. All server values use textContent, never innerHTML. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const body = document.body;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const regions = JSON.parse($('region-data').textContent);
  let record = null;
  let editing = false;
  let step = 1;
  const makeModels = {
    'Tesla': ['Model 3', 'Model Y', 'Model S', 'Model X', 'Other'],
    'Toyota': ['Camry', 'Corolla', 'RAV4', 'Innova', 'Fortuner', 'Other'],
    'Honda': ['Civic', 'City', 'Accord', 'CR-V', 'Other'],
    'Hyundai': ['i20', 'Creta', 'Venue', 'Tucson', 'Ioniq 5', 'Other'],
    'Maruti Suzuki': ['Swift', 'Baleno', 'Brezza', 'Dzire', 'Other'],
    'Tata': ['Nexon', 'Punch', 'Harrier', 'Safari', 'Tiago', 'Other'],
    'Mahindra': ['Scorpio', 'XUV700', 'Thar', 'Other'],
    'Kia': ['Seltos', 'Sonet', 'EV6', 'Other'],
    'Ford': ['F-150', 'Mustang', 'Explorer', 'Other'],
    'BMW': ['3 Series', '5 Series', 'X3', 'X5', 'Other'],
    'Mercedes-Benz': ['C-Class', 'E-Class', 'GLC', 'Other'],
    'Other': ['Other'],
  };

  function setAlert(message) {
    const el = $('alert');
    el.textContent = message || '';
    el.hidden = !message;
    if (message) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  function setProgress(number) {
    step = number;
    [1, 2, 3].forEach(i => {
      $('step-' + i).classList.toggle('on', i === number);
      $('step-' + i).classList.toggle('done', i < number);
    });
  }
  function configureRegions() {
    const country = $('country').value;
    const saved = $('region').value;
    $('region').replaceChildren();
    for (const value of regions[country]) {
      const o = document.createElement('option');
      o.value = value;
      o.textContent = value;
      $('region').append(o);
    }
    $('region').value = regions[country].includes(saved) ? saved : (country === 'IN' ? 'DL' : 'CA');
    $('plate').placeholder = country === 'IN' ? 'DL01AB1234' : 'ABC1234';
  }
  function configureModels() {
    const saved = $('model').value;
    $('model').replaceChildren();
    const starter = new Option('Select model', '');
    $('model').add(starter);
    for (const model of makeModels[$('make').value] || ['Other']) $('model').add(new Option(model, model));
    if ([...$('model').options].some(o => o.value === saved)) $('model').value = saved;
  }
  $('country').addEventListener('change', configureRegions);
  $('phone_country').addEventListener('change', () => {
    $('phone').placeholder = $('phone_country').value === 'IN' ? '9876543210' : '4155550123';
  });
  $('make').addEventListener('change', configureModels);
  $('plate').addEventListener('input', e => {
    e.target.value = e.target.value.replace(/[\s-]+/g, '').toUpperCase();
  });
  $('phone').addEventListener('input', e => {
    e.target.value = e.target.value.replace(/\D/g, '').slice(0, 10);
  });
  configureRegions(); configureModels();
  $('phone').placeholder = '9876543210';

  async function api(url, method = 'GET', payload = undefined) {
    const options = { method, credentials: 'same-origin', headers: {} };
    if (method !== 'GET') options.headers['X-CSRFToken'] = csrf;
    if (payload !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(payload);
    }
    const response = await fetch(url, options);
    const value = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(value.error || `Request failed (${response.status})`);
      error.code = value.code;
      throw error;
    }
    return value;
  }
  function payloadFromForm() {
    if (!$('vehicle-form').reportValidity()) throw new Error('Please complete all required fields.');
    const phone = $('phone').value.trim();
    if (!/^\d{10}$/.test(phone)) throw new Error('Phone number must be exactly 10 digits.');
    const services = [...document.querySelectorAll('input[name="services"]:checked')].map(e => e.value);
    if (!services.length) throw new Error('Select at least one service.');
    return {
      full_name: $('full_name').value, country: $('country').value,
      phone_country: $('phone_country').value, phone, plate: $('plate').value, region: $('region').value,
      vehicle_type: $('vehicle_type').value, make: $('make').value,
      model: $('model').value, color: $('color').value, services,
      channel: document.querySelector('input[name="channel"]:checked').value,
      consent: $('consent').checked,
    };
  }
  $('vehicle-form').addEventListener('submit', async e => {
    e.preventDefault(); setAlert('');
    try {
      const payload = payloadFromForm();
      $('submit').disabled = true;
      $('submit').textContent = 'Creating your QR…';
      record = editing
        ? await api(`/api/enrollments/${record.enrollment_id}`, 'PATCH', payload)
        : await api('/api/enrollments', 'POST', payload);
      editing = false;
      history.replaceState({}, '', '/enroll/' + record.enrollment_id + '/activate');
      $('form-screen').hidden = true;
      $('result-screen').hidden = false;
      showPreview();
    } catch (err) { setAlert(err.message); }
    finally { $('submit').disabled = false; $('submit').textContent = 'Generate my QR code →'; }
  });

  function setText(id, value) { $(id).textContent = value || '—'; }
  function hydrateRecord() {
    const id = record.enrollment_id;
    $('qr-image').src = `/api/enrollments/${id}/preview.png`;
    $('qr-url').href = record.qr_url;
    $('qr-url').textContent = 'View scan destination ↗';
    setText('review-name', record.full_name);
    setText('review-phone', record.phone);
    setText('review-plate', `${record.country} · ${record.region} · ${record.plate}`);
    setText('review-vehicle', [record.color, record.make, record.model].join(' '));
    setText('review-services', record.services.join(', '));
    setText('review-channel', record.channel === 'call' ? 'Automated call' : record.channel.toUpperCase());
    $('status-badge').classList.toggle('active', record.status === 'ACTIVE');
    $('status-badge').classList.toggle('inactive', record.status !== 'ACTIVE');
    $('status-badge').textContent = record.status === 'ACTIVE' ? '● ACTIVE' : '● INACTIVE — Activation required';
    $('duplicate-card').hidden = true;
    $('pending-card').hidden = true;
    $('review-button').hidden = true;
    $('activate-button').hidden = true;
    $('download-button').hidden = true;
    $('print-button').hidden = true;
    $('edit-button').hidden = true;
    if (record.status === 'ACTIVE') {
      setProgress(3);
      setText('result-eyebrow', 'ALL SET');
      setText('result-title', 'Your vehicle QR is now active.');
      setText('result-copy', 'Download it and place it somewhere visible on your vehicle. Your personal number stays private.');
      $('review-card').hidden = false;
      $('download-button').href = `/api/enrollments/${id}/download.png`;
      $('download-button').hidden = false;
      $('print-button').hidden = false;
      return;
    }
    if (record.review_state === 'PENDING_APPROVAL') {
      setProgress(2);
      setText('result-eyebrow', 'AWAITING REVIEW');
      setText('result-title', 'Your request was submitted.');
      setText('result-copy', 'The QR stays inactive while an administrator reviews the duplicate-plate request.');
      $('review-card').hidden = false;
      $('pending-card').hidden = false;
    } else if (record.review_state === 'REJECTED' || record.review_state === 'TRANSFERRED') {
      setProgress(2);
      setText('result-eyebrow', 'ACTION NOT AVAILABLE');
      setText('result-title', 'This registration cannot be activated.');
      setText('result-copy', 'Contact an administrator or create a new registration.');
      $('review-card').hidden = false;
    } else if (record.duplicate_plate && record.review_state !== 'APPROVED') {
      setProgress(2);
      setText('result-eyebrow', 'DUPLICATE PLATE');
      setText('result-title', 'We found an existing registration.');
      setText('result-copy', 'Your QR is saved but inactive. Choose how you want to be associated with this vehicle.');
      $('review-card').hidden = false;
      $('duplicate-card').hidden = false;
      $('edit-button').hidden = false;
    } else if (step >= 2 || record.review_state === 'APPROVED') {
      showReview();
    } else {
      setProgress(1);
      setText('result-eyebrow', 'PHASE 1 COMPLETE');
      setText('result-title', 'Your QR is ready to activate.');
      setText('result-copy', 'Your QR exists but will not connect to any service until you confirm your details.');
      $('review-card').hidden = true;
      $('review-button').hidden = false;
    }
  }
  function showPreview() { hydrateRecord(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
  function showReview() {
    setProgress(2);
    setText('result-eyebrow', 'ONE LAST STEP');
    setText('result-title', 'Everything look right?');
    setText('result-copy', 'Please double-check the phone and license plate before going live.');
    $('review-card').hidden = false;
    $('review-button').hidden = true;
    $('activate-button').hidden = false;
    $('edit-button').hidden = record.review_state !== 'NONE';
  }
  $('review-button').addEventListener('click', showReview);
  $('edit-button').addEventListener('click', () => {
    if (!record || record.status !== 'INACTIVE' || record.review_state !== 'NONE') return;
    editing = true;
    $('full_name').value = record.full_name;
    $('country').value = record.country;
    $('phone_country').value = record.phone_country;
    configureRegions();
    $('region').value = record.region;
    $('phone').placeholder = record.phone_country === 'IN' ? '9876543210' : '4155550123';
    $('phone').value = record.phone.replace(/^\+91|^\+1/, '');
    $('plate').value = record.plate;
    $('vehicle_type').value = record.vehicle_type;
    $('make').value = record.make;
    configureModels();
    $('model').value = record.model;
    $('color').value = record.color;
    for (const input of document.querySelectorAll('input[name="services"]')) input.checked = record.services.includes(input.value);
    document.querySelector(`input[name="channel"][value="${record.channel}"]`).checked = true;
    $('consent').checked = false; // Fresh explicit consent for changed contact details.
    $('result-screen').hidden = true;
    $('form-screen').hidden = false;
    $('submit').textContent = 'Save changes & return to QR →';
    setProgress(1);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  $('activate-button').addEventListener('click', async () => {
    setAlert('');
    $('activate-button').disabled = true;
    try {
      record = await api(`/api/enrollments/${record.enrollment_id}/activate`, 'POST', {});
      hydrateRecord();
    } catch (err) {
      if (err.code === 'DUPLICATE') {
        record.duplicate_plate = true;
        record.review_state = 'NONE';
        hydrateRecord();
      }
      setAlert(err.message);
    } finally { $('activate-button').disabled = false; }
  });
  for (const [elementId, action] of [['secondary-button', 'SECONDARY'], ['transfer-button', 'TRANSFER']]) {
    $(elementId).addEventListener('click', async () => {
      setAlert('');
      try {
        record = await api(`/api/enrollments/${record.enrollment_id}/duplicate-request`, 'POST', { action });
        hydrateRecord();
      } catch (err) { setAlert(err.message); }
    });
  }
  $('print-button').addEventListener('click', () => window.print());

  const existing = body.dataset.enrollmentId;
  if (existing) {
    $('form-screen').hidden = true;
    api(`/api/enrollments/${existing}`)
      .then(value => { record = value; $('result-screen').hidden = false; setProgress(2); hydrateRecord(); })
      .catch(err => setAlert(err.message));
  }
})();

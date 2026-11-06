'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { harness, Element } = require('./web_harness');

/** Return an eligible mock artifact; it cannot identify or invoke a real payload. */
function candidate() {
  return {
    board: 'codm-cc2652p',
    installed_version: '0.12',
    latest_version: '0.13',
    candidate_id: 'test-artifact',
    preflight_supported: true,
    update_available: true,
    install_supported: true,
    blocked_reason: null,
    in_progress: false,
    stage: null,
    progress_percent: null,
    request_id: null,
    status: null,
    error: null,
    recovery: null,
    rollback_supported: true,
  };
}

/**
 * Return a non-executing mock preflight response bound to the test candidate.
 * @param {Object} changes - Scenario-specific report overrides.
 * @returns {Object} Schema-one advisory report.
 */
function preflight(changes = {}) {
  return {
    schema: 1,
    operation: 'update',
    candidate_id: 'test-artifact',
    board: 'codm-cc2652p',
    installed_version: '0.12',
    target_version: '0.13',
    compatible: true,
    executable: true,
    identity_source: 'live_session',
    checks: [
      'artifact',
      'expected_board',
      'helper',
      'live_identity',
      'board',
      'protocol',
      'baudrate',
      'version',
    ].map((id) => ({ id, status: 'pass', code: 'compatible' })),
    blocked_reason: null,
    requires_manual_action: false,
    rollback_supported: true,
    ...changes,
  };
}

/**
 * Serve real preflight GETs while forwarding install/outcome requests to each scenario.
 * @param {Object} ui - Isolated browser harness.
 * @param {Function} responder - Handler for subsequent non-preflight HTTP requests.
 */
function withPreflight(
  ui,
  responder = () => {
    throw new Error('Unexpected request');
  },
) {
  ui.network((path, options) =>
    path.startsWith('/api/firmware/preflight') ? preflight() : responder(path, options),
  );
}

/** Old daemons and boards without an install path remain viewable without an enabled action. */
test('missing and unsupported firmware support fails closed', () => {
  const ui = harness();
  ui.snapshot(null);
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-review').disabled, true);
  assert.match(ui.element('#firmware-reason').textContent, /does not provide/);
  ui.snapshot({
    ...candidate(),
    board: 'pca10059',
    install_supported: false,
    blocked_reason: 'Nordic installation is not supported',
    recovery: 'Use the documented DFU procedure.',
  });
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-review').disabled, true);
  assert.match(ui.element('#firmware-recovery').textContent, /DFU/);
});

/** Replacing an artifact during review must require a fresh explicit review. */
test('candidate changes invalidate confirmation', async () => {
  const ui = harness();
  ui.snapshot(candidate());
  await ui.run('reconcileFirmwareRequest()');
  withPreflight(ui);
  await ui.element('#firmware-review').events.click();
  assert.equal(ui.element('#firmware-install').disabled, false);
  ui.snapshot({ ...candidate(), candidate_id: 'replacement' });
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-install').disabled, true);
  await ui.element('#firmware-form').events.submit({ preventDefault() {} });
  assert.equal(ui.calls.filter((c) => c.options.method === 'POST').length, 0);
  assert.match(ui.element('#firmware-review-error').textContent, /changed/);
});

/** A failed POST response resolves the same identity by GET and survives a page reload. */
test('lost submission is never replayed and resolves after reload', async () => {
  const storage = new Map();
  const ui = harness(storage);
  ui.snapshot(candidate());
  await ui.run('reconcileFirmwareRequest()');
  let requestId;
  withPreflight(ui, (path, options) => {
    if (options.method === 'POST') {
      const payload = JSON.parse(options.body);
      requestId = payload.request_id;
      assert.deepEqual(Object.keys(payload).sort(), ['candidate_id', 'request_id']);
      throw new TypeError('Lost reply');
    }
    assert.equal(path, '/api/commands/' + requestId);
    return { request_id: requestId, status: 'pending' };
  });
  await ui.element('#firmware-review').events.click();
  await ui.element('#firmware-form').events.submit({ preventDefault() {} });
  assert.equal(ui.calls.filter((c) => c.options.method === 'POST').length, 1);
  assert.equal(storage.size, 1);
  assert.equal(ui.element('#firmware-review').disabled, true);
  const reloaded = harness(storage);
  reloaded.snapshot(candidate());
  reloaded.network((path) => {
    assert.equal(path, '/api/commands/' + requestId);
    return { request_id: requestId, status: 'confirmed' };
  });
  await reloaded.run('reconcileFirmwareRequest();');
  reloaded.run('renderFirmware()');
  assert.equal(reloaded.calls.length, 1);
  assert.equal(reloaded.calls[0].options.method, undefined);
  assert.equal(storage.size, 0);
  assert.equal(reloaded.element('#firmware-status').textContent, 'Verified');
});

/** Only daemon measurements can turn an indeterminate update into a percentage. */
test('maintenance blocks controls and shows genuine progress only', () => {
  const ui = harness();
  const mqttSave = new Element();
  const renameSave = new Element();
  ui.element('#mqtt-form').controls.push(mqttSave);
  ui.element('#rename-form').controls.push(renameSave);
  ui.snapshot({
    ...candidate(),
    in_progress: true,
    status: 'pending',
    request_id: 'job1',
    stage: 'transferring',
  });
  ui.run('snapshot.mqtt = { enabled: true, editable: true }; render()');
  assert.equal(ui.element('#add-device').disabled, true);
  assert.equal(mqttSave.disabled, true);
  assert.equal(renameSave.disabled, true);
  assert.equal(ui.element('#maintenance-banner').hidden, false);
  assert.equal(ui.element('#firmware-progress').value, null);
  assert.equal(ui.element('#firmware-review').disabled, true);
  ui.snapshot({ ...candidate(), in_progress: true, status: 'pending', progress_percent: 37 });
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-progress').value, 37);
});

/** Release text is escaped and executable or credential-bearing links never become clickable. */
test('release metadata cannot inject markup or executable links', () => {
  const ui = harness();
  ui.snapshot({
    ...candidate(),
    board: '<img onerror=alert(1)>',
    release_url: 'javascript:alert(1)',
    release_notes: '<b>notes</b>',
  });
  ui.run('renderFirmware()');
  assert.match(ui.element('#firmware-details').innerHTML, /&lt;img/);
  assert.equal(ui.element('#firmware-release-link').hidden, true);
  assert.equal(ui.element('#firmware-notes-text').textContent, '<b>notes</b>');
  ui.snapshot({ ...candidate(), release_url: 'https://user:secret@example.org' });
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-release-link').hidden, true);
});

/** Durable uncertainty is presented as recovery, never as success or an automatic retry. */
test('unknown result preserves recovery messaging', async () => {
  const ui = harness();
  ui.snapshot({ ...candidate(), status: 'unknown', recovery: 'Reconnect in bootloader mode.' });
  await ui.run('reconcileFirmwareRequest()');
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-status').textContent, 'Needs recovery');
  assert.equal(ui.element('#firmware-review').disabled, true);
  assert.equal(ui.calls.length, 0);
});

/** Recovery keeps normal mutations blocked even after a firmware worker stops. */
test('recovery locks device changes without claiming installation is still running', async () => {
  const ui = harness();
  ui.snapshot({
    ...candidate(),
    in_progress: false,
    status: 'unknown',
    recovery: 'recovery_required',
  });
  ui.run('render()');
  assert.equal(ui.element('#add-device').disabled, true);
  assert.equal(ui.element('#firmware-status').textContent, 'Needs recovery');
  assert.equal(ui.element('#firmware-progress').hidden, true);
  assert.match(ui.element('#maintenance-message').textContent, /recovery/);
  await assert.rejects(ui.run("command('rename', {device:'test', name:'Test'})"), /paused/);
  assert.equal(ui.calls.length, 0);
});

/** A durable unknown result cannot be mistaken for permission to resubmit. */
test('unknown POST result blocks another install even before snapshot catches up', async () => {
  const ui = harness();
  ui.snapshot(candidate());
  await ui.run('reconcileFirmwareRequest()');
  withPreflight(ui, (path, options) => ({
    request_id: JSON.parse(options.body).request_id,
    status: 'unknown',
  }));
  await ui.element('#firmware-review').events.click();
  await ui.element('#firmware-form').events.submit({ preventDefault() {} });
  assert.equal(ui.element('#firmware-status').textContent, 'Needs recovery');
  assert.equal(ui.element('#firmware-review').disabled, true);
  assert.equal(ui.calls.filter((c) => c.options.method === 'POST').length, 1);
});

/** Preflight is a single safe GET that neither starts a job nor blocks device controls. */
test('standalone preflight uses GET and remains available for blocked candidates', async () => {
  const ui = harness();
  ui.snapshot({
    ...candidate(),
    install_supported: false,
    update_available: false,
    blocked_reason: 'bootloader_install_required',
  });
  ui.network(() =>
    preflight({
      executable: false,
      compatible: false,
      requires_manual_action: true,
      blocked_reason: 'bootloader_install_required',
      checks: [{ id: 'bootloader', status: 'fail', code: 'bootloader_install_required' }],
    }),
  );
  await ui.element('#firmware-preflight').events.click();
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0].options.method, undefined);
  assert.equal(ui.calls[0].path, '/api/firmware/preflight?candidate_id=test-artifact');
  assert.equal(ui.run('firmwareBusy()'), false);
  assert.match(ui.element('#firmware-preflight-report').innerHTML, /Manual preparation/);
  assert.match(ui.element('#firmware-preflight-report').innerHTML, /Failed/);
  assert.equal(ui.element('#firmware-review').disabled, true);
});

/** Failed or unknown checks and incomplete schemas cannot become installation approval. */
test('failed unknown and malformed reports keep installation disabled', async () => {
  for (const report of [
    preflight({ executable: false }),
    preflight({ compatible: null }),
    preflight({ schema: 2 }),
    preflight({ operation: 'recovery' }),
    preflight({ checks: [] }),
    preflight({ checks: [{ id: 'board', status: 'unknown', code: 'board_identity_unknown' }] }),
    preflight({ checks: [{ id: 'artifact', status: 'fail', code: 'artifact_invalid' }] }),
  ]) {
    const ui = harness();
    ui.snapshot(candidate());
    ui.network(() => report);
    await ui.element('#firmware-review').events.click();
    assert.equal(ui.element('#firmware-install').disabled, true);
    if (ui.run('firmwareUi.preflight.report'))
      assert.equal(ui.element('#firmware-status').textContent, 'Preflight blocked');
    await ui.element('#firmware-form').events.submit({ preventDefault() {} });
    assert.equal(ui.calls.length, 1);
  }
});

/** An advisory report must describe the same live board, artifact and versions as the review. */
test('mismatched preflight cannot authorize a reviewed update', async () => {
  for (const changes of [
    { candidate_id: 'other' },
    { board: 'other-board' },
    { target_version: '0.14' },
    { installed_version: '0.11' },
    { identity_source: 'unknown' },
    { rollback_supported: false },
  ]) {
    const ui = harness();
    ui.snapshot(candidate());
    ui.network(() => preflight(changes));
    await ui.element('#firmware-review').events.click();
    assert.equal(ui.element('#firmware-install').disabled, true);
    assert.match(ui.element('#firmware-review-error').textContent, /does not match/);
  }
});

/** Lost connectivity invalidates a prior successful report even if identical state returns. */
test('disconnect and candidate changes require another preflight', async () => {
  const ui = harness();
  ui.snapshot(candidate());
  withPreflight(ui);
  await ui.element('#firmware-review').events.click();
  assert.equal(ui.element('#firmware-install').disabled, false);
  ui.run('connected = false; renderFirmware(); connected = true; renderFirmware();');
  assert.equal(ui.element('#firmware-install').disabled, true);
  await ui.element('#firmware-recheck').events.click();
  assert.equal(ui.element('#firmware-install').disabled, false);
  ui.snapshot({ ...candidate(), candidate_id: 'replacement' });
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-install').disabled, true);
  assert.equal(ui.element('#firmware-recheck').disabled, true);
});

/** A delayed response cannot reopen installation after the underlying radio state changes. */
test('late preflight response is discarded after invalidation', async () => {
  const ui = harness();
  ui.snapshot(candidate());
  let finish;
  ui.network(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const pending = ui.element('#firmware-review').events.click();
  assert.equal(ui.element('#firmware-install').disabled, true);
  assert.equal(ui.run('firmwareBusy()'), false);
  ui.run('snapshot.ready = false; renderFirmware();');
  finish(preflight());
  await pending;
  ui.run('snapshot.ready = true; renderFirmware();');
  assert.equal(ui.run('firmwareUi.preflight.report'), null);
  assert.equal(ui.element('#firmware-install').disabled, true);
});

/** A missing endpoint or failed read never falls through to installation or auto-retries. */
test('unavailable preflight supports an explicit read retry only', async () => {
  const ui = harness();
  ui.snapshot(candidate());
  ui.network(() => {
    throw new Error('404');
  });
  await ui.element('#firmware-review').events.click();
  assert.match(ui.element('#firmware-review-error').textContent, /older daemons/);
  assert.equal(ui.element('#firmware-install').disabled, true);
  assert.equal(ui.calls.length, 1);
  withPreflight(ui);
  await ui.element('#firmware-recheck').events.click();
  assert.equal(ui.element('#firmware-install').disabled, false);
  assert.equal(ui.calls.length, 2);
});

/** Diagnostic code and check identifiers cannot introduce markup into the review. */
test('preflight renders untrusted report labels as escaped text', async () => {
  const ui = harness();
  ui.snapshot(candidate());
  ui.network(() =>
    preflight({
      executable: false,
      blocked_reason: '<img src=x>',
      checks: [{ id: '<script>id</script>', status: 'fail', code: '<b>reason</b>' }],
    }),
  );
  await ui.element('#firmware-review').events.click();
  const markup = ui.element('#firmware-review-preflight').innerHTML;
  assert.doesNotMatch(markup, /<script|<img|<b>/);
  assert.match(markup, /&lt;script/);
});

/** Older daemons must not expose an install path without the advisory capability. */
test('missing preflight capability disables checks and installation without HTTP', async () => {
  const ui = harness();
  ui.snapshot({ ...candidate(), preflight_supported: undefined });
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-review').disabled, true);
  assert.equal(ui.element('#firmware-preflight').disabled, true);
  await ui.element('#firmware-review').events.click();
  await ui.element('#firmware-preflight').events.click();
  assert.equal(ui.calls.length, 0);
  assert.match(ui.element('#firmware-reason').textContent, /Update the daemon/);
});

/** Nordic eligibility follows backend capability and a matching report, never the board name alone. */
test('Nordic review follows backend eligibility and encodes the selected artifact', async () => {
  const ui = harness();
  ui.snapshot({
    ...candidate(),
    board: 'nordic-pca10059',
    candidate_id: 'nordic:abc123',
    rollback_supported: false,
  });
  ui.network(() =>
    preflight({
      board: 'nordic-pca10059',
      candidate_id: 'nordic:abc123',
      rollback_supported: false,
    }),
  );
  await ui.element('#firmware-review').events.click();
  assert.equal(ui.element('#firmware-install').disabled, false);
  assert.equal(ui.calls[0].path, '/api/firmware/preflight?candidate_id=nordic%3Aabc123');
  assert.match(ui.element('#firmware-rollback').textContent, /no automatic rollback/);
  ui.snapshot({
    ...candidate(),
    board: 'nordic-pca10059',
    install_supported: false,
    blocked_reason: 'artifact_unqualified',
  });
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-review').disabled, true);
  assert.equal(ui.element('#firmware-install').disabled, true);
  assert.equal(ui.calls.length, 1);
});

/** Each required live compatibility check must be present before the UI enables installation. */
test('incomplete or contradictory all-pass reports never enable install', async () => {
  const complete = preflight();
  const incomplete = complete.checks.map((missing) =>
    preflight({
      checks: complete.checks.filter((check) => check.id !== missing.id),
    }),
  );
  for (const report of [
    ...incomplete,
    preflight({ checks: [{ id: 'artifact', status: 'pass', code: 'compatible' }] }),
    preflight({ checks: [...complete.checks, complete.checks[0]] }),
    preflight({ blocked_reason: 'radio_busy' }),
    preflight({ requires_manual_action: true }),
  ]) {
    const ui = harness();
    ui.snapshot(candidate());
    ui.network(() => report);
    await ui.element('#firmware-review').events.click();
    assert.equal(ui.element('#firmware-install').disabled, true);
    assert.equal(ui.element('#firmware-status').textContent, 'Preflight blocked');
    await ui.element('#firmware-form').events.submit({ preventDefault() {} });
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.calls[0].options.method, undefined);
  }
});

/** A verified installed candidate is up to date, not missing, and cannot trigger another install. */
test('installed candidate is distinguished from missing or non-newer firmware', async () => {
  const ui = harness();
  ui.snapshot({
    ...candidate(),
    installed_version: '0.13',
    update_available: false,
    status: 'confirmed',
  });
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-status').textContent, 'Verified');
  assert.equal(ui.element('#firmware-reason').textContent, 'Your gateway firmware is up to date.');
  assert.equal(ui.element('#firmware-review').disabled, true);
  await ui.element('#firmware-review').events.click();
  assert.equal(ui.calls.length, 0);
  ui.snapshot({ ...candidate(), installed_version: '0.14', update_available: false });
  ui.run('renderFirmware()');
  assert.match(ui.element('#firmware-reason').textContent, /No newer verified firmware update/);
  ui.snapshot({
    ...candidate(),
    candidate_id: null,
    latest_version: null,
    update_available: false,
  });
  ui.run('renderFirmware()');
  assert.match(ui.element('#firmware-reason').textContent, /No verified firmware candidate/);
});

/** Jobs started through HA expose transfer progress without claiming overall completion. */
test('external transfer completion remains updating until daemon confirmation', () => {
  const ui = harness();
  const job = {
    ...candidate(),
    request_id: 'ha-firmware-job',
    status: 'pending',
    in_progress: true,
    progress_percent: null,
  };
  for (const percent of [0, 42, 100]) {
    ui.snapshot({ ...job, stage: 'transfer', transfer_percent: percent });
    ui.run('renderFirmware()');
    assert.match(ui.element('#firmware-stage').textContent, new RegExp(`Transfer: ${percent}%`));
    assert.equal(ui.element('#firmware-progress').value, null);
    assert.equal(ui.element('#firmware-status').textContent, 'Updating');
    assert.equal(ui.element('#firmware-review').disabled, true);
  }
  for (const percent of [undefined, null, '100', -1, 101]) {
    ui.snapshot({ ...job, stage: 'transferring', transfer_percent: percent });
    ui.run('renderFirmware()');
    assert.doesNotMatch(ui.element('#firmware-stage').textContent, /%/);
    assert.equal(ui.element('#firmware-progress').value, null);
  }
  ui.snapshot({ ...job, stage: 'restoring', transfer_percent: 100 });
  ui.run('renderFirmware()');
  assert.match(ui.element('#firmware-stage').textContent, /verifying/);
  assert.doesNotMatch(ui.element('#firmware-stage').textContent, /100%/);
  assert.equal(ui.element('#firmware-status').textContent, 'Updating');
  ui.snapshot({
    ...job,
    stage: 'confirmed',
    status: 'confirmed',
    in_progress: false,
    installed_version: '0.13',
    update_available: false,
  });
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-status').textContent, 'Verified');
  assert.equal(ui.element('#firmware-progress').hidden, true);
  assert.equal(ui.calls.length, 0);
});

/** Shared catalog removal or changed eligibility revokes a previously reviewed installation. */
test('catalog refresh invalidates review even without a radio reconnect', async () => {
  for (const changes of [
    { candidate_id: null, latest_version: null, update_available: false },
    { install_supported: false, blocked_reason: 'artifact_unqualified' },
  ]) {
    const ui = harness();
    ui.snapshot(candidate());
    withPreflight(ui);
    await ui.element('#firmware-review').events.click();
    assert.equal(ui.element('#firmware-install').disabled, false);
    ui.snapshot({ ...candidate(), ...changes });
    ui.run('renderFirmware()');
    assert.equal(ui.element('#firmware-install').disabled, true);
    assert.equal(ui.run('firmwareUi.preflight.report'), null);
    await ui.element('#firmware-form').events.submit({ preventDefault() {} });
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.calls[0].options.method, undefined);
  }
});

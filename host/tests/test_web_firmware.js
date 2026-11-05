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
  ui.element('#firmware-review').events.click();
  assert.equal(ui.element('#firmware-install').disabled, false);
  ui.snapshot({ ...candidate(), candidate_id: 'replacement' });
  ui.run('renderFirmware()');
  assert.equal(ui.element('#firmware-install').disabled, true);
  await ui.element('#firmware-form').events.submit({ preventDefault() {} });
  assert.equal(ui.calls.length, 0);
  assert.match(ui.element('#firmware-review-error').textContent, /changed/);
});

/** A failed POST response resolves the same identity by GET and survives a page reload. */
test('lost submission is never replayed and resolves after reload', async () => {
  const storage = new Map();
  const ui = harness(storage);
  ui.snapshot(candidate());
  await ui.run('reconcileFirmwareRequest()');
  let requestId;
  ui.network((path, options) => {
    if (options.method === 'POST') {
      const payload = JSON.parse(options.body);
      requestId = payload.request_id;
      assert.deepEqual(Object.keys(payload).sort(), ['candidate_id', 'request_id']);
      throw new TypeError('Lost reply');
    }
    assert.equal(path, '/api/commands/' + requestId);
    return { request_id: requestId, status: 'pending' };
  });
  ui.element('#firmware-review').events.click();
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
  ui.network((path, options) => ({
    request_id: JSON.parse(options.body).request_id,
    status: 'unknown',
  }));
  ui.element('#firmware-review').events.click();
  await ui.element('#firmware-form').events.submit({ preventDefault() {} });
  assert.equal(ui.element('#firmware-status').textContent, 'Needs recovery');
  assert.equal(ui.element('#firmware-review').disabled, true);
  assert.equal(ui.calls.length, 1);
});

'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { harness } = require('./web_harness');

/**
 * Create a fresh, server-classified mock announcement with no radio activity.
 * @param {number} outputs - Suggested gang count, either one or two.
 * @returns {Object} Discovery candidate with a reviewed-profile token.
 */
function discovery(outputs = 1) {
  return {
    device: '12345678',
    suffix: `000${outputs}`,
    rssi: -51,
    profile: {
      experimental: true,
      status: 'detected',
      outputs,
      suffix: `000${outputs}`,
      token: 'mock-profile',
      expires_at: Date.now() / 1000 + 60,
    },
  };
}

/**
 * Prepare the actual add dialog logic against isolated snapshots and HTTP transport.
 * @param {Object[]} candidates - Announcements to show in the dialog.
 * @returns {Object} Browser harness with selection and submit helpers.
 */
function setup(candidates = [discovery()]) {
  const ui = harness();
  ui.snapshot(null);
  ui.context.testSnapshot.discoveries = candidates;
  ui.element('#add-method').value = 'manual';
  ui.element('#new-id').value = 'AABBCCDD';
  ui.element('#new-outputs').value = '2';
  ui.element('#new-suffix').value = '0002';
  ui.element('#new-name').value = 'Hall lights';
  ui.element('#add-dialog').open = true;
  ui.run('refresh = async () => true; toast = () => {}; render = () => renderDiscoveries();');
  ui.auto = () => {
    ui.element('#add-method').value = 'auto';
    ui.element('#add-method').events.change();
  };
  /**
   * Choose one candidate using the production click handler.
   * @param {string} device - Identity on the selected button.
   */
  ui.select = (device = '12345678') =>
    ui.element('#discoveries').events.click({
      target: { closest: () => ({ dataset: { candidate: device }, disabled: false }) },
    });
  /**
   * Submit the actual form handler and await its asynchronous command flow.
   * @param {string} value - Save-only or save-and-pair submit intent.
   */
  ui.submit = (value = 'save') =>
    ui.element('#add-form').events.submit({
      preventDefault() {},
      submitter: { value },
      currentTarget: ui.element('#add-form'),
    });
  return ui;
}

/** Manual setup retains explicit counts, suffixes, naming and the original add request. */
test('manual setup remains default and retains its request contract', async () => {
  const ui = setup();
  ui.run('renderDiscoveries()');
  assert.equal(ui.element('#auto-help').hidden, true);
  assert.equal(ui.element('#new-id').readOnly, false);
  ui.network((path, options) => {
    const request = JSON.parse(options.body);
    assert.equal(request.action, 'add');
    assert.equal(request.payload.device, 'aabbccdd');
    assert.equal(request.payload.outputs, 2);
    assert.equal(request.payload.suffix, '0002');
    assert.equal(request.payload.name, 'Hall lights');
    assert.equal('auto_detect' in request.payload, false);
    return { status: 'confirmed', request_id: request.payload.request_id };
  });
  await ui.submit();
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.element('#add-dialog').open, false);
});

/** Each known profile needs explicit selection and sends only the reviewed server token. */
test('one- and two-gang auto detection preserve review and explicit admission', async () => {
  for (const outputs of [1, 2]) {
    const ui = setup([discovery(outputs)]);
    ui.auto();
    assert.equal(ui.element('#add-save').disabled, true);
    assert.equal(ui.element('#new-id').value, '');
    assert.match(ui.element('#discoveries').innerHTML, new RegExp(`${outputs}-gang switch`));
    ui.select();
    assert.equal(ui.element('#add-save').disabled, false);
    assert.equal(ui.element('#new-outputs').disabled, true);
    assert.equal(ui.element('#new-outputs').value, String(outputs));
    assert.match(ui.element('#auto-review').textContent, new RegExp(`${outputs}-gang`));
    // DOM tampering cannot override the profile that the user selected.
    ui.element('#new-outputs').value = '9';
    ui.element('#new-id').value = 'deadbeef';
    ui.network((path, options) => {
      const request = JSON.parse(options.body);
      assert.equal(request.action, 'add');
      assert.equal(request.payload.device, '12345678');
      assert.equal(request.payload.auto_detect, true);
      assert.equal(request.payload.profile_token, 'mock-profile');
      assert.equal('outputs' in request.payload, false);
      assert.equal('suffix' in request.payload, false);
      return { status: 'confirmed', request_id: request.payload.request_id };
    });
    await ui.submit();
    assert.equal(ui.calls.length, 1);
  }
});

/** Incomplete, unknown and ambiguous metadata never enables automatic registration. */
test('unsupported and ambiguous discoveries require manual setup', () => {
  for (const profile of [
    undefined,
    {},
    { ...discovery().profile, status: 'unknown' },
    { ...discovery().profile, status: 'ambiguous' },
    { ...discovery().profile, token: null },
    { ...discovery().profile, outputs: 2 },
    { ...discovery().profile, experimental: false },
  ]) {
    const ui = setup([{ ...discovery(), profile }]);
    ui.auto();
    ui.select();
    assert.equal(ui.element('#add-save').disabled, true);
    assert.equal(ui.run('addUi.selection'), null);
    assert.throws(() => ui.run('addDeviceFields()'), /fresh detected switch/);
  }
});

/** Changes to the reviewed evidence invalidate the selection until the user reviews again. */
test('expired and replaced detection cannot be silently submitted', async () => {
  for (const patch of [
    { expires_at: Date.now() / 1000 - 1 },
    { token: 'replacement' },
    { outputs: 2, suffix: '0002' },
    { status: 'ambiguous' },
  ]) {
    const candidate = discovery();
    const ui = setup([candidate]);
    ui.auto();
    ui.select();
    Object.assign(candidate.profile, patch);
    ui.run('renderDiscoveries()');
    assert.equal(ui.element('#add-save').disabled, true);
    assert.match(ui.element('#auto-review').textContent, /expired or changed/);
    await ui.submit();
    assert.equal(ui.calls.length, 0);
    assert.match(ui.element('#add-error').textContent, /fresh detected switch/);
  }
});

/** Repeated identical announcements extend freshness without discarding an unchanged review. */
test('same profile renewal remains selected; reclassification requires reselection', () => {
  const candidate = discovery();
  const ui = setup([candidate]);
  ui.auto();
  ui.select();
  candidate.profile.expires_at += 10;
  ui.run('renderDiscoveries()');
  assert.equal(ui.element('#add-save').disabled, false);
  candidate.profile.token = 'new-session';
  ui.run('renderDiscoveries()');
  assert.equal(ui.element('#add-save').disabled, true);
  ui.select();
  assert.equal(ui.element('#add-save').disabled, false);
  assert.equal(ui.run('addDeviceFields().profile_token'), 'new-session');
});

/** A failed automatic add keeps the review visible and cannot start pairing or retry. */
test('server rejection preserves dialog and never starts pairing', async () => {
  const ui = setup();
  ui.auto();
  ui.select();
  ui.network((path, options) => ({
    status: 'rejected',
    error: 'Detection expired',
    request_id: JSON.parse(options.body).payload.request_id,
  }));
  await ui.submit('pair');
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.element('#add-dialog').open, true);
  assert.match(ui.element('#add-error').textContent, /Detection expired/);
});

/** Save-and-pair keeps the original single-device, bounded pairing operation. */
test('automatic setup pairs only the explicitly selected device', async () => {
  const ui = setup();
  ui.auto();
  ui.select();
  const requests = [];
  ui.network((path, options) => {
    const request = JSON.parse(options.body);
    requests.push(request);
    return { status: 'confirmed', request_id: request.payload.request_id };
  });
  await ui.submit('pair');
  assert.deepEqual(
    requests.map((request) => request.action),
    ['add', 'pair'],
  );
  assert.equal(requests[1].payload.device, '12345678');
  assert.equal(requests[1].payload.seconds, 120);
});

/** Returning to manual setup restores all manual choices and preserves the friendly name. */
test('manual entries survive trying experimental detection', () => {
  const ui = setup();
  ui.auto();
  ui.select();
  ui.element('#add-method').value = 'manual';
  ui.element('#add-method').events.change();
  assert.equal(ui.element('#new-id').value, 'AABBCCDD');
  assert.equal(ui.element('#new-outputs').value, '2');
  assert.equal(ui.element('#new-suffix').value, '0002');
  assert.equal(ui.element('#new-name').value, 'Hall lights');
  assert.equal(ui.element('#new-id').readOnly, false);
  assert.equal(ui.element('#new-outputs').disabled, false);
});

/** Radio loss, firmware maintenance and already-registered candidates prevent automatic admission. */
test('gateway and maintenance guards apply to experimental setup', () => {
  const ui = setup();
  ui.auto();
  ui.select();
  for (const state of [
    'connected = false',
    'connected = true; snapshot.ready = false',
    'snapshot.ready = true; snapshot.firmware_update = { recovery: "required" }',
    'snapshot.firmware_update = null; snapshot.discoveries[0].configured = true',
  ]) {
    ui.run(state + '; renderDiscoveries()');
    assert.equal(ui.element('#add-save').disabled, true);
    assert.equal(ui.element('#add-pair').disabled, true);
  }
});

/** Even malformed discovery data remains text instead of executable HTML. */
test('discovery markup escapes untrusted identities and ignores raw reason markup', () => {
  const candidate = discovery();
  candidate.device = '<img src=x onerror=alert(1)>';
  candidate.profile.reason = '<script>alert(1)</script>';
  const ui = setup([candidate]);
  ui.auto();
  assert.doesNotMatch(ui.element('#discoveries').innerHTML, /<img|<script/);
  assert.match(ui.element('#discoveries').innerHTML, /&lt;img/);
});

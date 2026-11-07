'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { harness, Element } = require('./web_harness');

/**
 * Create a daemon session snapshot without opening a radio or mutating a real registry.
 * @param {Object} changes - Lifecycle and profile fields for the scenario.
 * @returns {Object} Public commissioning session.
 */
function session(changes = {}) {
  return {
    session_id: 'session-one',
    status: 'listening',
    seconds_remaining: 120,
    device: null,
    name: null,
    outputs: null,
    suffix: null,
    profile: null,
    registered: false,
    error: null,
    observations: [],
    ...changes,
  };
}

/**
 * Prepare actual UI handlers with a mutable authoritative snapshot and isolated storage.
 * @param {Map} storage - Optional storage shared across simulated reloads.
 * @returns {Object} UI fixture and session update helper.
 */
function setup(storage = new Map()) {
  const ui = harness(storage);
  ui.snapshot(null);
  ui.context.testSnapshot.commissioning_supported = true;
  ui.context.testSnapshot.commissioning = null;
  ui.run('refresh = async () => { await reconcileCommissioning(); render(); return true; };');
  /**
   * Publish a daemon-owned session transition and render its current state.
   * @param {Object|null} value - New authoritative session state.
   */
  ui.publish = (value) => {
    ui.context.testSnapshot.commissioning = value;
    ui.run('render()');
  };
  return ui;
}

/** Return the registered switch used for lifecycle and name-management checks. */
function device() {
  return {
    id: '12345678',
    name: 'Livolo 12345678',
    outputs: 2,
    suffix: '0002',
    slot: 0,
    availability: 'online',
    state: null,
  };
}

/** One primary click starts the bounded session; detection and registration cannot report success. */
test('primary click starts once then shows detected setting up pairing and confirmed', async () => {
  const ui = setup();
  ui.run('render()');
  assert.equal(ui.element('#pair-new-device').textContent, 'Pair new device');
  ui.network((path, options) => {
    assert.equal(path, '/api/commission');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['X-Livolo-Token'], 'csrf-test');
    const payload = JSON.parse(options.body);
    assert.deepEqual(Object.keys(payload).sort(), ['request_id', 'seconds']);
    assert.equal(payload.seconds, 120);
    assert.equal(ui.element('#pair-session-dialog').open, true);
    assert.match(ui.element('#pair-session-description').textContent, /Activate pairing/);
    ui.context.testSnapshot.commissioning = session({ session_id: payload.request_id });
    return { request_id: payload.request_id, status: 'listening', action: 'commission' };
  });
  await ui.element('#pair-new-device').events.click();
  assert.equal(ui.element('#pair-session-status').textContent, 'Listening');
  const current = ui.context.testSnapshot.commissioning;
  const detected = {
    ...current,
    device: '12345678',
    name: 'Livolo 12345678',
    outputs: 2,
    suffix: '0002',
    profile: 'livolo-ec-suffix-0002',
  };
  ui.context.testSnapshot.ready = false;
  ui.publish({ ...detected, status: 'registering', registered: true });
  assert.equal(ui.element('#pair-session-status').textContent, 'Setting up');
  assert.doesNotMatch(
    ui.element('#pair-session-description').textContent,
    /retry targeted pairing/,
  );
  assert.match(ui.element('#pair-session-device').innerHTML, /2-gang switch/);
  assert.match(ui.element('#pair-session-device').innerHTML, /12345678/);
  assert.doesNotMatch(ui.element('#pair-session-stage').textContent, /success/);
  ui.context.testSnapshot.ready = true;
  ui.context.testSnapshot.devices = [device()];
  ui.publish({ ...detected, status: 'pairing', registered: true });
  assert.equal(ui.element('#pair-session-status').textContent, 'Pairing');
  assert.doesNotMatch(
    ui.element('#pair-session-description').textContent,
    /retry targeted pairing/,
  );
  assert.equal(ui.element('#pair-session-name').hidden, true);
  await ui.element('#pair-new-device').events.click();
  assert.equal(ui.calls.length, 1);
  ui.publish({ ...detected, status: 'confirmed', registered: true, seconds_remaining: 0 });
  assert.equal(ui.element('#pair-session-status').textContent, 'Paired');
  assert.equal(ui.element('#pair-session-name').hidden, false);
  ui.element('#pair-session-name').events.click();
  assert.equal(ui.element('#device-dialog').open, true);
  assert.equal(ui.element('#device-name').value, 'Livolo 12345678');
  assert.equal(ui.calls.length, 1);
});

/** Every unsuccessful terminal result stays visibly distinct from confirmed pairing. */
test('terminal failures cancellation and zero countdown never imply paired', () => {
  const ui = setup();
  for (const status of ['expired', 'cancelled', 'conflict', 'failed', 'unknown']) {
    ui.publish(session({ status, seconds_remaining: 0 }));
    assert.notEqual(ui.element('#pair-session-status').textContent, 'Paired');
    assert.equal(ui.element('#pair-session-cancel').hidden, true);
    assert.equal(ui.element('#pair-session-banner').hidden, false);
  }
  ui.publish(
    session({
      status: 'pairing',
      seconds_remaining: 0,
      registered: true,
      device: '12345678',
      name: 'Hall',
    }),
  );
  assert.equal(ui.element('#pair-session-status').textContent, 'Pairing');
  assert.match(ui.element('#pair-session-time').textContent, /waiting for the gateway/);
});

/** Unsupported old daemons keep advanced setup but cannot start the new session API. */
test('missing capability offers advanced setup without a new API request', async () => {
  const ui = setup();
  delete ui.context.testSnapshot.commissioning_supported;
  ui.run('render()');
  assert.equal(ui.element('#pair-new-device').disabled, true);
  assert.equal(ui.element('#add-device').disabled, false);
  await ui.element('#pair-new-device').events.click();
  assert.equal(ui.calls.length, 0);
});

/** Result banners survive reload and are reopened without creating another session. */
test('authoritative retained result survives reload and disconnected state is explicit', async () => {
  const ui = setup();
  ui.publish(session({ status: 'confirmed', device: '12345678', outputs: 1, registered: true }));
  await ui.run('reconcileCommissioning()');
  ui.element('#pair-session-view').events.click();
  assert.equal(ui.element('#pair-session-status').textContent, 'Paired');
  const reloaded = setup();
  reloaded.publish(ui.context.testSnapshot.commissioning);
  await reloaded.run('reconcileCommissioning()');
  reloaded.element('#pair-session-view').events.click();
  assert.equal(reloaded.element('#pair-session-status').textContent, 'Paired');
  reloaded.run('connected = false; render()');
  assert.equal(reloaded.element('#pair-session-status').textContent, 'Disconnected');
  reloaded.run('connected = true; render()');
  assert.equal(reloaded.element('#pair-session-status').textContent, 'Paired');
  reloaded.publish(session({ status: 'unknown', error: 'daemon_restarted' }));
  assert.equal(reloaded.element('#pair-session-status').textContent, 'Unknown');
  assert.match(reloaded.element('#pair-session-error').textContent, /not resumed automatically/);
  assert.equal(reloaded.calls.length, 0);
});

/** Fresh unsupported and conflicting observations remain readable and cannot inject markup. */
test('unknown profile and multiple-device reasons are shown without selecting a profile', () => {
  const ui = setup();
  const observations = [
    { device: '12345678', reason: 'unknown_pairing_suffix' },
    { device: '<img src=x>', reason: 'unknown_pairing_payload' },
  ];
  ui.publish(session({ observations }));
  assert.equal(ui.element('#pair-session-status').textContent, 'Listening');
  assert.equal(ui.element('#pair-session-device').hidden, true);
  assert.match(ui.element('#pair-session-observations').innerHTML, /not supported/);
  assert.match(ui.element('#pair-session-observations').innerHTML, /&lt;img/);
  assert.doesNotMatch(ui.element('#pair-session-observations').innerHTML, /<img/);
  ui.publish(session({ status: 'conflict', observations, error: 'multiple_devices' }));
  assert.match(ui.element('#pair-session-error').textContent, /Multiple switches/);
  ui.publish(session({ status: 'expired', error: 'unsupported_device_seen' }));
  assert.match(ui.element('#pair-session-error').textContent, /unsupported switch profiles/);
});

/** A lost start reply remains bound to its ID across reload and only explicit cancel closes it. */
test('lost start reply never replays and absent-session cancellation resolves the attempt', async () => {
  const storage = new Map();
  const ui = setup(storage);
  let startId;
  ui.network((path, options) => {
    if (options.method === 'POST') startId = JSON.parse(options.body).request_id;
    throw new Error('Lost or missing reply');
  });
  await ui.element('#pair-new-device').events.click();
  assert.equal(ui.run('commissionUi.pending.session_id'), startId);
  assert.equal(ui.element('#pair-session-status').textContent, 'Checking');
  const reloaded = setup(storage);
  reloaded.network((path, options) => {
    if (options.method !== 'POST') throw new Error('No accepted start');
    assert.equal(path, '/api/commission_cancel');
    const payload = JSON.parse(options.body);
    assert.equal(payload.session_id, startId);
    return { request_id: payload.request_id, status: 'confirmed', action: 'commission_cancel' };
  });
  await reloaded.run('reconcileCommissioning(); render()');
  await reloaded.element('#pair-new-device').events.click();
  assert.equal(reloaded.calls.filter((c) => c.options.method === 'POST').length, 0);
  await reloaded.element('#pair-session-cancel').events.click();
  assert.equal(reloaded.element('#pair-session-status').textContent, 'Cancelled');
  assert.equal(reloaded.run('commissionUi.pending'), null);
  assert.equal(storage.size, 0);
});

/** Lost or mismatched cancellation acknowledgements cannot be mistaken for pairing success. */
test('cancel lost reply and mismatched request ID retain safe session tracking', async () => {
  for (const mismatch of [false, true]) {
    const ui = setup();
    ui.publish(session({ status: 'pairing' }));
    await ui.run('reconcileCommissioning()');
    ui.network((path, options) => {
      if (options.method === 'POST') {
        assert.equal(JSON.parse(options.body).session_id, 'session-one');
        if (mismatch) return { request_id: 'different-id', status: 'confirmed' };
      }
      throw new Error('Lost cancellation reply');
    });
    await ui.element('#pair-session-cancel').events.click();
    assert.equal(ui.run('commissionUi.pending.operation'), 'cancel');
    assert.equal(ui.element('#pair-session-status').textContent, 'Checking');
    ui.publish(session({ status: 'cancelled' }));
    await ui.run('reconcileCommissioning(); render()');
    assert.equal(ui.run('commissionUi.pending'), null);
    assert.equal(ui.element('#pair-session-status').textContent, 'Cancelled');
    assert.equal(ui.calls.filter((c) => c.options.method === 'POST').length, 1);
  }
});

/** Stored uncertainty from another logical network cannot cancel or resume its request here. */
test('cross-network reload isolates unresolved request storage', async () => {
  const storage = new Map();
  const ui = setup(storage);
  ui.network(() => {
    throw new Error('No response');
  });
  await ui.element('#pair-new-device').events.click();
  const other = setup(storage);
  other.context.testSnapshot.gateway = 'another-gateway';
  await other.run('reconcileCommissioning(); render()');
  assert.equal(other.run('commissionUi.pending'), null);
  assert.equal(other.calls.length, 0);
  assert.equal(storage.size, 1);
});

/** Registration followed by failure preserves the device and points to targeted retry. */
test('registered but unconfirmed switch offers management without claiming success', () => {
  const ui = setup();
  ui.context.testSnapshot.devices = [device()];
  ui.publish(
    session({
      status: 'expired',
      device: '12345678',
      registered: true,
      outputs: 2,
      error: 'confirmation_timeout',
    }),
  );
  assert.equal(ui.element('#pair-session-status').textContent, 'Expired');
  assert.match(ui.element('#pair-session-description').textContent, /remains registered/);
  assert.equal(ui.element('#pair-session-name').textContent, 'Manage registered device');
  assert.equal(ui.calls.length, 0);
});

/** All ordinary mutation controls stay blocked during admission; cancellation remains available. */
test('active session blocks competing device MQTT and firmware actions', async () => {
  const ui = setup();
  const rename = new Element();
  const mqtt = new Element();
  ui.element('#rename-form').controls.push(rename);
  ui.element('#mqtt-form').controls.push(mqtt);
  ui.context.testSnapshot.mqtt = { enabled: true, editable: true };
  ui.publish(session());
  assert.equal(ui.element('#add-device').disabled, true);
  assert.equal(rename.disabled, true);
  assert.equal(mqtt.disabled, true);
  assert.equal(ui.element('#pair-session-cancel').disabled, false);
  await assert.rejects(ui.run("command('rename', { device:'12345678', name:'Room' })"), /paused/);
  assert.equal(ui.calls.length, 0);
});

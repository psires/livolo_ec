'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const { webcrypto } = require('node:crypto');
const vm = require('node:vm');

/** Minimal DOM surface for exercising real browser handlers without an installed browser. */
class Element {
  /** Initialize mutable browser properties and registered event callbacks. */
  constructor() {
    this.hidden = false;
    this.disabled = false;
    this.open = false;
    this.textContent = '';
    this.innerHTML = '';
    this.value = '';
    this.dataset = {};
    this.attributes = {};
    this.events = {};
    this.controls = [];
    this.classList = { toggle() {} };
  }

  /**
   * Capture a handler for an explicit test action.
   * @param {string} type - DOM event name.
   * @param {Function} callback - Production event handler.
   */
  addEventListener(type, callback) {
    this.events[type] = callback;
  }

  /** Open a review dialog without firing unrelated events. */
  showModal() {
    this.open = true;
  }

  /** Close a review dialog without submitting it. */
  close() {
    this.open = false;
  }

  /**
   * Represent DOM attribute removal, including indeterminate progress.
   * @param {string} name - Attribute to remove.
   */
  removeAttribute(name) {
    delete this.attributes[name];
    if (name === 'value') this.value = null;
  }

  /**
   * Record an attribute for subsequent rendering assertions.
   * @param {string} name - Attribute name.
   * @param {string} value - Attribute value.
   */
  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  /** Return no nested controls when they are irrelevant to a scenario. */
  querySelectorAll() {
    return this.controls;
  }

  /** Indicate that no element currently holds focus inside this stub. */
  contains() {
    return false;
  }
}

/**
 * Load the actual application into an isolated DOM and network harness.
 * @param {Map} storage - Session storage shared across simulated reloads.
 * @returns {Object} Context, elements, request log and test interaction helpers.
 */
function harness(storage = new Map()) {
  const elements = new Map();
  /**
   * Return a stable element for each selector used by the application.
   * @param {string} selector - Production DOM selector.
   * @returns {Element} Stable element stub.
   */
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  element('meta[name="livolo-base-path"]').content = '/';
  const calls = [];
  const context = vm.createContext({
    document: { querySelector: element, querySelectorAll: () => [], activeElement: null },
    window: { addEventListener() {} },
    location: { hash: '#network' },
    sessionStorage: {
      getItem: (key) => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    crypto: webcrypto,
    URL,
    AbortController,
    setTimeout: () => 1,
    clearTimeout() {},
    fetch: () => new Promise(() => {}),
  });
  vm.runInContext(readFileSync(join(__dirname, '../livolod/static/app.js'), 'utf8'), context);
  // Initial automatic polling is parked. Tests control snapshots and run the same
  // firmware reconciliation/rendering functions after an explicit submission.
  vm.runInContext(
    'refresh = async () => { await reconcileFirmwareRequest(); renderFirmware(); return true; };',
    context,
  );
  /**
   * Install a known authoritative snapshot for the next interaction.
   * @param {Object|null} firmware - Firmware summary, null to emulate an older daemon.
   */
  const snapshot = (firmware) => {
    context.testSnapshot = {
      gateway: '2a992b12',
      tag: 'a7',
      ready: true,
      csrf_token: 'csrf-test',
      devices: [],
      discoveries: [],
      results: [],
      diagnostics: {},
      mqtt: { enabled: false },
      ...(firmware ? { firmware_update: firmware } : {}),
    };
    vm.runInContext('snapshot = testSnapshot; connected = true;', context);
  };
  /**
   * Substitute only HTTP transport, retaining production request and handler logic.
   * @param {Function} responder - Response producer receiving path and request options.
   */
  const network = (responder) => {
    context.fetch = async (path, options) => {
      calls.push({ path, options });
      const body = await responder(path, options);
      return { ok: true, status: 200, json: async () => body };
    };
  };
  return {
    context,
    element,
    snapshot,
    network,
    calls,
    storage,
    run: (source) => vm.runInContext(source, context),
  };
}

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

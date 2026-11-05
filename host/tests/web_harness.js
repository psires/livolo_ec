'use strict';

const { readFileSync } = require('node:fs');
const { join } = require('node:path');
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

  /** Record keyboard focus without dispatching additional events. */
  focus() {
    this.focused = true;
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

module.exports = { harness, Element };

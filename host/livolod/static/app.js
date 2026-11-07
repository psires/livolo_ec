'use strict';
/**
 * Find the first matching UI element within a chosen root.
 *
 * @param {string} selector - CSS selector for the desired element.
 * @param {Document|Element} [root=document] - Search root.
 * @returns {Element|null} The matching element, if present.
 */
const $ = (selector, root = document) => root.querySelector(selector);
/**
 * Collect matching UI elements for rendering or event binding.
 *
 * @param {string} selector - CSS selector for the desired elements.
 * @param {Document|Element} [root=document] - Search root.
 * @returns {Element[]} Matching elements in document order.
 */
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
/**
 * Make a dynamic value safe to include in HTML text or quoted attributes.
 *
 * @param {*} value - Value to display; nullish values become empty text.
 * @returns {string} Escaped HTML text.
 */
const escapeHTML = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
/**
 * Pause asynchronous polling without blocking UI interaction.
 *
 * @param {number} ms - Delay in milliseconds.
 * @returns {Promise<void>} A promise resolved after the delay.
 */
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let snapshot = null,
  connected = false,
  authToken = '',
  filter = 'all',
  selectedId = null,
  refreshPromise = null;
const commissionUi = { pending: null, storageKey: null, posting: false, local: null, error: '' };
const addUi = { selection: null, manual: null };
const busyDevices = new Set();
const watched = new Map();
const localResults = new Map();
const finalErrors = new Set(['rejected', 'unknown', 'unconfirmed', 'busy', 'timeout']);
const firmwareUi = {
  preflight: { sequence: 0, key: null, checking: false, report: null, error: '' },
  review: null,
  request: null,
  storageKey: null,
  submitting: false,
  result: null,
  error: '',
};
/**
 * Allocate a browser command identifier for durable daemon deduplication.
 *
 * @returns {string} A random web-prefixed request identifier.
 */
const requestId = () =>
  'web-' +
  [...crypto.getRandomValues(new Uint8Array(16))]
    .map((n) => n.toString(16).padStart(2, '0'))
    .join('');

/**
 * Show a dismissible operation result, keeping errors visible until dismissed.
 *
 * @param {string} message - User-facing notification text.
 * @param {boolean} [error=false] - Whether to display a persistent error notification.
 */
function toast(message, error = false) {
  const item = document.createElement('div');
  item.className = 'toast' + (error ? ' error' : '');
  const text = document.createElement('span');
  text.textContent = message;
  const close = document.createElement('button');
  close.textContent = '×';
  close.setAttribute('aria-label', 'Dismiss notification');
  /** Dismiss this notification without changing other operation feedback. */
  close.addEventListener('click', () => item.remove());
  item.append(text, close);
  $('#toasts').append(item);
  if (!error) setTimeout(() => item.remove(), 7000);
  while ($('#toasts').children.length > 4) $('#toasts').firstElementChild.remove();
}
/**
 * Display or clear a form or connection error beside its related controls.
 *
 * @param {string} id - CSS selector for the error container.
 * @param {string} message - Error text, or empty text to hide the container.
 */
function showError(id, message) {
  const el = $(id);
  el.textContent = message;
  el.hidden = !message;
}
/**
 * Present a modal dialog without reopening one that is already active.
 *
 * @param {string} id - CSS selector for the dialog.
 */
function openDialog(id) {
  const dialog = $(id);
  if (!dialog.open) dialog.showModal();
}
/**
 * Look up a registered device in the latest gateway snapshot.
 *
 * @param {string} id - Device identity in wire-order hexadecimal.
 * @returns {Object|undefined} The matching device, when available.
 */
function deviceById(id) {
  return snapshot?.devices.find((d) => d.id === id);
}
/**
 * Choose a readable device label even when registry data is unavailable.
 *
 * @param {string} id - Device identity, or an empty value for the gateway.
 * @returns {string} Friendly name, identity, or gateway fallback.
 */
function deviceName(id) {
  return deviceById(id)?.name || id || 'Gateway';
}
/**
 * Describe how recently a device report was received.
 *
 * @param {number} timestamp - Report Unix timestamp in seconds, or a falsy missing value.
 * @returns {string} Human-readable report age.
 */
function relativeTime(timestamp) {
  if (!timestamp) return 'No report yet';
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - timestamp));
  if (seconds < 5) return 'Just now';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
/**
 * Resolve a browser API path beneath the server-validated ingress prefix.
 *
 * @param {string} path - Local browser API route.
 * @returns {string} Same-origin path in this UI's mount.
 */
function apiUrl(path) {
  const base = document.querySelector('meta[name="livolo-base-path"]').content;
  return base + path.replace(/^\//, '');
}

/**
 * Request gateway JSON with bounded waiting and actionable connection errors.
 *
 * @param {string} path - Same-origin API path.
 * @param {RequestInit} [options={}] - Fetch method, headers, and body overrides.
 * @returns {Promise<*>} The decoded successful response.
 */
async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(apiUrl(path), {
      cache: 'no-store',
      ...options,
      signal: controller.signal,
      headers: {
        ...(authToken ? { Authorization: 'Bearer ' + authToken } : {}),
        ...(options.headers || {}),
      },
    });
    const value = await response.json();
    if (response.status === 401) {
      connected = false;
      openDialog('#login-dialog');
      throw new Error('Access token required.');
    }
    if (!response.ok) throw new Error(value.error || `Request failed (${response.status}).`);
    return value;
  } catch (error) {
    if (error.name === 'AbortError')
      throw new Error('Gateway response timed out. Check Activity before trying again.');
    if (error instanceof TypeError)
      throw new Error('Cannot reach the gateway. Check its connection and try again.');
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
/**
 * Provide a consistent snapshot shape for the UI and bridge metadata.
 *
 * @param {Object} value - Gateway state response.
 * @returns {Object} Snapshot with device, discovery, and result arrays.
 */
function normalize(value) {
  return {
    ...value.bridge,
    ...value,
    devices: value.devices || [],
    discoveries: value.discoveries || [],
    results: value.results || [],
  };
}
/**
 * Refresh gateway state while sharing concurrent refresh requests.
 *
 * @returns {Promise<boolean>} Whether the gateway snapshot was obtained successfully.
 */
async function refresh() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      snapshot = normalize(await api('/api/state'));
      connected = true;
      showError('#connection-error', '');
      for (const result of [...snapshot.results].reverse()) observeResult(result);
      await reconcileFirmwareRequest();
      await reconcileCommissioning();
      render();
      return true;
    } catch (error) {
      connected = false;
      showError(
        '#connection-error',
        error.message + (snapshot ? ' Displayed reports may be out of date.' : ''),
      );
      render();
      return false;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}
/**
 * Merge command activity and notify users when watched pairing finishes.
 *
 * @param {Object} result - Daemon command result containing a request_id and status.
 */
function observeResult(result) {
  if (!result?.request_id) return;
  const previous = localResults.get(result.request_id);
  localResults.set(result.request_id, { ...previous, ...result });
  const watch = watched.get(result.request_id);
  if (watch && !['pending', 'pairing'].includes(result.status)) {
    watched.delete(result.request_id);
    if (watch.action === 'pair') {
      const name = deviceName(watch.device);
      if (result.status === 'confirmed')
        toast(`${name} paired. A device report confirmed the connection.`);
      else if (result.status === 'cancelled') toast(`Pairing stopped for ${name}.`);
      else
        toast(
          `Pairing ${name}: ${result.status}. ${result.error || 'Try the documented pairing procedure again.'}`,
          true,
        );
    }
  }
  if (localResults.size > 100) localResults.delete(localResults.keys().next().value);
}
/**
 * Submit one deduplicated command and await its bounded daemon outcome.
 *
 * @param {string} action - Requested daemon operation.
 * @param {Object} fields - Operation parameters before assigning a request identifier.
 * @returns {Promise<Object>} The accepted final or ongoing pairing result.
 */
async function command(action, fields) {
  if (!connected || !snapshot) throw new Error('Connect to the gateway before making changes.');
  if (firmwareBusy()) throw new Error('Device changes are paused for firmware maintenance.');
  if (commissioningActive(snapshot.commissioning))
    throw new Error('Device changes are paused while pairing a new switch.');
  const payload = { ...fields, request_id: requestId() };
  const local = {
    request_id: payload.request_id,
    action,
    device: fields.device,
    status: 'pending',
    updated: Date.now() / 1000,
  };
  localResults.set(payload.request_id, local);
  renderActivity();
  let result;
  try {
    result = await api('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Livolo-Token': snapshot.csrf_token },
      body: JSON.stringify({ action, payload }),
    });
    observeResult(result);
    const deadline = Date.now() + 20000;
    while (result.status === 'pending' && Date.now() < deadline) {
      await sleep(250);
      result = await api('/api/commands/' + encodeURIComponent(payload.request_id));
      observeResult(result);
    }
    if (result.status === 'pending')
      throw new Error('Request is still pending. Check Activity before trying again.');
    if (finalErrors.has(result.status))
      throw new Error(result.error || `Request ${result.status}.`);
    if (action === 'pair' && result.status === 'pairing')
      watched.set(payload.request_id, { action, device: fields.device });
    return result;
  } catch (error) {
    if (!result) observeResult({ ...local, status: 'unknown', error: error.message });
    throw error;
  } finally {
    await refresh();
    renderActivity();
  }
}
/**
 * Refresh connection indicators and visible views from the current snapshot.
 */
function render() {
  const ready = connected && snapshot?.ready;
  const maintenance = firmwareBusy();
  const connection = $('#connection');
  connection.className = 'connection' + (ready ? ' online' : connected ? '' : ' error');
  connection.innerHTML =
    '<span class="dot"></span>' +
    (connected && maintenance
      ? 'Firmware maintenance'
      : ready
        ? 'Gateway connected'
        : connected
          ? 'Radio reconnecting'
          : 'Gateway disconnected');
  $('#maintenance-banner').hidden = !maintenance;
  $('#maintenance-message').textContent = snapshot?.firmware_update?.recovery
    ? 'Firmware recovery is required. Device changes remain unavailable.'
    : 'Firmware maintenance is in progress. Device changes are temporarily unavailable.';
  $('#refresh-label').textContent = connected ? 'Updates automatically' : 'Connection interrupted';
  $('#simulation').hidden = !snapshot?.simulated;
  $('#add-device').disabled =
    !connected ||
    maintenance ||
    commissioningActive(snapshot?.commissioning) ||
    Boolean(commissionUi.pending) ||
    $('#add-form').dataset.busy === 'true';
  renderCommissioning();
  for (const id of ['#add-form', '#rename-form', '#forget-form']) {
    const form = $(id);
    $$('input,select,button[type="submit"]', form).forEach(
      (control) =>
        (control.disabled =
          !connected ||
          maintenance ||
          commissioningActive(snapshot?.commissioning) ||
          form.dataset.busy === 'true'),
    );
  }
  if (!snapshot) {
    $('#empty-state h2').textContent = 'Waiting for your gateway';
    $('#empty-state p').textContent =
      'Check that the Livolo daemon is running, then this page will reconnect automatically.';
    return;
  }
  const online = snapshot.devices.filter((d) => d.availability === 'online').length;
  $('#total-count').textContent = snapshot.devices.length;
  $('#nav-count').textContent = snapshot.devices.length;
  $('#online-count').textContent = connected ? online : '—';
  $('#offline-count').textContent = connected ? snapshot.devices.length - online : '—';
  renderDevices();
  renderPairing();
  renderActivity();
  renderNetwork();
  if ($('#add-dialog').open) renderDiscoveries();
  if ($('#device-dialog').open) renderDeviceDetails();
}
/**
 * Show filtered device reports and eligible controls while preserving keyboard focus.
 */
function renderDevices() {
  const search = $('#search').value.trim().toLowerCase();
  const devices = snapshot.devices.filter(
    (d) =>
      (filter === 'all' || d.availability === filter) &&
      `${d.name} ${d.id}`.toLowerCase().includes(search),
  );
  devices.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  const html = devices
    .map((d) => {
      const online = connected && d.availability === 'online';
      const state = d.state || {};
      const controlsDisabled = snapshot.diagnostics?.output_mode === 'disabled';
      const disabled =
        controlsDisabled ||
        firmwareBusy() ||
        commissioningActive(snapshot.commissioning) ||
        !connected ||
        !snapshot.ready ||
        !online ||
        busyDevices.has(d.id) ||
        snapshot.pairing?.device === d.id;
      const disabledReason = firmwareBusy()
        ? 'Device changes are paused for firmware maintenance'
        : controlsDisabled
          ? 'Output controls are disabled in gateway configuration'
          : 'Requires an online device and available radio';
      const outputs = Array.from({ length: d.outputs }, (_, n) => {
        const value =
          state.outputs?.[String(n + 1)] ??
          (Number.isInteger(state.state_bits)
            ? state.state_bits & (1 << n)
              ? 'ON'
              : 'OFF'
            : null);
        const pending = busyDevices.has(d.id);
        return `<button class="output-button ${pending ? 'pending' : value === 'ON' ? 'on' : ''}" data-output="${n}" data-device="${escapeHTML(d.id)}" ${disabled ? 'disabled' : ''} aria-label="${escapeHTML(d.name)}, output ${n + 1}: ${value || 'unknown'}${online ? '' : ', device offline'}" ${value ? `aria-pressed="${value === 'ON'}"` : ''} title="${disabled ? disabledReason : 'Toggle output ' + (n + 1)}"><span class="output-number">${n + 1}</span>${pending ? '…' : value || '—'}</button>`;
      }).join('');
      return `<tr><td><div class="device-identity"><span class="switch-icon" aria-hidden="true">${'<i></i>'.repeat(d.outputs)}</span><div><div class="device-title" title="${escapeHTML(d.name)}">${escapeHTML(d.name)}</div><div class="device-id">${escapeHTML(d.id)} · ${d.outputs} output${d.outputs === 1 ? '' : 's'}</div></div></div></td><td><span class="badge ${online ? 'online' : ''}">${!connected ? 'Unknown' : online ? 'Online' : 'Offline'}</span></td><td><div class="outputs">${outputs}</div></td><td><span class="signal ${online ? '' : 'stale'}"><span class="signal-bars" aria-hidden="true"><i></i><i></i><i></i><i></i></span>${Number.isFinite(state.rssi) ? `${state.rssi} dBm` : '—'}</span></td><td title="${state.last_seen ? escapeHTML(new Date(state.last_seen * 1000).toLocaleString()) : ''}">${relativeTime(state.last_seen)}</td><td><button class="manage-button" data-manage="${escapeHTML(d.id)}" aria-label="Manage ${escapeHTML(d.name)}">Manage</button></td></tr>`;
    })
    .join('');
  const list = $('#device-list');
  if (list.innerHTML !== html) {
    const focus = document.activeElement;
    const focusKey = list.contains(focus)
      ? { device: focus.dataset.device, output: focus.dataset.output, manage: focus.dataset.manage }
      : null;
    list.innerHTML = html;
    if (focusKey) {
      const match = $$('button', list).find((b) =>
        focusKey.manage
          ? b.dataset.manage === focusKey.manage
          : b.dataset.device === focusKey.device && b.dataset.output === focusKey.output,
      );
      if (match && !match.disabled) match.focus({ preventScroll: true });
    }
  }
  $('#empty-state').hidden = devices.length > 0;
  if (!devices.length) {
    const empty = !snapshot.devices.length;
    $('#empty-state h2').textContent = empty ? 'Your home starts here' : 'No matching devices';
    $('#empty-state p').textContent = empty
      ? 'Pair your first Livolo switch to connect it to your gateway.'
      : 'Try another name, device ID, or status filter.';
  }
  $('#list-summary').textContent =
    `${devices.length} of ${snapshot.devices.length} device${snapshot.devices.length === 1 ? '' : 's'}`;
}
/**
 * Show the active pairing target, remaining window, and cancellation availability.
 */
function renderPairing() {
  const pair = snapshot.pairing;
  $('#pairing-banner').hidden = !pair || commissioningActive(snapshot.commissioning);
  if (!pair) return;
  const seconds = Math.max(0, pair.seconds_remaining || 0);
  $('#pairing-title').textContent =
    `Waiting for ${deviceName(pair.device)} · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} remaining`;
  $('#pairing-description').textContent =
    'Put this switch into pairing mode using its documented procedure. A device report confirms success.';
  $('#stop-pairing').disabled = !connected || !snapshot.ready || firmwareBusy();
}
/**
 * Identify daemon-owned commissioning phases, including expected radio reconciliation.
 * @param {Object|null} session - Snapshot or durable commissioning result.
 * @returns {boolean} Whether the bounded session still owns admission.
 */
function commissioningActive(session) {
  return ['listening', 'registering', 'pairing'].includes(session?.status);
}

/**
 * Persist only unresolved request identities; never replay a start after reload.
 * @param {Object|null} intent - Start/cancel operation with request and session identities.
 */
function rememberCommissioning(intent) {
  commissionUi.pending = intent;
  try {
    if (intent) sessionStorage.setItem(commissionUi.storageKey, JSON.stringify(intent));
    else sessionStorage.removeItem(commissionUi.storageKey);
  } catch {
    // The daemon snapshot still exposes accepted sessions when browser storage is unavailable.
  }
}

/**
 * Apply a result to its original operation; cancellation confirmation is never pairing success.
 * @param {Object} intent - Original operation and target session identity.
 * @param {Object} result - Durable command envelope returned by the daemon.
 */
function acceptCommissioningResult(intent, result) {
  if (!result || result.request_id !== intent.request_id)
    throw new Error('Pairing request ID mismatch.');
  observeResult(result);
  const current = snapshot?.commissioning;
  const matching = current?.session_id === intent.session_id;
  if (intent.operation === 'cancel') {
    if (result.status === 'confirmed') {
      commissionUi.local =
        matching && !commissioningActive(current)
          ? null
          : {
              ...(matching ? current : commissionUi.local),
              session_id: intent.session_id,
              status: 'cancelled',
              error: null,
            };
      rememberCommissioning(null);
      commissionUi.error = '';
    } else if (['rejected', 'failed', 'unknown'].includes(result.status)) {
      commissionUi.error =
        result.error || 'Cancellation was not confirmed. Check the session or cancel again.';
      rememberCommissioning({
        operation: 'start',
        request_id: intent.session_id,
        session_id: intent.session_id,
      });
    }
    return;
  }
  if (
    ['confirmed', 'expired', 'cancelled', 'conflict', 'failed', 'unknown', 'rejected'].includes(
      result.status,
    )
  ) {
    commissionUi.local = matching
      ? null
      : {
          ...result,
          session_id: intent.session_id,
          status: result.status === 'rejected' ? 'failed' : result.status,
        };
    rememberCommissioning(null);
    commissionUi.error = result.error || '';
  }
}

/** Resolve uncertain requests by snapshot or GET while preserving their original identity. */
async function reconcileCommissioning() {
  const key = 'livolo-commission:' + apiUrl('/') + ':' + snapshot?.gateway + ':' + snapshot?.tag;
  if (commissionUi.storageKey !== key) {
    commissionUi.storageKey = key;
    commissionUi.pending = null;
    commissionUi.local = null;
    commissionUi.error = '';
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
      if (
        saved &&
        ['start', 'cancel'].includes(saved.operation) &&
        [saved.request_id, saved.session_id].every(
          (id) => typeof id === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(id),
        )
      )
        commissionUi.pending = saved;
    } catch {
      // Invalid browser storage must not create or replay any daemon operation.
    }
  }
  const intent = commissionUi.pending;
  if (!intent || commissionUi.posting) return;
  const current = snapshot?.commissioning;
  if (
    current?.session_id === intent.session_id &&
    (intent.operation === 'start' || !commissioningActive(current))
  ) {
    commissionUi.local = null;
    commissionUi.error = '';
    rememberCommissioning(null);
    return;
  }
  try {
    const result = await api('/api/commands/' + encodeURIComponent(intent.request_id));
    if (commissionUi.pending !== intent) return;
    acceptCommissioningResult(intent, result);
  } catch {
    commissionUi.error =
      'The request outcome is not confirmed. Checking the same request; cancel to close this attempt safely.';
  }
}

/**
 * Choose authoritative session data without mistaking an older session for a newly submitted one.
 * @returns {Object|null} Visible session or an unresolved local request placeholder.
 */
function displayedCommissioning() {
  const current = snapshot?.commissioning;
  const intent = commissionUi.pending;
  if (intent && current?.session_id !== intent.session_id)
    return { ...(commissionUi.local || {}), session_id: intent.session_id, status: 'unknown' };
  if (!intent && commissioningActive(current)) return current;
  if (commissionUi.local && current?.session_id !== commissionUi.local.session_id)
    return commissionUi.local;
  return current || commissionUi.local;
}

/**
 * Explain stable session and observation reasons in ordinary pairing language.
 * @param {string|null} reason - Public daemon reason or local transport explanation.
 * @returns {string} User-visible explanation without internal operation names.
 */
function commissioningReason(reason) {
  const messages = {
    unknown_pairing_suffix:
      'This switch type is not supported by automatic pairing. Use advanced setup.',
    unknown_pairing_payload: 'The switch’s pairing message is not recognized. Use advanced setup.',
    unsupported_device_seen:
      'Only unsupported switch profiles were heard. Use advanced setup with the known device settings.',
    known_pairing_signature: 'Supported switch detected.',
    multiple_devices:
      'Multiple switches requested pairing. Activate only the intended switch and try again.',
    conflicting_pairing_profiles:
      'Conflicting switch profiles were heard. Use advanced setup or try with only one switch in pairing mode.',
    discovery_timeout: 'No supported new switch was detected before the window closed.',
    confirmation_timeout:
      'The device was registered, but a fresh report did not confirm pairing before the window closed.',
    daemon_restarted:
      'The gateway restarted during pairing. The session was not resumed automatically.',
    radio_session_ended: 'The radio disconnected unexpectedly. Pairing could not be confirmed.',
    registry_full: 'The device registry is full. Free a slot before trying again.',
    registry_error: 'The gateway could not save the device. Check gateway storage.',
    radio_busy: 'The radio is busy. Wait for its current operation to finish.',
    commissioning_active: 'Another pairing session is already running.',
    not_a_session: 'This request does not identify a pairing session.',
  };
  return messages[reason] || reason || '';
}

/** Render listening, detected profile and terminal outcomes without optimistic pairing success. */
function renderCommissioning() {
  const session = displayedCommissioning();
  const active = commissioningActive(session);
  const unresolved = Boolean(commissionUi.pending);
  const viewable = active || unresolved || commissionUi.posting;
  const primary = $('#pair-new-device');
  primary.textContent = viewable ? 'View pairing' : 'Pair new device';
  primary.disabled =
    !viewable &&
    (!connected ||
      !snapshot?.ready ||
      firmwareBusy() ||
      snapshot?.commissioning_supported !== true ||
      Boolean(snapshot?.pairing));
  primary.title =
    snapshot?.commissioning_supported === true
      ? ''
      : 'Update the daemon for automatic pairing; advanced setup remains available.';
  const stages = {
    listening: [
      'Listening',
      'Activate pairing on your switch',
      'The gateway is listening for one supported switch. Keep only the intended switch in pairing mode. Device changes pause until this session ends.',
    ],
    registering: [
      'Setting up',
      'Switch detected',
      'Saving the detected profile and preparing the targeted pairing exchange. Registration alone does not confirm pairing.',
    ],
    pairing: [
      'Pairing',
      'Connecting your switch',
      'Waiting for a fresh device report to confirm pairing. Keep the switch in its documented pairing mode.',
    ],
    confirmed: [
      'Paired',
      'Device paired successfully',
      'A fresh device report confirmed pairing. You can now give the switch a friendly name.',
    ],
    expired: [
      'Expired',
      'Pairing window expired',
      'No confirmed pairing completed within this session. Activate pairing on the switch and try again when ready.',
    ],
    cancelled: [
      'Cancelled',
      'Pairing session closed',
      'Automatic admission has stopped. Cancellation does not reset or unpair a physical switch.',
    ],
    conflict: [
      'Conflict',
      'More than one candidate or conflicting reports',
      'Put only one supported switch into pairing mode, then start a new session. Advanced setup remains available.',
    ],
    failed: [
      'Failed',
      'Pairing did not complete',
      'Check the switch and gateway, then try again. No successful pairing was confirmed.',
    ],
    unknown: [
      'Unknown',
      'Pairing outcome is unknown',
      'The gateway cannot confirm the outcome. Check the switch before attempting another pairing.',
    ],
  };
  let detail = stages[session?.status] || [
    'Ready',
    'Pair a new switch',
    'Start a bounded session, then activate the switch’s documented pairing mode.',
  ];
  if (commissionUi.posting)
    detail =
      commissionUi.pending?.operation === 'cancel'
        ? [
            'Cancelling',
            'Closing the pairing session…',
            'Waiting for the gateway to confirm cancellation.',
          ]
        : [
            'Starting',
            'Opening a 2-minute pairing window…',
            'Activate pairing on the intended switch using its documented procedure.',
          ];
  else if (unresolved)
    detail = [
      'Checking',
      'Checking the pairing request',
      'The request outcome is not confirmed. Do not start another session; cancel to close this attempt.',
    ];
  if (!connected && (session || viewable))
    detail = [
      'Disconnected',
      'Connection interrupted',
      'The last reported outcome may be out of date. Reconnect to check this session; no start request will be replayed.',
    ];
  $('#pair-session-status').textContent = detail[0];
  $('#pair-session-status').className =
    'badge' + (connected && !viewable && session?.status === 'confirmed' ? ' online' : '');
  $('#pair-session-stage').textContent = detail[1];
  $('#pair-session-description').textContent =
    detail[2] +
    (connected && !viewable && session?.registered && session.status !== 'confirmed'
      ? ' The device remains registered. Open its device details to retry targeted pairing; it has not been removed.'
      : '');
  const seconds = session?.seconds_remaining;
  $('#pair-session-time').textContent =
    connected && active && Number.isFinite(seconds)
      ? Math.max(0, Math.ceil(seconds)) > 0
        ? `${Math.ceil(seconds)} seconds remaining`
        : 'Window ending; waiting for the gateway’s final result.'
      : '';
  const rows = session?.device
    ? [
        [
          'Detected type',
          [1, 2].includes(session.outputs)
            ? `Livolo EC ${session.outputs}-gang switch`
            : 'Livolo EC switch',
        ],
        ['Device ID', session.device],
        ['Outputs', session.outputs ?? 'Unknown'],
        ['Name', session.name || deviceName(session.device)],
      ]
    : [];
  $('#pair-session-device').hidden = !rows.length;
  $('#pair-session-device').innerHTML = rows
    .map(([label, value]) => `<dt>${label}</dt><dd>${escapeHTML(value)}</dd>`)
    .join('');
  const observations = Array.isArray(session?.observations) ? session.observations.slice(-8) : [];
  $('#pair-session-observations').hidden = !observations.length;
  $('#pair-session-observations').innerHTML =
    '<strong>Heard during this session</strong><ul>' +
    observations
      .map(
        (item) =>
          `<li><span>${escapeHTML(item.device)}</span><small>${escapeHTML(commissioningReason(item.reason))}</small></li>`,
      )
      .join('') +
    '</ul>';
  showError('#pair-session-error', commissioningReason(commissionUi.error || session?.error));
  $('#pair-session-background').hidden = !viewable;
  $('#pair-session-cancel').hidden = !viewable;
  $('#pair-session-cancel').disabled = !connected || commissionUi.posting;
  $('#pair-session-cancel').textContent = unresolved ? 'Close this attempt' : 'Cancel pairing';
  const manageable = session?.registered && deviceById(session.device);
  $('#pair-session-name').hidden = !manageable || active || unresolved;
  $('#pair-session-name').disabled = !connected || firmwareBusy();
  $('#pair-session-name').textContent =
    session?.status === 'confirmed' ? 'Name this device' : 'Manage registered device';
  $('#pair-session-banner').hidden = !session && !viewable;
  $('#pair-session-banner-title').textContent = detail[1];
  $('#pair-session-banner-description').textContent = session?.device
    ? `Device ${session.device} · ${detail[0]}`
    : detail[2];
}

/**
 * Submit one start or targeted cancellation and let snapshots own the session lifecycle.
 * @param {'start'|'cancel'} operation - Explicit user action; never an automatic retry.
 * @returns {Promise<void>} Completion of the submission and immediate status refresh.
 */
async function submitCommissioning(operation) {
  if (!connected || commissionUi.posting) return;
  const current = displayedCommissioning();
  if (
    operation === 'start' &&
    (!snapshot?.ready ||
      firmwareBusy() ||
      snapshot?.commissioning_supported !== true ||
      commissioningActive(current) ||
      commissionUi.pending ||
      snapshot.pairing)
  )
    return;
  const sessionId =
    operation === 'start' ? requestId() : commissionUi.pending?.session_id || current?.session_id;
  if (!sessionId) return;
  const intent = {
    operation,
    request_id: operation === 'start' ? sessionId : requestId(),
    session_id: sessionId,
  };
  if (operation === 'start') commissionUi.local = { session_id: sessionId, status: 'unknown' };
  commissionUi.error = '';
  rememberCommissioning(intent);
  commissionUi.posting = true;
  render();
  try {
    const payload =
      operation === 'start'
        ? { request_id: intent.request_id, seconds: 120 }
        : { request_id: intent.request_id, session_id: sessionId };
    const result = await api(operation === 'start' ? '/api/commission' : '/api/commission_cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Livolo-Token': snapshot.csrf_token },
      body: JSON.stringify(payload),
    });
    acceptCommissioningResult(intent, result);
  } catch {
    commissionUi.error =
      'The request outcome is not confirmed. Checking the same request; cancel to close this attempt safely.';
  } finally {
    commissionUi.posting = false;
    await refresh();
    render();
  }
}

/** Start one deliberate bounded session, or reopen a currently running/uncertain session. */
$('#pair-new-device').addEventListener('click', async () => {
  openDialog('#pair-session-dialog');
  if (!commissionUi.storageKey) await reconcileCommissioning();
  if (commissioningActive(displayedCommissioning()) || commissionUi.pending) {
    renderCommissioning();
    return;
  }
  await submitCommissioning('start');
});
/** Reopen authoritative session results without starting a new pairing window. */
$('#pair-session-view').addEventListener('click', () => {
  openDialog('#pair-session-dialog');
  renderCommissioning();
});
/** Cancel only the displayed or unresolved target, leaving other sessions untouched. */
$('#pair-session-cancel').addEventListener('click', () => submitCommissioning('cancel'));
/** Reuse normal naming and targeted pairing controls after the automatic session finishes. */
$('#pair-session-name').addEventListener('click', () => {
  const session = displayedCommissioning();
  const device = deviceById(session?.device);
  if (!device || commissioningActive(session) || commissionUi.pending) return;
  selectedId = device.id;
  $('#device-name').value = device.name;
  showError('#device-error', '');
  $('#pair-session-dialog').close();
  renderDeviceDetails();
  openDialog('#device-dialog');
});

/**
 * Show recent command outcomes from the browser and daemon activity history.
 */
function renderActivity() {
  const results = [...localResults.values()]
    .reverse()
    .sort((a, b) => (b.updated || 0) - (a.updated || 0))
    .slice(0, 50);
  const labels = {
    add: 'Add device',
    rename: 'Rename device',
    remove: 'Forget device',
    set: 'Set output',
    pair: 'Pair device',
    commission: 'Pair new device',
    commission_cancel: 'Cancel pairing session',
    snapshot: 'Refresh state',
    firmware_update: 'Firmware update',
  };
  $('#activity-list').innerHTML = results.length
    ? results
        .map(
          (r) =>
            `<div class="activity-row"><div><strong>${escapeHTML(labels[r.action] || 'Device request')} · ${escapeHTML(deviceName(r.device))}</strong><p>${escapeHTML(r.error || (r.status === 'confirmed' ? 'Confirmed by the daemon.' : r.status === 'pairing' ? 'Waiting for a device report.' : 'Status: ' + r.status))}</p><small>${escapeHTML(r.request_id)}</small></div><span class="badge ${r.status === 'confirmed' ? 'online' : ''}">${escapeHTML(r.status)}</span></div>`,
        )
        .join('')
    : '<div class="empty"><div class="empty-icon">≋</div><h2>Nothing to report yet</h2><p>Pairing, name changes, and output commands will appear here.</p></div>';
}
/**
 * Show gateway connectivity and diagnostics without closing expanded details.
 * Keep software identity distinct from radio targets; the legacy host version is not software.
 */
function renderNetwork() {
  const bridge = snapshot.bridge || snapshot;
  const diagnostics = snapshot.diagnostics || {};
  const rows = [
    ['Gateway ID', snapshot.gateway],
    ['Network tag', snapshot.tag],
    [
      'Daemon software',
      diagnostics.daemon_version ||
        (diagnostics.host_version ? 'Unknown (legacy daemon)' : 'Not reported'),
    ],
    [
      'Installed radio firmware',
      snapshot.firmware_update?.installed_version ||
        diagnostics.firmware?.running_version ||
        'Unknown',
    ],
    [
      'Radio compatibility target',
      diagnostics.firmware_compat_version || diagnostics.firmware?.target_version || 'Not reported',
    ],
    ['Native API revision', diagnostics.api_version ?? 'Not reported'],
    ['UART protocol revision', diagnostics.uart_protocol ?? 'Not reported'],
    [
      'Radio',
      !connected
        ? 'Unknown'
        : firmwareBusy()
          ? 'Maintenance'
          : snapshot.ready
            ? 'Ready'
            : 'Reconnecting',
    ],
    [
      'MQTT',
      !connected
        ? 'Unknown'
        : snapshot.mqtt?.enabled === false || snapshot.mqtt === null
          ? 'Disabled'
          : bridge.mqtt_connected === true
            ? 'Connected'
            : bridge.mqtt_connected === false
              ? 'Disconnected'
              : 'Not reported',
    ],
    ['Devices', `${snapshot.devices.length} / 256`],
    ['Pairing', snapshot.pairing ? deviceName(snapshot.pairing.device) : 'Closed'],
    ['Radio channel', '11'],
    ['Mode', snapshot.simulated ? 'Simulation' : 'Live gateway'],
  ];
  const diagnosticsOpen = Boolean($('#network-details details')?.open);
  $('#network-details').innerHTML =
    `<dl>${rows.map(([label, value]) => `<dt>${label}</dt><dd>${escapeHTML(value)}</dd>`).join('')}</dl><p>Device names and membership are stored on this gateway. The radio’s device table is restored by the daemon after a restart.</p><details><summary>Radio diagnostics</summary><pre>${escapeHTML(JSON.stringify(snapshot.diagnostics || {}, null, 2))}</pre></details>`;
  $('#network-details details').open = diagnosticsOpen;
  renderMqtt();
  renderFirmware();
}
/**
 * Refresh the selected device dialog and disable unavailable management actions.
 */
function renderDeviceDetails() {
  const d = deviceById(selectedId);
  if (!d) {
    $('#device-dialog').close();
    return;
  }
  $('#device-dialog-title').textContent = d.name;
  const rows = [
    ['Device ID', d.id],
    ['Status', !connected ? 'Unknown' : d.availability === 'online' ? 'Online' : 'Offline'],
    ['Outputs', d.outputs],
    ['Device suffix', d.suffix],
    ['Last report', relativeTime(d.state?.last_seen)],
    ['Signal', Number.isFinite(d.state?.rssi) ? d.state.rssi + ' dBm' : 'Not reported'],
  ];
  $('#device-details').innerHTML = rows
    .map(([key, value]) => `<div><span>${key}</span><strong>${escapeHTML(value)}</strong></div>`)
    .join('');
  const busy =
    $('#rename-form').dataset.busy === 'true' ||
    firmwareBusy() ||
    commissioningActive(snapshot?.commissioning);
  $('#pair-device').disabled =
    busy ||
    !connected ||
    !snapshot.ready ||
    Boolean(snapshot.pairing) ||
    commissioningActive(snapshot.commissioning);
  $('#forget-device').disabled = busy || !connected || busyDevices.has(d.id);
}
/** Return whether the user explicitly selected experimental device setup. */
function autoSetup() {
  return $('#add-method').value === 'auto';
}
/**
 * Check whether a discovery contains a currently usable experimental profile.
 * @param {Object|undefined} candidate - Unregistered discovery from the daemon.
 * @returns {boolean} Whether the profile can be reviewed and submitted now.
 */
function detectedProfile(candidate) {
  const profile = candidate?.profile;
  return Boolean(
    candidate &&
      !candidate.configured &&
      !deviceById(candidate.device) &&
      profile?.experimental === true &&
      profile.status === 'detected' &&
      typeof profile.token === 'string' &&
      profile.token.length > 0 &&
      ((profile.outputs === 1 && profile.suffix === '0001') ||
        (profile.outputs === 2 && profile.suffix === '0002')) &&
      Number.isFinite(profile.expires_at) &&
      profile.expires_at * 1000 > Date.now(),
  );
}
/**
 * Explain why an experimental candidate can or cannot be selected.
 * @param {Object} candidate - Nearby pairing announcement and optional profile.
 * @returns {string} Plain text suitable for an escaped candidate label.
 */
function detectionLabel(candidate) {
  if (detectedProfile(candidate)) return `${candidate.profile.outputs}-gang switch · Select`;
  const status = candidate.profile?.status;
  if (status === 'ambiguous') return 'Conflicting reports · Use manual setup';
  if (status === 'stale' || status === 'detected')
    return 'Expired · Put switch in pairing mode again';
  if (!candidate.profile) return 'Auto-detection unavailable · Use manual setup';
  return 'Unrecognized switch · Use manual setup';
}
/**
 * Match the current profile to the exact output count and suffix the user reviewed.
 * @returns {boolean} Whether the selected candidate remains safe to submit.
 */
function reviewedDetection() {
  const selected = addUi.selection;
  const current = snapshot?.discoveries.find((c) => c.device === selected?.device);
  return Boolean(
    selected &&
      detectedProfile(current) &&
      current.profile.token === selected.token &&
      current.profile.outputs === selected.outputs &&
      current.profile.suffix === selected.suffix,
  );
}
/** Keep manual fields editable and experimental fields bound to the reviewed discovery. */
function renderAddSetup() {
  const auto = autoSetup();
  const blocked =
    !connected ||
    firmwareBusy() ||
    commissioningActive(snapshot?.commissioning) ||
    $('#add-form').dataset.busy === 'true';
  $('#add-dialog').classList.toggle('auto-setup', auto);
  $('#device-fields').hidden = auto;
  $('#auto-help').hidden = !auto;
  $('#auto-review').hidden = !auto;
  $('#device-advanced').hidden = auto;
  $('#new-id').readOnly = auto;
  $('#new-suffix').readOnly = auto;
  $('#new-outputs').disabled = blocked || auto;
  $('#add-method').disabled = blocked;
  for (const id of ['#add-save', '#add-pair']) {
    $(id).disabled = blocked || (auto && (!snapshot?.ready || !reviewedDetection()));
  }
  if (!auto) return;
  const selected = addUi.selection;
  $('#new-id').value = selected?.device || '';
  $('#new-outputs').value = selected ? String(selected.outputs) : '';
  $('#new-suffix').value = selected?.suffix || '';
  $('#auto-review').textContent = !snapshot?.ready
    ? 'Waiting for the radio to reconnect. Detection requires a live gateway.'
    : !selected
      ? 'Select a detected switch below. Check that its ID and output count match your switch.'
      : reviewedDetection()
        ? `Review: ${selected.device} · ${selected.outputs}-gang switch · suffix ${selected.suffix}.`
        : 'This detection expired or changed. Put the switch in pairing mode again and reselect it, or use manual setup.';
}
/** Offer nearby unregistered pairing candidates without automatically selecting any device. */
function renderDiscoveries() {
  const auto = autoSetup();
  const candidates = snapshot.discoveries.filter((c) => !c.configured && !deviceById(c.device));
  const blocked =
    !connected ||
    firmwareBusy() ||
    commissioningActive(snapshot?.commissioning) ||
    $('#add-form').dataset.busy === 'true';
  const markup = candidates.length
    ? '<strong>Nearby devices requesting pairing</strong>' +
      candidates
        .map((c) => {
          const unavailable = blocked || (auto && (!snapshot.ready || !detectedProfile(c)));
          const label = auto
            ? detectionLabel(c)
            : `${Number.isFinite(c.rssi) ? c.rssi + ' dBm' : 'Detected'} · Select`;
          return `<button type="button" class="candidate" data-candidate="${escapeHTML(c.device)}"${unavailable ? ' disabled' : ''}${auto ? ` aria-pressed="${addUi.selection?.device === c.device}"` : ''}><span>${escapeHTML(c.device)}</span><span>${escapeHTML(label)}</span></button>`;
        })
        .join('')
    : auto
      ? 'Listening for an unpaired switch. Follow its documented pairing procedure to make it discoverable.'
      : 'No unregistered switch detected yet. You can enter its ID below.';
  if ($('#discoveries').innerHTML !== markup) $('#discoveries').innerHTML = markup;
  renderAddSetup();
}
/**
 * Build a manual request or an opt-in request bound to the reviewed detected profile.
 * @returns {Object} Add-device fields for shared command submission.
 * @throws {Error} If experimental evidence is missing, expired, or changed.
 */
function addDeviceFields() {
  const name = $('#new-name').value.trim();
  if (!name) throw new Error('Enter a friendly name.');
  if (autoSetup()) {
    if (!connected || !snapshot?.ready || !reviewedDetection()) {
      throw new Error('Select a fresh detected switch before saving, or use manual setup.');
    }
    const selected = addUi.selection;
    return {
      device: selected.device,
      name,
      auto_detect: true,
      profile_token: selected.token,
    };
  }
  return {
    device: $('#new-id').value.trim().toLowerCase(),
    name,
    outputs: Number($('#new-outputs').value),
    suffix: $('#new-suffix').value.trim().toLowerCase(),
  };
}
/**
 * Select the hash-addressed view and synchronize accessible navigation state.
 */
function changeView() {
  let view = location.hash.slice(1);
  if (!['devices', 'activity', 'network'].includes(view)) view = 'devices';
  $$('.view').forEach((el) => (el.hidden = el.id !== 'view-' + view));
  $$('.nav-link').forEach((el) => {
    const active = el.dataset.view === view;
    el.classList.toggle('active', active);
    if (active) el.setAttribute('aria-current', 'page');
    else el.removeAttribute('aria-current');
  });
  $('#page-label').textContent = view[0].toUpperCase() + view.slice(1);
  document.title = 'Livolo · ' + $('#page-label').textContent;
}
/**
 * Keep a form busy during an operation and display any resulting error locally.
 *
 * @param {HTMLFormElement} form - Form whose controls belong to the operation.
 * @param {string} errorId - CSS selector for its error container.
 * @param {function(): Promise<*>} work - Asynchronous operation to execute.
 * @returns {Promise<void>} Completion after restoring form availability.
 */
async function formAction(form, errorId, work) {
  form.dataset.busy = 'true';
  showError(errorId, '');
  const controls = $$('input,select,button', form);
  controls.forEach((el) => (el.disabled = true));
  try {
    await work();
  } catch (error) {
    showError(errorId, error.message);
  } finally {
    delete form.dataset.busy;
    controls.forEach((el) => (el.disabled = false));
    render();
  }
}
/**
 * Open a two-minute device pairing window and explain the required physical action.
 *
 * @param {string} device - Configured device identity to pair.
 * @returns {Promise<void>} Completion after the daemon accepts pairing.
 */
async function startPairing(device) {
  await command('pair', { device, seconds: 120 });
  toast(
    `Pairing window opened for ${deviceName(device)}. Follow the switch’s documented pairing procedure.`,
  );
}
/**
 * Dismiss the containing dialog when its close control is activated.
 *
 * @param {HTMLButtonElement} button - Close control bound to its containing dialog.
 */
$$('.close-dialog').forEach((button) =>
  button.addEventListener('click', () => button.closest('dialog').close()),
);
/** Keep the visible view synchronized with browser navigation. */
window.addEventListener('hashchange', changeView);
changeView();
/**
 * Apply the chosen availability filter and update its accessible selection state.
 *
 * @param {HTMLButtonElement} button - Control selecting an availability filter.
 */
$$('[data-filter]').forEach((button) =>
  button.addEventListener('click', () => {
    filter = button.dataset.filter;
    $$('[data-filter]').forEach((b) => {
      b.classList.toggle('active', b === button);
      b.setAttribute('aria-pressed', String(b === button));
    });
    if (snapshot) renderDevices();
  }),
);
/**
 * Refresh matching device rows as the search text changes.
 */
$('#search').addEventListener('input', () => {
  if (snapshot) renderDevices();
});
/**
 * Start device registration with an empty form and current discovery candidates.
 */
$('#add-device').addEventListener('click', () => {
  $('#add-form').reset();
  addUi.selection = null;
  addUi.manual = null;
  $('#pairing-instructions').open = true;
  showError('#add-error', '');
  renderDiscoveries();
  openDialog('#add-dialog');
});
/** Preserve manual entries when switching into or out of experimental setup. */
$('#add-method').addEventListener('change', () => {
  $('#pairing-instructions').open = !autoSetup();
  if (autoSetup()) {
    addUi.manual = {
      device: $('#new-id').value,
      outputs: $('#new-outputs').value,
      suffix: $('#new-suffix').value,
    };
    addUi.selection = null;
  } else if (addUi.manual) {
    $('#new-id').value = addUi.manual.device;
    $('#new-outputs').value = addUi.manual.outputs;
    $('#new-suffix').value = addUi.manual.suffix;
  }
  showError('#add-error', '');
  renderDiscoveries();
});
/**
 * Fill identity fields or pin the experimental profile the user selected for review.
 * @param {MouseEvent} event - Click whose target may identify a discovery candidate.
 */
$('#discoveries').addEventListener('click', (event) => {
  const button = event.target.closest('[data-candidate]');
  if (!button || button.disabled) return;
  const candidate = snapshot.discoveries.find((c) => c.device === button.dataset.candidate);
  if (!candidate || candidate.configured || deviceById(candidate.device)) return;
  if (autoSetup()) {
    if (!detectedProfile(candidate)) return;
    addUi.selection = {
      device: candidate.device,
      outputs: candidate.profile.outputs,
      suffix: candidate.profile.suffix,
      token: candidate.profile.token,
    };
  } else {
    $('#new-id').value = candidate.device;
    $('#new-suffix').value = candidate.suffix || '0002';
  }
  showError('#add-error', '');
  renderDiscoveries();
  $('#new-name').focus();
});
/**
 * Open device management or request a confirmed output change from a device row.
 *
 * @param {MouseEvent} event - Click on a management or output control.
 */
$('#device-list').addEventListener('click', async (event) => {
  const manage = event.target.closest('[data-manage]');
  if (manage) {
    selectedId = manage.dataset.manage;
    $('#device-name').value = deviceById(selectedId).name;
    showError('#device-error', '');
    renderDeviceDetails();
    openDialog('#device-dialog');
    return;
  }
  const button = event.target.closest('[data-output]');
  if (!button || button.disabled) return;
  const id = button.dataset.device,
    output = Number(button.dataset.output),
    d = deviceById(id);
  const on =
    d.state?.outputs?.[String(output + 1)] === 'ON' || Boolean(d.state?.state_bits & (1 << output));
  busyDevices.add(id);
  renderDevices();
  try {
    await command('set', { device: id, mask: 1 << output, value: on ? 0 : 1 << output });
    toast(`${deviceName(id)} · Output ${output + 1} ${on ? 'off' : 'on'}, confirmed.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    busyDevices.delete(id);
    renderDevices();
  }
});
/**
 * Save a nonempty friendly name for the selected device.
 *
 * @param {SubmitEvent} event - Rename form submission to handle without navigation.
 */
$('#rename-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const name = $('#device-name').value.trim();
  if (!name) {
    showError('#device-error', 'Enter a friendly name.');
    return;
  }
  formAction(event.currentTarget, '#device-error', async () => {
    await command('rename', { device: selectedId, name });
    $('#device-dialog').close();
    toast('Device name saved.');
  });
});
/**
 * Open pairing for the selected device while its management form is busy.
 *
 * @param {MouseEvent} event - Unused pairing-button click.
 */
$('#pair-device').addEventListener('click', (event) =>
  formAction($('#rename-form'), '#device-error', async () => {
    await startPairing(selectedId);
    $('#device-dialog').close();
  }),
);
/**
 * Cancel the active pairing window and report the daemon outcome.
 */
$('#stop-pairing').addEventListener('click', async () => {
  const pair = snapshot.pairing;
  if (!pair) return;
  $('#stop-pairing').disabled = true;
  try {
    await command('pair', { device: pair.device, seconds: 0 });
    toast('Pairing window closed.');
  } catch (error) {
    toast(error.message, true);
  } finally {
    renderPairing();
  }
});
/**
 * Register a device and optionally pair it after radio reconciliation completes.
 *
 * @param {SubmitEvent} event - Submission identifying whether immediate pairing was requested.
 */
$('#add-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const pair = event.submitter?.value === 'pair';
  let fields;
  try {
    fields = addDeviceFields();
  } catch (error) {
    showError('#add-error', error.message);
    return;
  }
  return formAction(event.currentTarget, '#add-error', async () => {
    await command('add', fields);
    $('#add-dialog').close();
    toast(`${fields.name} saved.`);
    if (pair) {
      try {
        const deadline = Date.now() + 20000;
        while ((!connected || !snapshot.ready) && Date.now() < deadline) {
          await sleep(500);
          await refresh();
        }
        if (!connected || !snapshot.ready)
          throw new Error(
            'Device saved, but the radio is not ready. Open the device and choose Pair device once the gateway reconnects.',
          );
        await startPairing(fields.device);
      } catch (error) {
        toast(error.message, true);
      }
    }
  });
});
/**
 * Ask for confirmation before removing the selected device and its saved state.
 */
$('#forget-device').addEventListener('click', () => {
  $('#forget-description').textContent =
    `Remove “${deviceName(selectedId)}” and its saved state from this gateway?`;
  showError('#forget-error', '');
  openDialog('#forget-dialog');
});
/**
 * Remove the confirmed device and close its management dialogs after success.
 *
 * @param {SubmitEvent} event - Confirmed removal submission to handle without navigation.
 */
$('#forget-form').addEventListener('submit', (event) => {
  event.preventDefault();
  formAction(event.currentTarget, '#forget-error', async () => {
    const name = deviceName(selectedId);
    await command('remove', { device: selectedId });
    $('#forget-dialog').close();
    $('#device-dialog').close();
    toast(`${name} removed from this gateway.`);
  });
});
/**
 * Keep authentication visible until a usable gateway connection is established.
 *
 * @param {Event} event - Dialog cancellation attempt to prevent.
 */
$('#login-dialog').addEventListener('cancel', (event) => event.preventDefault());
/**
 * Try the supplied access token and close authentication only after a successful refresh.
 *
 * @param {SubmitEvent} event - Token form submission to handle without navigation.
 */
$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  showError('#login-error', '');
  authToken = $('#access-token').value;
  const button = $('button', event.currentTarget);
  button.disabled = true;
  try {
    if (await refresh()) {
      $('#login-dialog').close();
      $('#access-token').value = '';
    } else {
      showError(
        '#login-error',
        'Could not connect. Check the access token and gateway connection.',
      );
    }
  } finally {
    button.disabled = false;
  }
});
/**
 * Schedule the next snapshot refresh after the preceding refresh finishes.
 *
 * @returns {Promise<void>} Completion after scheduling the next refresh.
 */
(async function poll() {
  await refresh();
  setTimeout(poll, 2000);
})();

let mqttInitialized = false;
/**
 * Reset editable broker fields to the latest public settings without exposing credentials.
 */
function fillMqtt() {
  const settings = snapshot?.mqtt;
  if (!settings || settings.enabled === false) return;
  $('#mqtt-host').value = settings.host;
  $('#mqtt-port').value = settings.port;
  $('#mqtt-topic').value = settings.base_topic;
  $('#mqtt-username').value = settings.username;
  $('#mqtt-password-action').value = 'keep';
  $('#mqtt-password').value = '';
  updatePasswordField();
  mqttInitialized = true;
}
/**
 * Require a new password only when replacement is explicitly selected.
 */
function updatePasswordField() {
  const replacing = $('#mqtt-password-action').value === 'replace';
  $('#mqtt-password-field').hidden = !replacing;
  $('#mqtt-password').required = replacing;
  if (!replacing) $('#mqtt-password').value = '';
}
/**
 * Show broker health and control whether its settings may be edited.
 */
function renderMqtt() {
  const settings = snapshot.mqtt;
  const disabled = settings === null || settings?.enabled === false;
  $('#mqtt-disabled').hidden = !disabled;
  $('#mqtt-intro').hidden = disabled;
  $('#mqtt-form').hidden = disabled;
  if (disabled) {
    $('#mqtt-status').textContent = 'Disabled';
    $('#mqtt-status').className = 'badge';
    $('#mqtt-password').value = '';
    mqttInitialized = false;
    return;
  }
  if (!mqttInitialized) fillMqtt();
  const online = connected && settings?.connected;
  $('#mqtt-status').textContent = !connected ? 'Unknown' : online ? 'Connected' : 'Disconnected';
  $('#mqtt-status').className = 'badge' + (online ? ' online' : '');
  $('#mqtt-password-note').textContent = settings?.password_set
    ? 'A password is configured. Its value is never sent back to this page.'
    : 'No password is configured.';
  $('#mqtt-transport').textContent = settings?.tls
    ? 'TLS is enabled in the gateway configuration.'
    : 'Transport: plain MQTT. TLS certificates can be configured on the gateway.';
  $('#mqtt-topic-help').textContent =
    'Example state topic: ' + (settings?.base_topic || 'livolo') + '/<device-id>/state';
  const busy =
    $('#mqtt-form').dataset.busy === 'true' ||
    firmwareBusy() ||
    commissioningActive(snapshot?.commissioning);
  $$('input,select,button', $('#mqtt-form')).forEach(
    (el) => (el.disabled = busy || !connected || !settings?.editable),
  );
  if (!busy && settings?.error)
    $('#mqtt-feedback').textContent = settings.error + ' The gateway will keep retrying.';
  else if (!busy && online && $('#mqtt-feedback').dataset.saved)
    $('#mqtt-feedback').textContent = 'Settings saved. Connected to the broker.';
  if (settings && !settings.editable)
    $('#mqtt-feedback').textContent = snapshot.simulated
      ? 'MQTT configuration is unavailable in this demo.'
      : 'MQTT settings are managed by the gateway configuration.';
}
/** Keep password entry and validation aligned with the selected password action. */
$('#mqtt-password-action').addEventListener('change', updatePasswordField);
/**
 * Discard unsaved broker edits and clear the settings feedback.
 */
$('#mqtt-reset').addEventListener('click', () => {
  fillMqtt();
  showError('#mqtt-error', '');
  $('#mqtt-feedback').textContent = '';
  delete $('#mqtt-feedback').dataset.saved;
});
/**
 * Save editable broker settings with an explicit password action and refresh connection state.
 *
 * @param {SubmitEvent} event - Broker form submission to handle without navigation.
 */
$('#mqtt-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if (firmwareBusy()) {
    showError('#mqtt-error', 'Settings changes are paused for firmware maintenance.');
    return;
  }
  if (commissioningActive(snapshot?.commissioning)) {
    showError('#mqtt-error', 'Settings changes are paused while pairing a new switch.');
    return;
  }
  const fields = {
    host: $('#mqtt-host').value.trim(),
    port: Number($('#mqtt-port').value),
    base_topic: $('#mqtt-topic').value.trim(),
    username: $('#mqtt-username').value.trim(),
    password_action: $('#mqtt-password-action').value,
  };
  if (fields.password_action === 'replace') fields.password = $('#mqtt-password').value;
  formAction(event.currentTarget, '#mqtt-error', async () => {
    await api('/api/settings/mqtt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Livolo-Token': snapshot.csrf_token },
      body: JSON.stringify(fields),
    });
    $('#mqtt-password').value = '';
    $('#mqtt-password-action').value = 'keep';
    updatePasswordField();
    $('#mqtt-feedback').dataset.saved = 'true';
    $('#mqtt-feedback').textContent = 'Settings saved. Connecting to the broker…';
    mqttInitialized = false;
    await refresh();
  });
});

/**
 * Keep conflicting device changes disabled while maintenance owns or may own the radio.
 *
 * @returns {boolean} Whether firmware work is active or its submission is unresolved.
 */
function firmwareBusy() {
  return Boolean(
    firmwareUi.submitting ||
      firmwareUi.request ||
      snapshot?.firmware_update?.in_progress ||
      snapshot?.firmware_update?.recovery ||
      snapshot?.firmware_update?.status === 'pending',
  );
}

/**
 * Translate public firmware reason codes without exposing private backend details.
 * @param {string} code - Sanitized daemon or preflight reason.
 * @returns {string} User-facing explanation.
 */
function firmwareReason(code) {
  const reasons = {
    board_identity_unknown: 'The gateway has not identified the connected radio board.',
    bundle_missing: 'No firmware bundle is installed on this gateway.',
    bundle_invalid: 'The installed firmware bundle did not pass validation.',
    artifact_missing: 'No firmware payload is installed for this exact board.',
    artifact_invalid: 'The firmware payload did not pass integrity or signature checks.',
    artifact_unqualified: 'This firmware has not been qualified for installation.',
    incompatible_host: 'The firmware bundle does not match the radio compatibility target.',
    incompatible_baudrate: 'The firmware does not support the configured serial rate.',
    unsupported_board: 'Firmware installation is not supported for this board.',
    backend_unavailable: 'The required firmware installation backend is unavailable.',
    bootloader_install_required: 'This radio needs its one-time bootloader installation first.',
    signing_key_mismatch: 'The firmware signing key does not match the trusted key.',
    radio_offline: 'Connect the radio and wait for it to become ready.',
    simulation: 'Firmware installation is disabled in simulation mode.',
    radio_busy: 'The radio is busy. Wait for current device activity to finish.',
    live_identity_required: 'A connected, identified radio is required.',
    board_mismatch: 'The firmware is for a different radio board.',
    board_identity_changed: 'The radio identity changed. Reconnect and check again.',
    incompatible_protocol: 'The firmware and radio protocol versions do not match.',
    version_not_newer: 'The candidate is not newer than the installed firmware.',
    candidate_stale: 'The firmware candidate changed. Review the current update.',
    recovery_required: 'Firmware recovery is required before another installation.',
    update_in_progress: 'A firmware update is already in progress.',
  };

  return reasons[code] || String(code || 'Check unavailable').replaceAll('_', ' ');
}

/**
 * Explain why the daemon's currently advertised artifact cannot be installed.
 *
 * @returns {string} Blocking reason, or empty text for an eligible candidate.
 */
function firmwareBlockedReason() {
  const firmware = snapshot?.firmware_update;
  if (!connected) return 'Reconnect to the gateway to check firmware availability.';
  if (!firmware) return 'This daemon does not provide firmware installation through the web UI.';
  if (firmware.recovery) return 'Firmware recovery is required before device changes can resume.';
  if (firmwareBusy()) return 'An update is in progress or its outcome is being checked.';
  if (commissioningActive(snapshot?.commissioning))
    return 'Finish or cancel pairing before updating firmware.';
  if (firmware.preflight_supported !== true)
    return 'Update the daemon to enable firmware preflight and installation.';
  if (firmware.blocked_reason) {
    return firmwareReason(firmware.blocked_reason);
  }
  if ((firmware.status || firmwareUi.result?.status) === 'unknown')
    return 'The previous update needs recovery before another install.';
  if (firmware.install_supported !== true)
    return 'Firmware installation is unavailable for this radio.';
  if (!firmware.candidate_id || !firmware.latest_version || !firmware.board)
    return 'No verified firmware candidate is available for this radio.';
  if (firmware.update_available !== true)
    return firmware.installed_version === firmware.latest_version
      ? 'Your gateway firmware is up to date.'
      : 'No newer verified firmware update is available for this radio.';
  return '';
}

/**
 * Remember only the request identity so a reload can resolve an uncertain submission.
 *
 * @param {string|null} id - Durable request ID, or null after a definitive outcome.
 */
function rememberFirmwareRequest(id) {
  firmwareUi.request = id;
  try {
    if (id) sessionStorage.setItem(firmwareUi.storageKey, id);
    else sessionStorage.removeItem(firmwareUi.storageKey);
  } catch {
    // Private browsing can deny storage; the daemon snapshot still owns job state.
  }
}

/**
 * Apply an outcome only to the request this browser submitted, without replaying it.
 *
 * @param {Object} result - Durable result or matching firmware snapshot summary.
 */
function acceptFirmwareResult(result) {
  if (!result || result.request_id !== firmwareUi.request) return;
  if (!['pending', 'confirmed', 'rejected', 'unknown'].includes(result.status)) return;
  firmwareUi.result = result;
  firmwareUi.error = result.error || '';
  if (result.status !== 'pending') rememberFirmwareRequest(null);
  if (result.status === 'unknown')
    firmwareUi.error ||=
      'The update outcome is unknown. Follow the recovery instructions before retrying.';
}

/**
 * Recover the same durable request after a lost POST response or page reload.
 *
 * @returns {Promise<void>} Completion of read-only reconciliation; never submits firmware.
 */
async function reconcileFirmwareRequest() {
  const key = 'livolo-firmware:' + apiUrl('/') + ':' + snapshot.gateway + ':' + snapshot.tag;
  if (firmwareUi.storageKey !== key) {
    firmwareUi.storageKey = key;
    firmwareUi.request = null;
    firmwareUi.result = null;
    firmwareUi.error = '';
    try {
      const saved = sessionStorage.getItem(key);
      if (saved && /^[A-Za-z0-9_.:-]{1,128}$/.test(saved)) firmwareUi.request = saved;
    } catch {
      // Missing storage cannot authorize a second submission; consult the snapshot.
    }
  }
  if (!firmwareUi.request || firmwareUi.submitting) return;
  const summary = snapshot.firmware_update;
  if (summary?.request_id === firmwareUi.request) {
    acceptFirmwareResult(summary);
    if (summary.status === 'pending' && summary.in_progress) return;
  }
  if (!firmwareUi.request) return;
  const stored = snapshot.results.find((result) => result.request_id === firmwareUi.request);
  if (stored) acceptFirmwareResult(stored);
  if (!firmwareUi.request) return;
  try {
    const result = await api('/api/commands/' + encodeURIComponent(firmwareUi.request));
    if (result?.request_id !== firmwareUi.request) throw new Error('Firmware request ID mismatch.');
    acceptFirmwareResult(result);
  } catch {
    firmwareUi.error =
      'The update request could not be resolved. Its status will be checked again; it will not be submitted again.';
  }
}

/**
 * Render an artifact's exact identity as escaped labels shared by the card and review.
 *
 * @param {Object} firmware - Public firmware status or the pinned review candidate.
 * @returns {string} Definition-list contents with no executable release content.
 */
function firmwareDetails(firmware) {
  return [
    ['Board', firmware.board || 'Unknown'],
    ['Installed radio firmware', firmware.installed_version || 'Unknown'],
    ['Candidate radio firmware', firmware.latest_version || 'No verified candidate'],
    ['Artifact', firmware.candidate_id || 'None'],
  ]
    .map(([label, value]) => `<dt>${label}</dt><dd>${escapeHTML(value)}</dd>`)
    .join('');
}

/**
 * Keep confirmation pinned to the artifact and board the user actually reviewed.
 *
 * @returns {boolean} Whether the current candidate still matches the open review.
 */
function firmwareReviewMatches() {
  const firmware = snapshot?.firmware_update;
  return Boolean(
    firmwareUi.review &&
      firmware &&
      ['board', 'candidate_id', 'latest_version', 'installed_version', 'rollback_supported'].every(
        (key) => firmware[key] === firmwareUi.review[key],
      ),
  );
}

/** Return the current gateway and candidate facts that a preflight review depends on. */
function firmwarePreflightKey() {
  const firmware = snapshot?.firmware_update || {};
  return JSON.stringify([
    connected,
    snapshot?.ready,
    snapshot?.gateway,
    snapshot?.tag,
    snapshot?.session,
    snapshot?.commissioning?.session_id,
    snapshot?.commissioning?.status,
    ...[
      'board',
      'candidate_id',
      'latest_version',
      'installed_version',
      'rollback_supported',
      'preflight_supported',
      'install_supported',
      'update_available',
      'blocked_reason',
      'in_progress',
      'status',
      'recovery',
    ].map((key) => firmware[key]),
  ]);
}

/**
 * Require an explicit positive result and no failed or missing individual checks.
 * @param {Object|null} report - Validated advisory response, if available.
 * @returns {boolean} Whether the report has completely passed.
 */
function firmwareReportPassed(report) {
  const required = [
    'artifact',
    'expected_board',
    'helper',
    'live_identity',
    'board',
    'protocol',
    'baudrate',
    'version',
  ];
  const identities = new Set(report?.checks.map((item) => item.id));
  return Boolean(
    report &&
      report.executable === true &&
      report.compatible === true &&
      report.blocked_reason === null &&
      report.requires_manual_action === false &&
      report.checks.length <= 32 &&
      identities.size === report.checks.length &&
      required.every((id) => identities.has(id)) &&
      report.checks.every((item) => item.status === 'pass'),
  );
}

/**
 * Compare an advisory report with the board and artifact presented to the user.
 * @param {Object} report - Validated schema-one preflight response.
 * @param {Object|undefined} firmware - Snapshot or pinned artifact review.
 * @returns {boolean} Whether all installation identity facts match.
 */
function firmwareReportMatches(report, firmware) {
  return Boolean(
    firmware &&
      report.candidate_id === firmware.candidate_id &&
      report.board === firmware.board &&
      report.target_version === firmware.latest_version &&
      report.installed_version === firmware.installed_version &&
      report.rollback_supported === firmware.rollback_supported &&
      report.identity_source === 'live_session',
  );
}

/**
 * Keep installation blocked until a successful preflight describes the reviewed artifact.
 * @returns {string} Blocking explanation, or empty text after a complete matching check.
 */
function firmwarePreflightReason() {
  const check = firmwareUi.preflight;
  const report = check.report;
  if (check.checking) return 'Preflight is checking the firmware and gateway.';
  if (check.error) return check.error;
  if (!report || check.key !== firmwarePreflightKey()) return 'Run preflight before installing.';
  if (!firmwareReportPassed(report))
    return report.blocked_reason
      ? firmwareReason(report.blocked_reason)
      : 'Preflight has failed or incomplete checks.';
  if (!firmwareReportMatches(report, firmwareUi.review))
    return 'Preflight does not match this gateway and reviewed firmware. Check again.';
  return '';
}

/**
 * Run an advisory GET only; superseded responses cannot authorize an installation.
 * @returns {Promise<void>} Completion after displaying a report or a retryable read error.
 */
async function runFirmwarePreflight() {
  if (!connected || snapshot?.firmware_update?.preflight_supported !== true) return;
  const check = firmwareUi.preflight;
  const sequence = ++check.sequence;
  check.key = firmwarePreflightKey();
  check.checking = true;
  check.report = null;
  check.error = '';
  const candidate = snapshot.firmware_update.candidate_id;
  renderFirmware();
  try {
    const report = await api(
      '/api/firmware/preflight' +
        (candidate ? '?candidate_id=' + encodeURIComponent(candidate) : ''),
    );
    if (sequence !== check.sequence) return;
    if (
      !report ||
      report.schema !== 1 ||
      report.operation !== 'update' ||
      !Array.isArray(report.checks) ||
      report.checks.some(
        (item) =>
          !item ||
          typeof item.id !== 'string' ||
          typeof item.code !== 'string' ||
          !['pass', 'fail', 'unknown'].includes(item.status),
      )
    )
      throw new Error('Unsupported preflight report.');
    check.report = report;
  } catch {
    if (sequence !== check.sequence) return;
    check.error =
      'Preflight could not be completed. Reconnect and try again; older daemons may need an update.';
  } finally {
    if (sequence === check.sequence) {
      check.checking = false;
      renderFirmware();
    }
  }
}

/** Show escaped advisory checks in both the network card and the installation review. */
function renderFirmwarePreflight() {
  const check = firmwareUi.preflight;
  if (check.key && check.key !== firmwarePreflightKey()) {
    check.sequence++;
    check.key = null;
    check.report = null;
    check.checking = false;
    check.error = 'Gateway or firmware details changed. Run preflight again.';
  }
  const labels = {
    artifact: 'Firmware integrity',
    expected_board: 'Expected board',
    helper: 'Installation helper',
    live_identity: 'Connected radio',
    board: 'Board identity',
    protocol: 'Radio protocol',
    baudrate: 'Serial rate',
    version: 'Firmware version',
    bootloader: 'Bootloader',
    signing_key: 'Trusted signing key',
    previous_update: 'Previous update',
    lifecycle: 'Radio availability',
  };
  const statuses = { pass: 'Passed', fail: 'Failed', unknown: 'Unknown' };
  const report = check.report;
  const message = check.checking
    ? 'Running preflight…'
    : check.error ||
      (report
        ? firmwareReportPassed(report)
          ? firmwareReportMatches(report, snapshot?.firmware_update)
            ? 'Preflight passed. Installation will recheck the radio and firmware.'
            : 'Preflight does not match the current radio and firmware. Check again.'
          : 'Preflight did not pass. Installation is unavailable.'
        : 'Run preflight to check this firmware before installing.');
  const markup =
    `<strong>${escapeHTML(message)}</strong><p class="muted">Read-only check · No radio commands, flashing or device changes.</p>` +
    (report
      ? '<ul class="preflight-checks">' +
        report.checks
          .map(
            (item) =>
              `<li><span>${escapeHTML(labels[item.id] || item.id.replaceAll('_', ' '))}</span><span class="check-${item.status}">${statuses[item.status]}${item.status !== 'pass' ? ' · ' + escapeHTML(firmwareReason(item.code)) : ''}</span></li>`,
          )
          .join('') +
        '</ul>' +
        (report.blocked_reason && !report.checks.some((item) => item.code === report.blocked_reason)
          ? `<p>${escapeHTML(firmwareReason(report.blocked_reason))}</p>`
          : '') +
        (report.requires_manual_action
          ? '<p>Manual preparation or recovery is required before this update can run.</p>'
          : '')
      : '');
  $('#firmware-preflight-report').innerHTML = markup;
  $('#firmware-preflight-report').hidden = !check.checking && !report && !check.error;
  $('#firmware-review-preflight').innerHTML = markup;
  $('#firmware-preflight').disabled =
    !connected || snapshot?.firmware_update?.preflight_supported !== true || check.checking;
  $('#firmware-recheck').disabled =
    !connected ||
    snapshot?.firmware_update?.preflight_supported !== true ||
    check.checking ||
    !firmwareReviewMatches();
}

/** Run diagnostics without starting or reserving an installation job. */
$('#firmware-preflight').addEventListener('click', runFirmwarePreflight);
/** Refresh the advisory report for the exact artifact still open for review. */
$('#firmware-recheck').addEventListener('click', () => {
  if (firmwareReviewMatches()) return runFirmwarePreflight();
});

/**
 * Show daemon-owned eligibility, genuine progress and durable recovery information.
 */
function renderFirmware() {
  renderFirmwarePreflight();
  const firmware = snapshot?.firmware_update || {};
  const local = firmwareUi.result;
  const status = firmware.status || local?.status;
  const busy = firmwareBusy();
  const checked = firmwareUi.preflight.report;
  const preflightBlocked =
    checked && (!firmwareReportPassed(checked) || !firmwareReportMatches(checked, firmware));
  $('#firmware-details').innerHTML = firmwareDetails({
    ...firmware,
    installed_version:
      firmware.installed_version || snapshot?.diagnostics?.firmware?.running_version,
  });
  $('#firmware-status').textContent = !connected
    ? 'Disconnected'
    : firmware.recovery || status === 'unknown'
      ? 'Needs recovery'
      : busy
        ? firmware.in_progress
          ? 'Updating'
          : 'Checking outcome'
        : status === 'confirmed'
          ? 'Verified'
          : status === 'unknown'
            ? 'Needs recovery'
            : status === 'rejected'
              ? 'Not installed'
              : preflightBlocked
                ? 'Preflight blocked'
                : firmwareBlockedReason()
                  ? 'Unavailable'
                  : 'Update available';
  $('#firmware-status').className =
    'badge' + (connected && !busy && status === 'confirmed' ? ' online' : '');
  $('#firmware-reason').textContent =
    firmwareBlockedReason() ||
    (preflightBlocked
      ? checked.blocked_reason
        ? firmwareReason(checked.blocked_reason)
        : 'Preflight did not pass for this radio and firmware.'
      : 'A verified update is ready for review.');
  $('#firmware-review').disabled = Boolean(firmwareBlockedReason());
  const request = firmware.request_id || firmwareUi.request || local?.request_id;
  $('#firmware-job').hidden = !request && !busy;
  const stages = {
    idle: 'Ready',
    preparing: 'Preparing the update',
    preflight: 'Checking the radio and firmware',
    disarm: 'Pausing radio control',
    begin: 'Starting firmware transfer',
    transfer: 'Transferring firmware',
    transferring: 'Transferring firmware',
    transferred: 'Firmware transfer finished; verification is still required',
    commit: 'Committing the firmware',
    committed: 'Firmware committed; verification is still required',
    activate: 'Activating the new firmware',
    bootloader_entry: 'Entering the radio bootloader',
    flash: 'Writing firmware',
    restoring: 'Reconnecting and verifying the radio',
    confirmed: 'Firmware installation verified',
    failed: 'Firmware installation failed',
    interrupted: 'Firmware installation was interrupted',
    restored_previous: 'Previous firmware restored; the update was not installed',
  };
  let stage =
    stages[firmware.stage] ||
    (firmware.stage ? String(firmware.stage).replaceAll('_', ' ') : status || 'Checking request');
  if (
    ['transfer', 'transferring'].includes(firmware.stage) &&
    Number.isFinite(firmware.transfer_percent) &&
    firmware.transfer_percent >= 0 &&
    firmware.transfer_percent <= 100
  )
    stage += ' · Transfer: ' + firmware.transfer_percent + '%';
  $('#firmware-stage').textContent = !connected
    ? 'Connection interrupted. Last reported stage: ' + stage
    : stage;
  $('#firmware-request').textContent = request ? 'Request: ' + request : '';
  const progress = $('#firmware-progress');
  progress.hidden = !firmware.in_progress;
  if (
    Number.isFinite(firmware.progress_percent) &&
    firmware.progress_percent >= 0 &&
    firmware.progress_percent <= 100
  ) {
    progress.value = firmware.progress_percent;
  } else progress.removeAttribute('value');
  showError('#firmware-error', firmwareUi.error || firmware.error || '');
  const recovery = firmware.recovery || local?.recovery;
  $('#firmware-recovery').textContent =
    recovery === 'recovery_required'
      ? 'The daemon could not verify a safe completion. Follow the emergency recovery procedure for this exact board before restarting device control.'
      : recovery || '';
  $('#firmware-recovery').hidden = !$('#firmware-recovery').textContent;
  $('#firmware-notes-text').textContent = firmware.release_notes || '';
  const link = $('#firmware-release-link');
  link.hidden = true;
  link.removeAttribute('href');
  if (typeof firmware.release_url === 'string') {
    try {
      const url = new URL(firmware.release_url);
      if (url.protocol === 'https:' && !url.username && !url.password) {
        link.href = url.href;
        link.hidden = false;
      }
    } catch {
      // Malformed release links must not become navigation targets.
    }
  }
  $('#firmware-notes').hidden = !firmware.release_notes && link.hidden;
  if ($('#firmware-dialog').open) {
    const reason = !firmwareReviewMatches()
      ? 'The firmware candidate changed. Close this dialog and review the current update.'
      : firmwareBlockedReason() || firmwarePreflightReason();
    $('#firmware-install').disabled = Boolean(reason);
    showError('#firmware-review-error', reason);
  }
}

/** Pin exact artifact details before asking for an explicit installation decision. */
$('#firmware-review').addEventListener('click', async () => {
  if (firmwareBlockedReason()) return;
  firmwareUi.review = { ...snapshot.firmware_update };
  $('#firmware-review-details').innerHTML = firmwareDetails(firmwareUi.review);
  $('#firmware-rollback').textContent = firmwareUi.review.rollback_supported
    ? 'This radio supports trial rollback. Installation is complete only after the daemon verifies it.'
    : 'This radio has no automatic rollback. If installation is interrupted, bootloader recovery may be required.';
  openDialog('#firmware-dialog');
  await runFirmwarePreflight();
});

/**
 * Submit the reviewed artifact once and leave completion tracking to durable status.
 *
 * @param {SubmitEvent} event - Explicit firmware-install confirmation.
 */
$('#firmware-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!firmwareReviewMatches() || firmwareBlockedReason() || firmwarePreflightReason()) {
    renderFirmware();
    return;
  }
  const payload = { request_id: requestId(), candidate_id: firmwareUi.review.candidate_id };
  rememberFirmwareRequest(payload.request_id);
  firmwareUi.submitting = true;
  firmwareUi.result = null;
  firmwareUi.error = '';
  $('#firmware-dialog').close();
  render();
  try {
    const result = await api('/api/firmware/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Livolo-Token': snapshot.csrf_token },
      body: JSON.stringify(payload),
    });
    if (result?.request_id !== payload.request_id) throw new Error('Firmware request ID mismatch.');
    acceptFirmwareResult(result);
    observeResult({ ...result, action: 'firmware_update' });
  } catch {
    firmwareUi.error =
      'The update submission could not be confirmed. Checking the same request; do not start another install.';
  } finally {
    firmwareUi.submitting = false;
    await refresh();
    render();
  }
});

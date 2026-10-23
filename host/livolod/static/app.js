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
const busyDevices = new Set();
const watched = new Map();
const localResults = new Map();
const finalErrors = new Set(['rejected', 'unknown', 'unconfirmed', 'busy', 'timeout']);
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
    const response = await fetch(path, {
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
  const connection = $('#connection');
  connection.className = 'connection' + (ready ? ' online' : connected ? '' : ' error');
  connection.innerHTML =
    '<span class="dot"></span>' +
    (ready ? 'Gateway connected' : connected ? 'Radio reconnecting' : 'Gateway disconnected');
  $('#refresh-label').textContent = connected ? 'Updates automatically' : 'Connection interrupted';
  $('#simulation').hidden = !snapshot?.simulated;
  $('#add-device').disabled = !connected || $('#add-form').dataset.busy === 'true';
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
        !connected ||
        !snapshot.ready ||
        !online ||
        busyDevices.has(d.id) ||
        snapshot.pairing?.device === d.id;
      const disabledReason = controlsDisabled
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
      ? 'Add your first Livolo switch to give it a name and connect it to your gateway.'
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
  $('#pairing-banner').hidden = !pair;
  if (!pair) return;
  const seconds = Math.max(0, pair.seconds_remaining || 0);
  $('#pairing-title').textContent =
    `Waiting for ${deviceName(pair.device)} · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} remaining`;
  $('#pairing-description').textContent =
    'Put this switch into pairing mode using its documented procedure. A device report confirms success.';
  $('#stop-pairing').disabled = !connected || !snapshot.ready;
}
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
    snapshot: 'Refresh state',
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
 */
function renderNetwork() {
  const bridge = snapshot.bridge || snapshot;
  const rows = [
    ['Gateway ID', snapshot.gateway],
    ['Network tag', snapshot.tag],
    ['Radio', !connected ? 'Unknown' : snapshot.ready ? 'Ready' : 'Reconnecting'],
    [
      'MQTT',
      !connected
        ? 'Unknown'
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
  const busy = $('#rename-form').dataset.busy === 'true';
  $('#pair-device').disabled = busy || !connected || !snapshot.ready || Boolean(snapshot.pairing);
  $('#forget-device').disabled = busy || !connected || busyDevices.has(d.id);
}
/**
 * Offer nearby unregistered pairing candidates in the add-device dialog.
 */
function renderDiscoveries() {
  const candidates = snapshot.discoveries.filter((c) => !deviceById(c.device));
  $('#discoveries').innerHTML = candidates.length
    ? '<strong>Nearby devices requesting pairing</strong>' +
      candidates
        .map(
          (c) =>
            `<button type="button" class="candidate" data-candidate="${escapeHTML(c.device)}"><span>${escapeHTML(c.device)}</span><span>${Number.isFinite(c.rssi) ? c.rssi + ' dBm' : 'Detected'} · Select</span></button>`,
        )
        .join('')
    : 'No unregistered switch detected yet. You can enter its ID below.';
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
 */
$$('.close-dialog').forEach((button) =>
  button.addEventListener('click', () => button.closest('dialog').close()),
);
window.addEventListener('hashchange', changeView);
changeView();
/**
 * Apply the chosen availability filter and update its accessible selection state.
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
  showError('#add-error', '');
  renderDiscoveries();
  openDialog('#add-dialog');
});
/**
 * Fill registration identity fields from a selected nearby pairing candidate.
 *
 * @param {MouseEvent} event - Click whose target may identify a discovery candidate.
 */
$('#discoveries').addEventListener('click', (event) => {
  const button = event.target.closest('[data-candidate]');
  if (!button) return;
  const candidate = snapshot.discoveries.find((c) => c.device === button.dataset.candidate);
  $('#new-id').value = candidate.device;
  $('#new-suffix').value = candidate.suffix || '0002';
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
  const fields = {
    device: $('#new-id').value.trim().toLowerCase(),
    name: $('#new-name').value.trim(),
    outputs: Number($('#new-outputs').value),
    suffix: $('#new-suffix').value.trim().toLowerCase(),
  };
  if (!fields.name) {
    showError('#add-error', 'Enter a friendly name.');
    return;
  }
  formAction(event.currentTarget, '#add-error', async () => {
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
 * Refresh automatically after each completed request without overlapping polls.
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
  if (!settings) return;
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
  const busy = $('#mqtt-form').dataset.busy === 'true';
  $$('input,select,button', $('#mqtt-form')).forEach(
    (el) => (el.disabled = busy || !connected || !settings?.editable),
  );
  if (!busy && settings?.error)
    $('#mqtt-feedback').textContent = settings.error + ' The gateway will keep retrying.';
  else if (!busy && online && $('#mqtt-feedback').dataset.saved)
    $('#mqtt-feedback').textContent = 'Settings saved. Connected to the broker.';
  if (settings && !settings.editable)
    $('#mqtt-feedback').textContent = 'MQTT configuration is unavailable in this demo.';
}
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

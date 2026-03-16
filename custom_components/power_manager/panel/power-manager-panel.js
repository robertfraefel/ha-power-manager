/**
 * @file power-manager-panel.js
 * @layer FRONTEND
 *
 * Custom Web Component rendered in the Home Assistant sidebar as the
 * ⚡ Power Manager panel.  Communicates with the backend exclusively
 * through the HA WebSocket API (power_manager/* commands).
 *
 * Refresh strategy
 * ----------------
 * Two complementary mechanisms keep the dashboard current:
 *
 *  1. **Full reload** (`_load`) — fires once on startup, then on a
 *     `setInterval` matching `scan_interval_seconds`.  Fetches coordinator
 *     state (surplus, decisions, config) via WS and re-renders all sections.
 *
 *  2. **Live entity update** (`_updateLiveValues`) — called on every
 *     `set hass()` invocation (i.e. on every HA state change).  Walks DOM
 *     elements tagged with `data-live-watt` or `data-live-switch` and
 *     updates them directly from `hass.states`, so power readings and switch
 *     state dots refresh in real time without a WS round-trip.
 *
 * Status dots
 * -----------
 * Each consumer row shows two overlaid dots:
 *  - Large circle  — coordinator decision (green = ON, grey = OFF, outline = deactivated)
 *  - Small square  — actual HA switch state (blue = ON, grey = OFF, outlined = unknown)
 */

/** Increment this whenever the panel JS changes. Shown in the summary bar. */
const PANEL_VERSION = '0.2.25';

/**
 * `<power-manager-panel>` — sidebar panel for the Power Manager integration.
 *
 * Registered as a custom element via `customElements.define`.  HA creates
 * one instance per panel navigation and sets the `hass` property whenever
 * the global HA state object changes.
 */
class PowerManagerPanel extends HTMLElement {
  /**
   * Called by the browser when the element is inserted into the DOM.
   * Renders the static shell and wires up "Add" button handlers on first
   * connection; restarts the polling timer if the user navigated back.
   */
  connectedCallback() {
    if (!this._initialized) {
      // Persistent edit maps: keyed by consumer/producer name, cleared on save.
      // Tracks in-progress field edits so poll-timer re-renders never clobber them.
      this._pendingEdits = {};
      this._pendingProdEdits = {};
      this._renderShell();
      this._bind();
      this._initialized = true;
    } else if (!this._pollTimer) {
      // User navigated away (which cleared the timer) and came back
      this._load();
    }
  }

  /**
   * Called when the element is removed from the DOM (e.g. user navigates away).
   * Clears the auto-refresh timer to prevent memory leaks and stale WS calls.
   */
  disconnectedCallback() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
    if (this._lightTimer) {
      clearInterval(this._lightTimer);
      this._lightTimer = null;
    }
    this._loading = false;
  }

  /**
   * HA calls this setter on every global state change.
   * On first call it bootstraps the component; on subsequent calls it
   * applies a lightweight live-value update without a full WS reload.
   *
   * @param {Object} hass - The Home Assistant frontend `hass` object.
   */
  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      // HA may call set hass() before connectedCallback(), so initialise the
      // edit maps here too — otherwise _renderProducers/_renderConsumers crash.
      this._pendingEdits = this._pendingEdits || {};
      this._pendingProdEdits = this._pendingProdEdits || {};
      this._renderShell();
      this._bind();
      this._initialized = true;
      this._load();
    } else if (this._data) {
      // Light update: refresh entity-based live values without a WS round-trip
      this._updateLiveValues();
    }
  }

  // ── helpers ────────────────────────────────────────────────────────────────

  /**
   * Send a WebSocket command to the backend and return the response.
   *
   * @param {string} type  - WS command type, e.g. `"power_manager/get_config"`.
   * @param {Object} extra - Additional payload fields merged into the WS message.
   * @returns {Promise<*>} Resolved with the server response.
   */
  async _ws(type, extra = {}) {
    return this._hass.callWS({ type, ...extra });
  }

  /**
   * Return all known entity IDs for a given domain, merging live states with
   * the entity registry so newly-added entities that haven't reported a state
   * yet are still included in datalist autocomplete suggestions.
   *
   * @param {string} domain - HA domain, e.g. `"sensor"` or `"switch"`.
   * @returns {Promise<string[]>} Sorted, deduplicated list of entity IDs.
   */
  async _entitiesByDomain(domain) {
    const stateEntities = Object.keys(this._hass?.states || {}).filter((id) =>
      id.startsWith(`${domain}.`)
    );
    let registryEntities = [];
    try {
      const registry = await this._ws('config/entity_registry/list');
      registryEntities = (registry || [])
        .map((e) => e?.entity_id)
        .filter((id) => id && id.startsWith(`${domain}.`));
    } catch (_) { /* fall back to states */ }
    return Array.from(new Set([...stateEntities, ...registryEntities])).sort();
  }

  /**
   * Convert a HA state object to watts, handling W / kW / MW units.
   *
   * @param {Object|undefined} stateObj - A `hass.states[entityId]` object.
   * @returns {number} Value in watts, or `NaN` if unavailable/non-numeric.
   */
  _toWatts(stateObj) {
    if (!stateObj) return NaN;
    const value = Number(stateObj.state);
    if (!Number.isFinite(value)) return NaN;
    const unit = String(stateObj.attributes?.unit_of_measurement || '').trim().toLowerCase();
    if (unit === 'kw' || unit === 'kilowatt' || unit === 'kilowatts') return value * 1000;
    if (unit === 'mw' || unit === 'megawatt' || unit === 'megawatts') return value * 1000000;
    return value;
  }

  /**
   * Escape a value for safe use inside an HTML attribute (e.g. value="…").
   * Prevents names containing `"`, `<`, `>`, or `&` from breaking innerHTML.
   *
   * @param {*} s - Value to escape.
   * @returns {string} HTML-attribute-safe string.
   */
  _esc(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  /**
   * Refresh entity-state values in the DOM without a full WS reload.
   *
   * Walks elements tagged with:
   *  - `data-live-watt`   — updates text content with the current watt reading.
   *  - `data-live-switch` — updates the CSS class and title of the actual-state dot.
   *
   * Called on every `set hass()` invocation so values track HA state in real time.
   */
  _updateLiveValues() {
    // Power sensor cells tagged with data-live-watt
    this.querySelectorAll('[data-live-watt]').forEach((el) => {
      const entityId = el.dataset.liveWatt;
      if (!entityId) return;
      const w = this._toWatts(this._hass?.states?.[entityId]);
      if (Number.isFinite(w)) el.textContent = w.toFixed(1);
    });

    // Switch state dots tagged with data-live-switch
    this.querySelectorAll('[data-live-switch]').forEach((el) => {
      const entityId = el.dataset.liveSwitch;
      if (!entityId) return;
      const state = this._hass?.states?.[entityId]?.state;
      const isOn = state === 'on';
      const isUnknown = state === undefined || state === null;
      el.className = `status-dot dot-actual ${isUnknown ? 'dot-actual-unknown' : (isOn ? 'dot-actual-on' : 'dot-actual-off')}`;
      el.title = isUnknown ? 'Switch: unknown' : (isOn ? 'Switch: ON' : 'Switch: OFF');
    });
  }

  // ── shell ──────────────────────────────────────────────────────────────────

  /**
   * Write the full static HTML skeleton (styles, card containers, datalists,
   * "Add" row inputs) into `this.innerHTML`.  Called once on first connection.
   * Dynamic content (rows, summary tiles) is filled in by the render* methods.
   */
  _renderShell() {
    this.innerHTML = `
      <style>
        .wrap { padding: 12px 16px; font-family: var(--primary-font-family); max-width: 1600px; }
        h2 { margin: 0 0 10px; font-size: 1.3em; }
        h3 { margin: 0 0 8px; font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.6; }
        .card { border: 1px solid var(--divider-color); border-radius: 10px; padding: 12px; margin: 6px 0; background: var(--card-background-color, var(--primary-background-color)); }
        /* Summary grid */
        .summary-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 6px; }
        .stat { background: var(--secondary-background-color, rgba(0,0,0,.04)); border-radius: 6px; padding: 6px 10px; }
        .stat-label { font-size: 10px; opacity: 0.55; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 2px; }
        .stat-value { font-size: 1.05em; font-weight: 600; }
        /* Tables */
        .table-wrap { overflow-x: auto; margin: 0 0 8px; }
        table { width: 100%; border-collapse: collapse; white-space: nowrap; }
        th { border-bottom: 2px solid var(--divider-color); padding: 5px 6px; text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.6; }
        td { border-bottom: 1px solid var(--divider-color); padding: 3px 5px; vertical-align: middle; }
        tr:last-child td { border-bottom: none; }
        /* Inputs inside tables — compact */
        td input, td select { padding: 3px 5px; min-width: 0; width: 100%; box-sizing: border-box; font-size: 12px; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--primary-background-color); color: var(--primary-text-color); }
        td input[type=number] { width: 62px; }
        td input[list] { width: 240px; }
        td select { width: 105px; }
        /* "Add" row inputs */
        .add-row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; padding-top: 8px; border-top: 1px solid var(--divider-color); margin-top: 2px; }
        .add-row input, .add-row select { padding: 5px 7px; min-width: 0; font-size: 12px; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--primary-background-color); color: var(--primary-text-color); }
        .add-row input[list] { width: 220px; }
        .add-row input[type=number] { width: 80px; }
        .add-row input:not([list]):not([type=number]) { width: 110px; }
        /* Buttons */
        button { padding: 4px 9px; cursor: pointer; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--secondary-background-color, #f5f5f5); color: var(--primary-text-color); font-size: 12px; }
        button:hover { background: var(--primary-color, #03a9f4); color: #fff; border-color: transparent; }
        .btn-add { background: var(--primary-color, #03a9f4); color: #fff; border-color: transparent; font-weight: 600; }
        .btn-add:hover { opacity: 0.85; }
        .btn-del { color: var(--error-color, #db4437); }
        .btn-del:hover { background: var(--error-color, #db4437); color: #fff; }
        /* Consumer status dots */
        .status-cell { display: flex; align-items: center; justify-content: center; gap: 4px; }
        .status-dot { display: inline-block; border-radius: 50%; vertical-align: middle; flex-shrink: 0; }
        .dot-decision { width: 12px; height: 12px; }
        .dot-decision-on  { background: #2e7d32; box-shadow: 0 0 4px #2e7d3280; }
        .dot-decision-off { background: #9e9e9e; }
        .dot-decision-deactivated { background: transparent; border: 2px solid #bdbdbd; width: 10px; height: 10px; }
        .dot-actual { width: 7px; height: 7px; border-radius: 2px; }
        .dot-actual-on  { background: #1565c0; }
        .dot-actual-off { background: #bdbdbd; }
        .dot-actual-unknown { background: transparent; border: 1px solid #bdbdbd; }
        /* Running toggle */
        .toggle-wrap { display: flex; align-items: center; gap: 6px; }
        .toggle { position: relative; display: inline-block; width: 36px; height: 20px; flex-shrink: 0; }
        .toggle input { opacity: 0; width: 0; height: 0; }
        .toggle-track { position: absolute; cursor: pointer; inset: 0; background: #ccc; border-radius: 20px; transition: background .2s; }
        .toggle-track:before { position: absolute; content: ""; height: 14px; width: 14px; left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: transform .2s; }
        .toggle input:checked + .toggle-track { background: #2e7d32; }
        .toggle input:checked + .toggle-track:before { transform: translateX(16px); }
        /* Legend */
        .legend { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 6px 0 10px; font-size: 12px; }
        .legend-item { display: flex; align-items: center; gap: 5px; }
        .legend-sep { opacity: 0.35; }
      </style>
      <div class="wrap">
        <h2><img src="/power_manager_static/logo.svg" style="height:28px;vertical-align:middle;margin-right:8px;margin-bottom:3px"> Power Manager</h2>
        <datalist id="sensorEntitiesList"></datalist>
        <datalist id="switchEntitiesList"></datalist>

        <div id="summary" class="card">Loading…</div>

        <div class="card">
          <h3>Producers</h3>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Entity</th><th>Current power in W</th><th style="width:110px"></th></tr></thead>
              <tbody id="prodRows"></tbody>
            </table>
          </div>
          <div class="add-row">
            <input id="newProdName" placeholder="Name" />
            <input id="newProdEntity" list="sensorEntitiesList" placeholder="sensor.pv_power" />
            <button id="addProd" class="btn-add">+ Add producer</button>
          </div>
        </div>

        <div class="card">
          <h3>Base load</h3>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Entity</th><th>Current power in W</th><th style="width:110px"></th></tr></thead>
              <tbody id="baseRows"></tbody>
            </table>
          </div>
        </div>

        <div class="card">
          <h3>Consumers</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style="width:44px"></th>
                  <th>Name</th><th>Switch entity</th><th>Power sensor entity</th><th>Current power in W</th>
                  <th>Prio</th><th>Exp power in W</th><th>Min run time in min</th><th>Mode</th>
                  <th>Decision</th><th style="width:110px"></th>
                </tr>
              </thead>
              <tbody id="consRows"></tbody>
            </table>
          </div>
          <div class="legend">
            <span class="legend-item"><span class="status-dot dot-decision dot-decision-on"></span> Decision: ON</span>
            <span class="legend-item"><span class="status-dot dot-decision dot-decision-off"></span> Decision: OFF</span>
            <span class="legend-item"><span class="status-dot dot-decision dot-decision-deactivated"></span> Deactivated</span>
            <span class="legend-sep">·</span>
            <span class="legend-item"><span class="status-dot dot-actual dot-actual-on"></span> Switch ON</span>
            <span class="legend-item"><span class="status-dot dot-actual dot-actual-off"></span> Switch OFF</span>
          </div>
          <div class="legend" style="margin-top:2px;font-size:11px;opacity:0.7">
            <span><b>P#</b> = priority (1 = first to get surplus)</span>
            <span class="legend-sep">·</span>
            <span><b>surplus ok</b> = enough solar to switch ON</span>
            <span class="legend-sep">·</span>
            <span><b>no surplus</b> = not enough solar</span>
            <span class="legend-sep">·</span>
            <span><b>min-run hold</b> = within minimum runtime · <b>⏱</b> = time remaining</span>
          </div>
          <div class="add-row">
            <input id="newConName" placeholder="Name" />
            <input id="newConSwitch" list="switchEntitiesList" placeholder="switch.xxx" />
            <input id="newConPower" list="sensorEntitiesList" placeholder="sensor.xxx" />
            <input id="newConPrio" type="number" placeholder="Prio" />
            <input id="newConExpected" type="number" placeholder="Exp W" />
            <input id="newConMin" type="number" placeholder="Min min" />
            <button id="addCon" class="btn-add">+ Add consumer</button>
          </div>
        </div>
      </div>
    `;
  }

  // ── bind static buttons ────────────────────────────────────────────────────

  /**
   * Attach click handlers to the static "Add producer" and "Add consumer"
   * buttons in the shell.  Row-level Save/Del handlers are attached per-row
   * inside `_renderProducers` and `_renderConsumers`.
   */
  _bind() {
    this.querySelector('#addProd').onclick = async () => {
      const prodName = this.querySelector('#newProdName').value.trim();
      if (!prodName) {
        alert('Producer name cannot be empty.');
        return;
      }
      try {
        await this._ws('power_manager/add_producer', {
          name: prodName,
          entity_id: this.querySelector('#newProdEntity').value.trim(),
        });
        ['#newProdName', '#newProdEntity'].forEach((sel) => { this.querySelector(sel).value = ''; });
        await this._load();
      } catch (err) {
        alert(`Failed to add producer: ${err?.message || err}`);
        this._loading = false; // clear in case a previous _load() left it stuck
        await this._load();
      }
    };

    this.querySelector('#addCon').onclick = async () => {
      const conName = this.querySelector('#newConName').value.trim();
      if (!conName) {
        alert('Consumer name cannot be empty.');
        return;
      }
      const priorityNum = Number(this.querySelector('#newConPrio').value);
      const expectedNum = Number(this.querySelector('#newConExpected').value);
      const minNum = Number(this.querySelector('#newConMin').value);
      try {
        await this._ws('power_manager/add_consumer', {
          name: conName,
          switch_entity: this.querySelector('#newConSwitch').value.trim(),
          power_entity: this.querySelector('#newConPower').value.trim(),
          priority: Number.isFinite(priorityNum) ? Math.round(priorityNum) : 1,
          expected_power: Number.isFinite(expectedNum) ? expectedNum : 0,
          min_run_minutes: Number.isFinite(minNum) ? minNum : 0,
        });
        ['#newConName', '#newConSwitch', '#newConPower', '#newConPrio', '#newConExpected', '#newConMin']
          .forEach((sel) => { this.querySelector(sel).value = ''; });
        await this._load();
      } catch (err) {
        alert(`Failed to add consumer: ${err?.message || err}`);
        this._loading = false; // clear in case a previous _load() left it stuck
        await this._load();
      }
    };
  }

  // ── full data load ─────────────────────────────────────────────────────────

  /**
   * Fetch the full coordinator state via `power_manager/get_config`, populate
   * entity datalists, and re-render all four sections (summary, base load,
   * producers, consumers).
   *
   * Also starts the auto-refresh `setInterval` on the first successful call,
   * using `scan_interval_seconds` from the response (minimum 5 s).
   *
   * @returns {Promise<void>}
   */
  async _load() {
    if (this._loading) return;
    this._loading = true;
    try {
      let data;
      try {
        data = await this._ws('power_manager/get_config');
      } catch (err) {
        console.error('[PowerManager] get_config failed:', err);
        return;
      }
      this._data = data;

      // Start (or restart if interval changed) the auto-refresh poll timer.
      const interval = Math.max(5, Number(data.scan_interval_seconds) || 10) * 1000;
      if (this._pollTimer && this._pollIntervalMs !== interval) {
        clearInterval(this._pollTimer);
        this._pollTimer = null;
      }
      if (!this._pollTimer) {
        this._pollIntervalMs = interval;
        this._pollTimer = setInterval(() => this._load(), interval);
      }
      if (!this._lightTimer) {
        // Fast 2 s tick to keep live entity values current even when HA pushes no updates
        this._lightTimer = setInterval(() => { if (this._data) this._updateLiveValues(); }, 2000);
      }

      // Refresh entity datalists
      const [sensorEntities, switchEntities] = await Promise.all([
        this._entitiesByDomain('sensor'),
        this._entitiesByDomain('switch'),
      ]);
      this.querySelector('#sensorEntitiesList').innerHTML = sensorEntities
        .map((v) => `<option value="${v}"></option>`).join('');
      this.querySelector('#switchEntitiesList').innerHTML = switchEntities
        .map((v) => `<option value="${v}"></option>`).join('');

      this._renderSummary(data);

      // Skip re-rendering a section if the user is actively editing an input
      // inside it — otherwise the DOM replacement kills the datalist dropdown
      // and overwrites in-progress changes.
      const active = this.querySelector(':focus');
      const baseSection = this.querySelector('#baseRows');
      const prodSection = this.querySelector('#prodRows');
      const consSection = this.querySelector('#consRows');

      if (!active || !baseSection?.contains(active)) this._renderBaseLoad(data);
      if (!active || !prodSection?.contains(active)) this._renderProducers(data);
      if (!active || !consSection?.contains(active)) this._renderConsumers(data);
    } finally {
      // Always clear the guard — even if a render function throws,
      // the next _load() call must not silently become a no-op.
      this._loading = false;
    }
  }

  // ── summary card ───────────────────────────────────────────────────────────

  /**
   * Render the top summary tile grid showing production, base load, surplus,
   * remaining surplus, scan interval, running state, and version.
   *
   * @param {Object} data - Response from `power_manager/get_config`.
   */
  _renderSummary(data) {
    const prod = Number(data.total_production ?? 0);
    const base = Number(data.base_load ?? data.base_load_current_w ?? 0);
    const surplus = Number(data.surplus ?? (prod - base));
    const remaining = Number(data.remaining_surplus ?? surplus);
    const fmt = (v) => Number.isFinite(v) ? v.toFixed(1) + ' W' : 'n/a';
    const surplusColor = surplus >= 0 ? '#2e7d32' : '#b71c1c';

    this.querySelector('#summary').innerHTML = `
      <div class="summary-grid">
        <div class="stat"><div class="stat-label">Production</div><div class="stat-value">${fmt(prod)}</div></div>
        <div class="stat"><div class="stat-label">${data.base_load_name || 'Base load'}</div><div class="stat-value">${fmt(base)}</div></div>
        <div class="stat"><div class="stat-label">Surplus</div><div class="stat-value" style="color:${surplusColor}">${fmt(surplus)}</div></div>
        <div class="stat"><div class="stat-label">Remaining</div><div class="stat-value">${fmt(remaining)}</div></div>
        <div class="stat"><div class="stat-label">Scan interval</div><div class="stat-value" style="display:flex;align-items:center;gap:4px">
          <input id="scanIntervalInput" type="number" min="5" max="3600" value="${data.scan_interval_seconds}"
                 style="width:52px;padding:2px 4px;font-size:.9em;border:1px solid var(--divider-color);border-radius:4px;background:var(--primary-background-color);color:var(--primary-text-color)">
          <span style="font-size:.85em">s</span>
          <button id="saveScanInterval" style="padding:2px 6px;font-size:.85em">Set</button>
        </div></div>
        <div class="stat"><div class="stat-label">Status</div><div class="stat-value">
          <div class="toggle-wrap">
            <label class="toggle"><input type="checkbox" id="runningToggle" ${data.running ? 'checked' : ''}><span class="toggle-track"></span></label>
            <span>${data.running ? 'Running' : 'Stopped'}</span>
          </div>
        </div></div>
        <div class="stat"><div class="stat-label">Backend</div><div class="stat-value" style="font-size:.9em">${data.integration_version}</div></div>
        <div class="stat"><div class="stat-label">Dashboard</div><div class="stat-value" style="font-size:.9em">${PANEL_VERSION}</div></div>
      </div>
    `;

    this.querySelector('#runningToggle').onchange = async (e) => {
      await this._hass.callService('power_manager', 'set_running', { running: e.target.checked });
      await this._load();
    };

    this.querySelector('#saveScanInterval').onclick = async () => {
      const val = parseInt(this.querySelector('#scanIntervalInput').value, 10);
      if (!Number.isFinite(val) || val < 5) {
        alert('Scan interval must be at least 5 seconds.');
        return;
      }
      await this._ws('power_manager/set_scan_interval', { scan_interval_seconds: val });
      await this._load();
    };
  }

  // ── base load card ─────────────────────────────────────────────────────────

  /**
   * Render the base load card with an editable entity ID input, live watt
   * reading, Save and Clear buttons.
   *
   * The watt cell is tagged with `data-live-watt` so `_updateLiveValues()`
   * keeps it current between full reloads.
   *
   * @param {Object} data - Response from `power_manager/get_config`.
   */
  _renderBaseLoad(data) {
    const entityId = data.base_load_entity || '';
    const baseName = data.base_load_name || 'Base load';
    const liveW = this._toWatts(this._hass?.states?.[entityId]);
    const displayW = Number.isFinite(liveW) ? liveW.toFixed(1) : (data.base_load_current_w ?? 'n/a');

    const tbody = this.querySelector('#baseRows');
    tbody.innerHTML = `
      <tr>
        <td><input id="baseName" value="${baseName}" placeholder="Base load" /></td>
        <td><input id="baseEntity" list="sensorEntitiesList" value="${entityId}" placeholder="sensor.house_total_power" /></td>
        <td data-live-watt="${entityId}">${displayW}</td>
        <td style="white-space:nowrap">
          <button id="saveBase">Save</button>
          <button id="delBase" class="btn-del">Clear</button>
        </td>
      </tr>
    `;

    this.querySelector('#saveBase').onclick = async () => {
      await this._ws('power_manager/set_base', {
        base_load_entity: this.querySelector('#baseEntity').value.trim(),
        base_load_name: this.querySelector('#baseName').value.trim() || 'Base load',
      });
      await this._load();
    };
    this.querySelector('#delBase').onclick = async () => {
      await this._ws('power_manager/set_base', { base_load_entity: '', base_load_name: 'Base load' });
      await this._load();
    };
  }

  // ── producers card ─────────────────────────────────────────────────────────

  /**
   * Render one table row per configured producer with editable name and
   * entity_id inputs, a live watt cell (`data-live-watt`), Save and Del
   * buttons.
   *
   * Passing `new_name` in the save payload allows the producer to be renamed
   * in a single round-trip.
   *
   * @param {Object} data - Response from `power_manager/get_config`.
   */
  _renderProducers(data) {
    const prodRows = this.querySelector('#prodRows');
    prodRows.innerHTML = '';
    (data.producers || []).forEach((p) => {
      const liveW = this._toWatts(this._hass?.states?.[p.entity_id]);
      const displayW = Number.isFinite(liveW) ? liveW.toFixed(1)
        : ((data.producer_states || {})[p.name]?.power ?? 'n/a');

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><input data-k="name" value="${this._esc(p.name)}" /></td>
        <td><input data-k="entity" list="sensorEntitiesList" value="${this._esc(p.entity_id)}" placeholder="sensor.xxx" /></td>
        <td data-live-watt="${this._esc(p.entity_id)}">${displayW}</td>
        <td style="white-space:nowrap">
          <button data-a="save">Save</button>
          <button data-a="del" class="btn-del">Del</button>
        </td>
      `;
      // Restore any in-progress edits for this producer
      this._pendingProdEdits = this._pendingProdEdits || {};
      if (this._pendingProdEdits[p.name]) {
        Object.entries(this._pendingProdEdits[p.name]).forEach(([k, v]) => {
          const el = tr.querySelector(`[data-k="${k}"]`);
          if (el) el.value = v;
        });
      }
      // Track future edits in real-time
      tr.querySelectorAll('[data-k]').forEach((el) => {
        el.addEventListener('input', () => {
          if (!this._pendingProdEdits[p.name]) this._pendingProdEdits[p.name] = {};
          this._pendingProdEdits[p.name][el.dataset.k] = el.value;
        });
      });

      tr.querySelector('[data-a="save"]').onclick = async () => {
        const newName = tr.querySelector('[data-k="name"]').value.trim();
        const entity = tr.querySelector('[data-k="entity"]').value.trim();
        const payload = { name: p.name, entity_id: entity };
        if (newName && newName !== p.name) payload.new_name = newName;
        try {
          await this._ws('power_manager/update_producer', payload);
          delete this._pendingProdEdits[p.name];
          await this._load();
        } catch (err) {
          alert(`Failed to save producer: ${err?.message || err}`);
        }
      };
      tr.querySelector('[data-a="del"]').onclick = async () => {
        delete this._pendingProdEdits[p.name];
        await this._ws('power_manager/remove_producer', { name: p.name });
        await this._load();
      };
      prodRows.appendChild(tr);
    });
  }

  // ── consumers card ─────────────────────────────────────────────────────────

  /**
   * Render one table row per configured consumer.
   *
   * Each row contains:
   *  - Two-dot status indicator (coordinator decision + actual switch state)
   *  - Editable fields: name, switch entity, power entity, priority,
   *    expected power, min runtime, mode dropdown
   *  - Live watt cell tagged with `data-live-watt`
   *  - Actual-state dot tagged with `data-live-switch`
   *  - Decision text column (surplus budget reasoning computed client-side)
   *  - Save and Del buttons
   *
   * The decision text is computed locally by replaying the coordinator's
   * priority-ordered allocation logic over the snapshot data, so it does not
   * require an extra WS call.
   *
   * @param {Object} data - Response from `power_manager/get_config`.
   */
  _renderConsumers(data) {
    // Pre-compute decision info per consumer
    const conditionByConsumer = {};
    let remainingSurplus = Number(data.surplus || 0);
    const nowTs = Date.now() / 1000;
    const _fmtTime = (sec) => sec >= 60
      ? `${Math.floor(sec / 60)}m ${sec % 60}s`
      : `${sec}s`;
    [...(data.consumers || [])]
      .sort((a, b) => Number(a.priority ?? 999) - Number(b.priority ?? 999))
      .forEach((c) => {
        const state = (data.consumer_states || {})[c.name] || {};
        const mode = state.mode || c.mode || 'auto';
        const expected = Number(c.expected_power || 0);
        const priority = Number(c.priority ?? 999);
        const isOn = Boolean(state.is_on);
        const onUntil = Number(state.on_until || 0);
        const secLeft = onUntil > nowTs ? Math.max(0, Math.round(onUntil - nowTs)) : 0;
        // Show countdown while running; once expired keep showing '✓' (min-run done)
        // so the timer never disappears while the consumer is ON.
        const timeLeft = secLeft > 0 ? _fmtTime(secLeft) : (isOn ? '✓' : null);

        let reason;
        if (mode === 'deactivated') {
          reason = 'deactivated';
        } else if (mode === 'force_on') {
          reason = 'force_on';
        } else if (mode === 'force_off') {
          reason = 'force_off';
        } else {
          // auto mode — mirror the backend algorithm exactly:
          //   Consumer ON  → stay on while remaining_surplus >= -(current_power * 0.05).
          //                  Draw is already in base_load; do not deduct.
          //   Consumer OFF → turn on when remaining_surplus >= expected * 1.05.
          //                  Deduct expected so lower-priority consumers see the
          //                  correct reduced budget in this display pass.
          const currentPower = Number(state.power ?? 0);
          const turnOnThreshold = expected * 1.05;
          const turnOffThreshold = -(currentPower * 0.05);
          if (isOn) {
            if (remainingSurplus >= turnOffThreshold) {
              reason = `running (${remainingSurplus.toFixed(0)}W surplus)`;
            } else if (onUntil > nowTs) {
              reason = 'min-run hold';  // timer still counting down
            } else {
              // Timer expired but consumer still ON (e.g. waiting for incremental shed).
              reason = `surplus low (${remainingSurplus.toFixed(0)}W)`;
            }
            // No deduction — draw already reflected in base_load.
          } else {
            if (remainingSurplus >= turnOnThreshold) {
              reason = `surplus ok (${remainingSurplus.toFixed(0)}W ≥ ${turnOnThreshold.toFixed(0)}W)`;
              remainingSurplus -= expected;
            } else {
              reason = `no surplus (${remainingSurplus.toFixed(0)}W < ${turnOnThreshold.toFixed(0)}W)`;
            }
          }
        }
        conditionByConsumer[c.name] = { priority, reason, timeLeft };
      });

    const consRows = this.querySelector('#consRows');
    consRows.innerHTML = '';
    (data.consumers || []).forEach((c) => {
      const stateData = (data.consumer_states || {})[c.name] || {};

      // Power value — prefer live HA state
      const liveW = this._toWatts(this._hass?.states?.[c.power_entity]);
      const coordW = Number(stateData.power);
      const displayW = Number.isFinite(liveW) ? liveW.toFixed(1)
        : (Number.isFinite(coordW) ? coordW.toFixed(1) : 'n/a');

      // Status dots
      const isDeactivated = (stateData.mode || 'auto') === 'deactivated';
      const coordDecision = Boolean(stateData.is_on);
      const coordDotClass = isDeactivated ? 'dot-decision-deactivated'
        : (coordDecision ? 'dot-decision-on' : 'dot-decision-off');
      const coordTitle = isDeactivated ? 'Decision: deactivated'
        : (coordDecision ? 'Decision: ON' : 'Decision: OFF');

      const switchState = this._hass?.states?.[c.switch_entity]?.state;
      const actualOn = switchState === 'on';
      const actualDotClass = switchState === undefined ? 'dot-actual-unknown'
        : (actualOn ? 'dot-actual-on' : 'dot-actual-off');
      const actualTitle = switchState === undefined ? 'Switch: unknown'
        : (actualOn ? 'Switch: ON' : 'Switch: OFF');

      // Current runtime mode (from coordinator) — used to pre-select dropdown
      const currentMode = stateData.mode || c.mode || 'auto';

      // Decision column text
      const decisionHtml = (() => {
        if (!data.running) return '<span style="opacity:0.6;font-style:italic">Power Manager stopped</span>';
        const { priority, reason, timeLeft } = conditionByConsumer[c.name] || { priority: c.priority ?? '?', reason: 'n/a', timeLeft: null };
        const prioHtml = `<span style="display:inline-block;background:#e0e0e0;color:#333;border-radius:3px;padding:1px 5px;font-weight:700;font-size:10px;margin-right:5px;letter-spacing:0.03em">P${priority}</span>`;
        const timeHtml = timeLeft ? `<br><span style="opacity:0.65">⏱ ${timeLeft}${timeLeft !== '✓' ? ' left' : ''}</span>` : '';
        return `${prioHtml}${reason}${timeHtml}`;
      })();

      const tr = document.createElement('tr');
      tr.dataset.consumer = c.name;
      tr.innerHTML = `
        <td style="text-align:center">
          <span class="status-dot dot-decision ${coordDotClass}" title="${this._esc(coordTitle)}"></span>
        </td>
        <td><input data-k="name" value="${this._esc(c.name)}" /></td>
        <td style="white-space:nowrap">
          <span class="status-dot dot-actual ${actualDotClass}" title="${this._esc(actualTitle)}"
                data-live-switch="${this._esc(c.switch_entity)}"
                style="vertical-align:middle;margin-right:4px"></span><input data-k="switch" list="switchEntitiesList" value="${this._esc(c.switch_entity)}" placeholder="switch.xxx" style="width:250px;display:inline-block;vertical-align:middle" /></td>
        <td><input data-k="power" list="sensorEntitiesList" value="${this._esc(c.power_entity)}" placeholder="sensor.xxx" /></td>
        <td style="text-align:right;padding-right:10px" data-live-watt="${this._esc(c.power_entity)}">${displayW}</td>
        <td><input data-k="priority" type="number" value="${c.priority ?? 1}" /></td>
        <td><input data-k="expected" type="number" value="${c.expected_power ?? 0}" /></td>
        <td><input data-k="min" type="number" value="${c.min_run_minutes ?? 0}" /></td>
        <td>
          <select data-k="mode">
            <option value="auto">auto</option>
            <option value="force_on">force_on</option>
            <option value="force_off">force_off</option>
            <option value="deactivated">deactivated</option>
          </select>
        </td>
        <td style="font-size:11px;max-width:220px;white-space:normal;line-height:1.5">${decisionHtml}</td>
        <td style="white-space:nowrap">
          <button data-a="save">Save</button>
          <button data-a="del" class="btn-del">Del</button>
        </td>
      `;

      // Set dropdown to current runtime mode
      tr.querySelector('[data-k="mode"]').value = currentMode;
      // Restore any in-progress edits (tracked via _pendingEdits since last save)
      this._pendingEdits = this._pendingEdits || {};
      if (this._pendingEdits[c.name]) {
        Object.entries(this._pendingEdits[c.name]).forEach(([k, v]) => {
          const el = tr.querySelector(`[data-k="${k}"]`);
          if (el) el.value = v;
        });
      }
      // Track future edits in real-time so poll-timer re-renders never lose them
      tr.querySelectorAll('[data-k]').forEach((el) => {
        el.addEventListener('input', () => {
          if (!this._pendingEdits[c.name]) this._pendingEdits[c.name] = {};
          this._pendingEdits[c.name][el.dataset.k] = el.value;
        });
      });

      tr.querySelector('[data-a="save"]').onclick = async () => {
        const newName = tr.querySelector('[data-k="name"]').value.trim();
        const priorityNum = Number(tr.querySelector('[data-k="priority"]').value);
        const expectedNum = Number(tr.querySelector('[data-k="expected"]').value);
        const minNum = Number(tr.querySelector('[data-k="min"]').value);
        const payload = {
          name: c.name,
          switch_entity: tr.querySelector('[data-k="switch"]').value.trim(),
          power_entity: tr.querySelector('[data-k="power"]').value.trim(),
          priority: Number.isFinite(priorityNum) ? Math.round(priorityNum) : Number(c.priority ?? 1),
          expected_power: Number.isFinite(expectedNum) ? expectedNum : Number(c.expected_power ?? 0),
          min_run_minutes: Number.isFinite(minNum) ? minNum : Number(c.min_run_minutes ?? 0),
          mode: tr.querySelector('[data-k="mode"]').value,
        };
        if (newName && newName !== c.name) payload.new_name = newName;
        try {
          await this._ws('power_manager/update_consumer', payload);
          delete this._pendingEdits[c.name];
          await this._load();
        } catch (err) {
          alert(`Failed to save consumer: ${err?.message || err}`);
        }
      };
      tr.querySelector('[data-a="del"]').onclick = async () => {
        delete this._pendingEdits[c.name];
        await this._ws('power_manager/remove_consumer', { name: c.name });
        await this._load();
      };
      consRows.appendChild(tr);
    });
  }
}

customElements.define('power-manager-panel', PowerManagerPanel);

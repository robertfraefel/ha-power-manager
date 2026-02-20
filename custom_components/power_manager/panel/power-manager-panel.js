class PowerManagerPanel extends HTMLElement {
  connectedCallback() {
    if (!this._initialized) {
      this._renderShell();
      this._bind();
      this._initialized = true;
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._renderShell();
      this._bind();
      this._initialized = true;
      this._load();
    }
  }

  async _ws(type, extra = {}) {
    return this._hass.callWS({ type, ...extra });
  }

  async _entitiesByDomain(domain) {
    const stateEntities = Object.keys(this._hass?.states || {}).filter((entityId) =>
      entityId.startsWith(`${domain}.`)
    );

    let registryEntities = [];
    try {
      const registry = await this._ws('config/entity_registry/list');
      registryEntities = (registry || [])
        .map((entry) => entry?.entity_id)
        .filter((entityId) => entityId && entityId.startsWith(`${domain}.`));
    } catch (_err) {
      // Fallback to states only if entity registry WS is unavailable
    }

    return Array.from(new Set([...stateEntities, ...registryEntities])).sort();
  }

  _datalistHtml(listId, options, selected = "") {
    const normalizedSelected = selected || "";
    const all = [...options];
    if (normalizedSelected && !all.includes(normalizedSelected)) {
      all.unshift(normalizedSelected);
    }

    return `<datalist id="${listId}">${all
      .map((value) => `<option value="${value}"></option>`)
      .join("")}</datalist>`;
  }

  _entityInput(inputId, listId, options, selected = "", placeholder = "") {
    return `
      <input id="${inputId}" list="${listId}" value="${selected || ''}" placeholder="${placeholder}" />
      ${this._datalistHtml(listId, options, selected)}
    `;
  }

  _toWatts(stateObj) {
    if (!stateObj) return NaN;
    const value = Number(stateObj.state);
    if (!Number.isFinite(value)) return NaN;

    const unit = String(stateObj.attributes?.unit_of_measurement || '').trim().toLowerCase();
    if (unit === 'kw' || unit === 'kilowatt' || unit === 'kilowatts') return value * 1000;
    if (unit === 'mw' || unit === 'megawatt' || unit === 'megawatts') return value * 1000000;
    return value;
  }

  _renderShell() {
    this.innerHTML = `
      <style>
        .wrap { padding: 12px 16px; font-family: var(--primary-font-family); max-width: 1600px; }
        h2 { margin: 0 0 10px; font-size: 1.3em; }
        h3 { margin: 0 0 8px; font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.6; }
        .card { border: 1px solid var(--divider-color); border-radius: 10px; padding: 12px; margin: 6px 0; background: var(--card-background-color, var(--primary-background-color)); }
        /* Summary grid */
        .summary-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 6px; margin-bottom: 10px; }
        .stat { background: var(--secondary-background-color, rgba(0,0,0,.04)); border-radius: 6px; padding: 6px 10px; }
        .stat-label { font-size: 10px; opacity: 0.55; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 2px; }
        .stat-value { font-size: 1.05em; font-weight: 600; }
        /* Base load row inside summary */
        .base-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding-top: 8px; border-top: 1px solid var(--divider-color); }
        .base-row label { font-size: 12px; opacity: 0.65; white-space: nowrap; }
        .base-row input { flex: 1; min-width: 160px; max-width: 340px; padding: 4px 8px; font-size: 13px; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--primary-background-color); color: var(--primary-text-color); }
        /* Tables */
        .table-wrap { overflow-x: auto; margin: 0 0 8px; }
        table { width: 100%; border-collapse: collapse; white-space: nowrap; }
        th { border-bottom: 2px solid var(--divider-color); padding: 5px 6px; text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.6; }
        td { border-bottom: 1px solid var(--divider-color); padding: 3px 5px; vertical-align: middle; }
        tr:last-child td { border-bottom: none; }
        /* Inputs inside tables — compact */
        td input, td select { padding: 3px 5px; min-width: 0; width: 100%; box-sizing: border-box; font-size: 12px; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--primary-background-color); color: var(--primary-text-color); }
        td input[type=number] { width: 62px; }
        td input[list] { width: 155px; }
        td select { width: 105px; }
        /* "Add" row inputs */
        .add-row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; padding-top: 8px; border-top: 1px solid var(--divider-color); margin-top: 2px; }
        .add-row input, .add-row select { padding: 5px 7px; min-width: 0; font-size: 12px; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--primary-background-color); color: var(--primary-text-color); }
        .add-row input[list] { width: 170px; }
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
        /* Legend */
        .legend { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 2px 0 8px; font-size: 11px; opacity: 0.7; }
        .legend-item { display: flex; align-items: center; gap: 4px; }
        .legend-sep { opacity: 0.35; }
      </style>
      <div class="wrap">
        <h2>⚡ Power Manager</h2>
        <datalist id="sensorEntitiesList"></datalist>
        <datalist id="switchEntitiesList"></datalist>
        <div id="summary" class="card">Loading…</div>

        <div class="card">
          <h3>Producers</h3>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Entity</th><th>Current W</th><th style="width:110px"></th></tr></thead>
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
          <h3>Consumers</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style="width:44px"></th>
                  <th>Name</th><th>Switch</th><th>Power sensor</th><th>W now</th>
                  <th>Prio</th><th>Exp W</th><th>Min min</th><th>Mode</th>
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

  _bind() {
    this.querySelector('#addProd').onclick = async () => {
      await this._ws('power_manager/add_producer', {
        name: this.querySelector('#newProdName').value.trim(),
        entity_id: this.querySelector('#newProdEntity').value.trim(),
      });
      await this._load();
    };

    this.querySelector('#addCon').onclick = async () => {
      const name = this.querySelector('#newConName').value.trim();
      const switchEntity = this.querySelector('#newConSwitch').value.trim();
      const powerEntity = this.querySelector('#newConPower').value.trim();
      const priorityNum = Number(this.querySelector('#newConPrio').value);
      const expectedNum = Number(this.querySelector('#newConExpected').value);
      const minNum = Number(this.querySelector('#newConMin').value);

      const payload = {
        name,
        switch_entity: switchEntity,
        power_entity: powerEntity,
        priority: Number.isFinite(priorityNum) ? Math.round(priorityNum) : 1,
        expected_power: Number.isFinite(expectedNum) ? expectedNum : 0,
        min_run_minutes: Number.isFinite(minNum) ? minNum : 0,
      };

      try {
        await this._ws('power_manager/add_consumer', payload);
        this.querySelector('#newConName').value = '';
        this.querySelector('#newConSwitch').value = '';
        this.querySelector('#newConPower').value = '';
        this.querySelector('#newConPrio').value = '';
        this.querySelector('#newConExpected').value = '';
        this.querySelector('#newConMin').value = '';
        await this._load();
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error('Failed to add consumer', err);
        alert(`Failed to add consumer: ${err?.message || err}`);
      }
    };
  }

  async _load() {
    const data = await this._ws('power_manager/get_config');
    this._data = data;

    const sensorEntities = await this._entitiesByDomain('sensor');
    const switchEntities = await this._entitiesByDomain('switch');

    this.querySelector('#sensorEntitiesList').innerHTML = sensorEntities
      .map((value) => `<option value="${value}"></option>`)
      .join('');
    this.querySelector('#switchEntitiesList').innerHTML = switchEntities
      .map((value) => `<option value="${value}"></option>`)
      .join('');

    const summaryTotalProduction = Number(data.total_production ?? 0);
    const summaryBaseLoad = Number(data.base_load ?? data.base_load_current_w ?? 0);
    const summarySurplus = Number(data.surplus ?? (summaryTotalProduction - summaryBaseLoad));
    const summaryRemainingSurplus = Number(data.remaining_surplus ?? summarySurplus);

    const fmt = (v) => Number.isFinite(v) ? v.toFixed(1) + ' W' : 'n/a';
    const surplusColor = summarySurplus >= 0 ? '#2e7d32' : '#b71c1c';

    this.querySelector('#summary').innerHTML = `
      <div class="summary-grid">
        <div class="stat">
          <div class="stat-label">Production</div>
          <div class="stat-value">${fmt(summaryTotalProduction)}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Base load</div>
          <div class="stat-value">${fmt(summaryBaseLoad)}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Surplus</div>
          <div class="stat-value" style="color:${surplusColor}">${fmt(summarySurplus)}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Remaining</div>
          <div class="stat-value">${fmt(summaryRemainingSurplus)}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Scan interval</div>
          <div class="stat-value">${data.scan_interval_seconds}s</div>
        </div>
        <div class="stat">
          <div class="stat-label">Status</div>
          <div class="stat-value">${data.running ? '🟢 Running' : '🔴 Stopped'}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Version</div>
          <div class="stat-value" style="font-size:.95em">${data.integration_version}</div>
        </div>
      </div>
      <div class="base-row">
        <label>Base load entity:</label>
        <input id="baseEntity" list="sensorEntitiesList" value="${data.base_load_entity || ''}" placeholder="sensor.house_total_power" />
        <button id="saveBase">Save</button>
        <button id="delBase" class="btn-del">Clear</button>
      </div>
    `;

    this.querySelector('#saveBase').onclick = async () => {
      await this._ws('power_manager/set_base', {
        base_load_entity: this.querySelector('#baseEntity').value.trim(),
      });
      await this._load();
    };

    this.querySelector('#delBase').onclick = async () => {
      await this._ws('power_manager/set_base', {
        base_load_entity: '',
      });
      await this._load();
    };

    const prodRows = this.querySelector('#prodRows');
    prodRows.innerHTML = '';
    (data.producers || []).forEach((p) => {
      const tr = document.createElement('tr');
      const current = (data.producer_states || {})[p.name]?.power ?? 'n/a';
      tr.innerHTML = `
        <td><input data-k="name" value="${p.name}" /></td>
        <td><input data-k="entity" list="sensorEntitiesList" value="${p.entity_id || ''}" placeholder="sensor.xxx" /></td>
        <td>${current}</td>
        <td style="white-space:nowrap">
          <button data-a="save">Save</button>
          <button data-a="del" class="btn-del">Del</button>
        </td>
      `;
      tr.querySelector('[data-a="save"]').onclick = async () => {
        const newName = tr.querySelector('[data-k="name"]').value.trim();
        const entity = tr.querySelector('[data-k="entity"]').value.trim();
        const payload = { name: p.name, entity_id: entity };
        if (newName && newName !== p.name) {
          payload.new_name = newName;
        }
        try {
          await this._ws('power_manager/update_producer', payload);
          await this._load();
        } catch (err) {
          // eslint-disable-next-line no-console
          console.error('Failed to save producer', err);
          alert(`Failed to save producer: ${err?.message || err}`);
        }
      };
      tr.querySelector('[data-a="del"]').onclick = async () => {
        await this._ws('power_manager/remove_producer', { name: p.name });
        await this._load();
      };
      prodRows.appendChild(tr);
    });

    const conditionByConsumer = {};
    let remainingSurplus = Number(data.surplus || 0);
    const nowTs = Date.now() / 1000;
    const sortedForDecision = [...(data.consumers || [])].sort(
      (a, b) => Number(a.priority ?? 999) - Number(b.priority ?? 999)
    );
    sortedForDecision.forEach((c) => {
      const state = (data.consumer_states || {})[c.name] || {};
      const mode = state.mode || c.mode || 'auto';
      const expected = Number(c.expected_power || 0);
      const holdActive = Number(state.on_until || 0) > nowTs;

      let reason = 'auto: off';
      if (mode === 'deactivated') {
        reason = 'deactivated';
      } else if (mode === 'force_on') {
        reason = 'force_on';
      } else if (mode === 'force_off') {
        reason = 'force_off';
      } else if (remainingSurplus >= expected) {
        reason = `auto: surplus ok (${remainingSurplus.toFixed(1)}W ≥ ${expected.toFixed(1)}W)`;
        remainingSurplus -= expected;
      } else if (holdActive) {
        const secLeft = Math.max(0, Math.round(Number(state.on_until || 0) - nowTs));
        reason = `auto: min-run hold (${secLeft}s left)`;
      } else {
        reason = `auto: no surplus (${remainingSurplus.toFixed(1)}W < ${expected.toFixed(1)}W)`;
      }

      conditionByConsumer[c.name] = reason;
    });

    const consRows = this.querySelector('#consRows');
    consRows.innerHTML = '';
    (data.consumers || []).forEach((c) => {
      const tr = document.createElement('tr');
      const currentWFromCoordinator = (data.consumer_states || {})[c.name]?.power;
      const powerEntityId = (c.power_entity || '').trim();
      const stateObj = powerEntityId ? this._hass?.states?.[powerEntityId] : undefined;
      const hassStateNum = this._toWatts(stateObj);
      const coordNum = Number(currentWFromCoordinator);
      const displayNum = Number.isFinite(hassStateNum)
        ? hassStateNum
        : (Number.isFinite(coordNum) ? coordNum : NaN);
      const currentW = Number.isFinite(displayNum) ? displayNum.toFixed(1) : 'n/a';
      const decision = conditionByConsumer[c.name] || 'n/a';

      // Status: coordinator decision + actual switch state
      const stateData = (data.consumer_states || {})[c.name] || {};
      const switchEntityId = c.switch_entity || '';
      const switchStateObj = switchEntityId ? this._hass?.states?.[switchEntityId] : null;
      const isDeactivated = (stateData.mode || 'auto') === 'deactivated';

      // Coordinator decision (large dot)
      const coordDecision = Boolean(stateData.is_on);
      const coordDotClass = isDeactivated
        ? 'dot-decision-deactivated'
        : (coordDecision ? 'dot-decision-on' : 'dot-decision-off');
      const coordTitle = isDeactivated ? 'Decision: deactivated' : (coordDecision ? 'Decision: ON' : 'Decision: OFF');

      // Actual switch state (small square dot)
      const actualOn = switchStateObj ? switchStateObj.state === 'on' : null;
      const actualDotClass = actualOn === null
        ? 'dot-actual-unknown'
        : (actualOn ? 'dot-actual-on' : 'dot-actual-off');
      const actualTitle = actualOn === null
        ? 'Switch: unknown'
        : (actualOn ? 'Switch: ON' : 'Switch: OFF');

      tr.innerHTML = `
        <td style="text-align:center">
          <div class="status-cell">
            <span class="status-dot dot-decision ${coordDotClass}" title="${coordTitle}"></span>
            <span class="status-dot dot-actual ${actualDotClass}" title="${actualTitle}"></span>
          </div>
        </td>
        <td><input data-k="name" value="${c.name}" /></td>
        <td><input data-k="switch" list="switchEntitiesList" value="${c.switch_entity || ''}" placeholder="switch.xxx" /></td>
        <td><input data-k="power" list="sensorEntitiesList" value="${c.power_entity || ''}" placeholder="sensor.xxx" /></td>
        <td style="text-align:right;padding-right:12px">${currentW}</td>
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
        <td style="font-size:12px;max-width:200px;white-space:normal">${decision}</td>
        <td style="white-space:nowrap">
          <button data-a="save">Save</button>
          <button data-a="del" class="btn-del">Del</button>
        </td>
      `;
      tr.querySelector('[data-k="mode"]').value = c.mode || 'auto';
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

        if (newName && newName !== c.name) {
          payload.new_name = newName;
        }

        try {
          await this._ws('power_manager/update_consumer', payload);
          await this._load();
        } catch (err) {
          // eslint-disable-next-line no-console
          console.error('Failed to save consumer', err);
          alert(`Failed to save consumer: ${err?.message || err}`);
        }
      };
      tr.querySelector('[data-a="del"]').onclick = async () => {
        await this._ws('power_manager/remove_consumer', { name: c.name });
        await this._load();
      };
      consRows.appendChild(tr);
    });
  }
}

customElements.define('power-manager-panel', PowerManagerPanel);

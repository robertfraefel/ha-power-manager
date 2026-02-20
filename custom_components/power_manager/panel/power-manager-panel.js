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
        .wrap { padding: 16px; font-family: var(--primary-font-family); max-width: 1600px; }
        h2 { margin: 4px 0 16px; font-size: 1.4em; }
        h3 { margin: 0 0 10px; font-size: 1em; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.7; }
        .card { border: 1px solid var(--divider-color); border-radius: 12px; padding: 16px; margin: 12px 0; background: var(--card-background-color, var(--primary-background-color)); }
        /* Summary grid */
        .summary-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }
        .stat { background: var(--secondary-background-color, rgba(0,0,0,.04)); border-radius: 8px; padding: 10px 14px; }
        .stat-label { font-size: 11px; opacity: 0.6; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
        .stat-value { font-size: 1.15em; font-weight: 600; }
        /* Tables */
        .table-wrap { overflow-x: auto; margin: 0 0 12px; }
        table { width: 100%; border-collapse: collapse; white-space: nowrap; }
        th { border-bottom: 2px solid var(--divider-color); padding: 6px 8px; text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.65; }
        td { border-bottom: 1px solid var(--divider-color); padding: 4px 6px; vertical-align: middle; }
        tr:last-child td { border-bottom: none; }
        /* Inputs inside tables — compact */
        td input, td select { padding: 4px 6px; min-width: 0; width: 100%; box-sizing: border-box; font-size: 13px; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--primary-background-color); color: var(--primary-text-color); }
        td input[type=number] { width: 70px; }
        td input[list] { width: 160px; }
        td select { width: 110px; }
        /* "Add" row inputs */
        .add-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; padding-top: 8px; border-top: 1px solid var(--divider-color); margin-top: 4px; }
        .add-row input, .add-row select { padding: 6px 8px; min-width: 0; font-size: 13px; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--primary-background-color); color: var(--primary-text-color); }
        .add-row input[list] { width: 180px; }
        .add-row input[type=number] { width: 90px; }
        .add-row input:not([list]):not([type=number]) { width: 120px; }
        /* Buttons */
        button { padding: 5px 10px; cursor: pointer; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--secondary-background-color, #f5f5f5); color: var(--primary-text-color); font-size: 13px; }
        button:hover { background: var(--primary-color, #03a9f4); color: #fff; border-color: transparent; }
        .btn-add { background: var(--primary-color, #03a9f4); color: #fff; border-color: transparent; font-weight: 600; }
        .btn-add:hover { opacity: 0.85; }
        .btn-del { color: var(--error-color, #db4437); }
        .btn-del:hover { background: var(--error-color, #db4437); color: #fff; }
        /* Badge for mode */
        .mode-badge { display: inline-block; padding: 2px 7px; border-radius: 10px; font-size: 11px; font-weight: 600; }
        .badge-auto { background: #e8f5e9; color: #2e7d32; }
        .badge-force_on { background: #fff8e1; color: #f57f17; }
        .badge-force_off { background: #fce4ec; color: #b71c1c; }
        .badge-deactivated { background: #eeeeee; color: #616161; }
        /* Base-load inline table */
        #baseTable { margin: 12px 0 0; }
        .small { opacity: 0.6; font-size: 11px; margin-top: 8px; }
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
                  <th>Name</th><th>Switch</th><th>Power sensor</th><th>W now</th>
                  <th>Prio</th><th>Exp W</th><th>Min min</th><th>Mode</th>
                  <th>Decision</th><th style="width:110px"></th>
                </tr>
              </thead>
              <tbody id="consRows"></tbody>
            </table>
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
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <span style="font-size:13px;opacity:.7">Base load entity:</span>
        <input id="baseEntity" list="sensorEntitiesList" value="${data.base_load_entity || ''}" placeholder="sensor.house_total_power" style="flex:1;min-width:200px;padding:5px 8px;border:1px solid var(--divider-color);border-radius:4px;font-size:13px;background:var(--primary-background-color);color:var(--primary-text-color)" />
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
      tr.innerHTML = `
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

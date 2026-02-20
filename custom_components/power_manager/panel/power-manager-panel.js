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

  _renderShell() {
    this.innerHTML = `
      <style>
        .wrap { padding: 16px; font-family: var(--primary-font-family); }
        h2, h3 { margin: 8px 0; }
        table { width: 100%; border-collapse: collapse; margin: 10px 0 20px; }
        th, td { border-bottom: 1px solid var(--divider-color); padding: 8px; text-align: left; vertical-align: middle; }
        .row { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0; align-items: center; }
        input, select { padding: 6px; min-width: 220px; max-width: 420px; }
        button { padding: 8px 10px; cursor: pointer; }
        .card { border: 1px solid var(--divider-color); border-radius: 12px; padding: 12px; margin: 12px 0; }
        .small { opacity: 0.8; font-size: 12px; }
      </style>
      <div class="wrap">
        <h2>Power Manager</h2>
        <datalist id="sensorEntitiesList"></datalist>
        <datalist id="switchEntitiesList"></datalist>
        <div id="summary" class="card">Loading...</div>

        <div class="card">
          <h3>Producers</h3>
          <table>
            <thead><tr><th>Name</th><th>Entity</th><th>Current W</th><th></th></tr></thead>
            <tbody id="prodRows"></tbody>
          </table>
          <div class="row">
            <input id="newProdName" placeholder="name" />
            <input id="newProdEntity" list="sensorEntitiesList" placeholder="sensor.xxx" />
            <button id="addProd">Add producer</button>
          </div>
        </div>

        <div class="card">
          <h3>Consumers</h3>
          <table>
            <thead>
              <tr><th>Name</th><th>Switch</th><th>Power sensor</th><th>Current W</th><th>Priority</th><th>Expected W</th><th>Min min</th><th>Mode</th><th>Condition state</th><th></th></tr>
            </thead>
            <tbody id="consRows"></tbody>
          </table>
          <div class="row">
            <input id="newConName" placeholder="name" />
            <input id="newConSwitch" list="switchEntitiesList" placeholder="switch.xxx" />
            <input id="newConPower" list="sensorEntitiesList" placeholder="sensor.xxx" />
            <input id="newConPrio" type="number" placeholder="priority" />
            <input id="newConExpected" type="number" placeholder="expected W" />
            <input id="newConMin" type="number" placeholder="min minutes" />
            <button id="addCon">Add consumer</button>
          </div>
        </div>

        <div class="small">Entity/switch/power fields are selectable dropdowns. Inline edits are saved per row.</div>
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
      await this._ws('power_manager/add_consumer', {
        name: this.querySelector('#newConName').value.trim(),
        switch_entity: this.querySelector('#newConSwitch').value.trim(),
        power_entity: this.querySelector('#newConPower').value.trim(),
        priority: Number(this.querySelector('#newConPrio').value || 1),
        expected_power: Number(this.querySelector('#newConExpected').value || 0),
        min_run_minutes: Number(this.querySelector('#newConMin').value || 0),
      });
      await this._load();
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

    this.querySelector('#summary').innerHTML = `
      <div><b>Version:</b> ${data.integration_version}</div>
      <div><b>Running:</b> ${data.running}</div>
      <div><b>Total production:</b> ${Number.isFinite(summaryTotalProduction) ? summaryTotalProduction.toFixed(1) : 'n/a'} W</div>
      <div><b>Base load:</b> ${Number.isFinite(summaryBaseLoad) ? summaryBaseLoad.toFixed(1) : 'n/a'} W</div>
      <div><b>Surplus:</b> ${Number.isFinite(summarySurplus) ? summarySurplus.toFixed(1) : 'n/a'} W</div>
      <div><b>Remaining surplus (after allocation):</b> ${Number.isFinite(summaryRemainingSurplus) ? summaryRemainingSurplus.toFixed(1) : 'n/a'} W</div>
      <div><b>Scan interval:</b> ${data.scan_interval_seconds}s</div>
      <table>
        <thead><tr><th>Name</th><th>Entity</th><th>Current W</th><th></th></tr></thead>
        <tbody>
          <tr>
            <td>Base load</td>
            <td>
              <input id="baseEntity" list="sensorEntitiesList" value="${data.base_load_entity || ''}" placeholder="sensor.house_total_power" />
            </td>
            <td>${data.base_load_current_w}</td>
            <td>
              <button id="saveBase">Save</button>
              <button id="delBase">Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
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
        <td>${p.name}</td>
        <td><input data-k="entity" list="sensorEntitiesList" value="${p.entity_id || ''}" placeholder="sensor.xxx" /></td>
        <td>${current}</td>
        <td>
          <button data-a="save">Save</button>
          <button data-a="del">Delete</button>
        </td>
      `;
      tr.querySelector('[data-a="save"]').onclick = async () => {
        const entity = tr.querySelector('[data-k="entity"]').value.trim();
        await this._ws('power_manager/update_producer', { name: p.name, entity_id: entity });
        await this._load();
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
      if (mode === 'force_on') {
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
      const hassStateNum = stateObj ? Number(stateObj.state) : NaN;
      const coordNum = Number(currentWFromCoordinator);
      const displayNum = Number.isFinite(hassStateNum)
        ? hassStateNum
        : (Number.isFinite(coordNum) ? coordNum : NaN);
      const currentW = Number.isFinite(displayNum) ? displayNum.toFixed(1) : 'n/a';
      tr.innerHTML = `
        <td><input data-k="name" value="${c.name}" /></td>
        <td><input data-k="switch" list="switchEntitiesList" value="${c.switch_entity || ''}" placeholder="switch.xxx" /></td>
        <td><input data-k="power" list="sensorEntitiesList" value="${c.power_entity || ''}" placeholder="sensor.xxx" /></td>
        <td>${currentW}</td>
        <td><input data-k="priority" type="number" value="${c.priority ?? 1}" /></td>
        <td><input data-k="expected" type="number" value="${c.expected_power ?? 0}" /></td>
        <td><input data-k="min" type="number" value="${c.min_run_minutes ?? 0}" /></td>
        <td>
          <select data-k="mode">
            <option value="auto">auto</option>
            <option value="force_on">force_on</option>
            <option value="force_off">force_off</option>
          </select>
        </td>
        <td>${conditionByConsumer[c.name] || 'n/a'}</td>
        <td>
          <button data-a="save">Save</button>
          <button data-a="del">Delete</button>
        </td>
      `;
      tr.querySelector('[data-k="mode"]').value = c.mode || 'auto';
      tr.querySelector('[data-a="save"]').onclick = async () => {
        await this._ws('power_manager/update_consumer', {
          name: c.name,
          new_name: tr.querySelector('[data-k="name"]').value.trim(),
          switch_entity: tr.querySelector('[data-k="switch"]').value.trim(),
          power_entity: tr.querySelector('[data-k="power"]').value.trim(),
          priority: Number(tr.querySelector('[data-k="priority"]').value || 1),
          expected_power: Number(tr.querySelector('[data-k="expected"]').value || 0),
          min_run_minutes: Number(tr.querySelector('[data-k="min"]').value || 0),
          mode: tr.querySelector('[data-k="mode"]').value,
        });
        await this._load();
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

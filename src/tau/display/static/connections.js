// TAU display: Connections panel.
// Shows everything TAU could connect to on each machine, grouped by place and kind.
// Plain JavaScript, text at least 15px, calm and roomy.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  let currentView = null;
  let filterText = '';
  let onlyOn = false;
  let busy = false;
  let filterTimer = null;
  const openDetails = new Set();
  const userClosed = new Set();
  const expandedMore = new Set();

  const kindDataMap = new Map();
  const bundleDataMap = new Map();

  function formatAt(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      if (!isNaN(d.getTime())) return d.toLocaleString();
    } catch (_) {}
    return String(iso);
  }

  function buildQuery() {
    const p = [];
    if (onlyOn) p.push('only_on=1');
    if (filterText) p.push('text=' + encodeURIComponent(filterText));
    return p.length ? '?' + p.join('&') : '';
  }

  async function loadView() {
    try {
      const r = await fetch('connections' + buildQuery()).then(res => res.json());
      if (r) render(r);
    } catch (_) {
      say('That didn’t reach me. Is TAU still running?', true);
    }
  }

  function say(text, bad) {
    const m = $('connmsg');
    if (m) {
      m.textContent = text || '';
      m.className = bad ? 'bad' : '';
    }
    if (text && window.__tauNote) window.__tauNote(text);
  }

  function showReply(r) {
    if (!r) return;
    if (r.say) {
      say(r.say, r.ok === false && !r.confirm);
    }
    if (r.view) {
      render(r.view);
    }
  }

  function askConfirm(question, onYes, onNo) {
    const box = $('connconfirm');
    box.innerHTML = `<p>${esc(question)}</p><button class="yes" id="confyes">Yes, change it</button><button id="confno">No</button>`;
    box.style.display = 'block';
    const yesBtn = box.querySelector('.yes');
    if (yesBtn) yesBtn.focus();
    box.onclick = (e) => {
      const t = e.target.closest('button');
      if (!t) return;
      if (t.classList.contains('yes') || t.id === 'confyes') {
        box.style.display = 'none';
        onYes();
      } else if (t.id === 'confno' || t.textContent.trim() === 'No') {
        box.style.display = 'none';
        if (onNo) onNo();
      }
    };
  }

  function findRow(machineName, rowId) {
    if (!currentView || !currentView.machines) return null;
    for (const m of currentView.machines) {
      if (m.machine !== machineName) continue;
      for (const p of (m.places || [])) {
        for (const k of (p.kinds || [])) {
          for (const r of (k.rows || [])) {
            if (r.type === 'one' && String(r.id) === String(rowId)) return r;
            if (r.type === 'bundle' && r.items) {
              for (const it of r.items) {
                if (String(it.id) === String(rowId)) return it;
              }
            }
          }
        }
      }
    }
    return null;
  }

  function getRowIds(row) {
    if (row.detail && Array.isArray(row.detail.ids) && row.detail.ids.length > 0) {
      return row.detail.ids.map(String);
    }
    return [String(row.id)];
  }

  async function switchLevel(machineName, row, newLevel, confirmed) {
    if (busy) return;
    busy = true;
    const ids = getRowIds(row);
    try {
      const r = await fetch('connections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          op: 'switch',
          machine: machineName,
          ids: ids,
          level: newLevel,
          confirm: !!confirmed,
          only_on: onlyOn,
          text: filterText,
        }),
      }).then(res => res.json());

      if (r.confirm) {
        askConfirm(r.confirm, () => switchLevel(machineName, row, newLevel, true), () => say('Okay, nothing changed.'));
        return;
      }
      showReply(r);
    } catch (_) {
      say('That didn’t reach me. Is TAU still running?', true);
    } finally {
      busy = false;
    }
  }

  async function doLook() {
    if (busy) return;
    busy = true;
    const lookBtn = $('connlook');
    const lookNowBtn = $('connlooknow');
    if (lookBtn) lookBtn.disabled = true;
    if (lookNowBtn) lookNowBtn.disabled = true;
    say('Looking. This takes up to a minute and changes nothing.');
    try {
      const r = await fetch('connections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ op: 'look', only_on: onlyOn, text: filterText }),
      }).then(res => res.json());
      showReply(r);
      // The counts are already on the summary line; don't say them twice.
      if (r && r.ok) say('I looked. Nothing was switched on or changed.');
    } catch (_) {
      say('That didn’t reach me. Is TAU still running?', true);
    } finally {
      busy = false;
      if (lookBtn) lookBtn.disabled = false;
      if (lookNowBtn) lookNowBtn.disabled = false;
    }
  }

  async function doAllOff(confirmed) {
    if (busy) return;
    busy = true;
    try {
      const r = await fetch('connections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ op: 'all_off', confirm: !!confirmed, only_on: onlyOn, text: filterText }),
      }).then(res => res.json());
      if (r.confirm) {
        askConfirm(r.confirm, () => doAllOff(true), () => say('Okay, nothing changed.'));
        return;
      }
      showReply(r);
    } catch (_) {
      say('That didn’t reach me. Is TAU still running?', true);
    } finally {
      busy = false;
    }
  }

  function renderRowOne(row, machineName, levels) {
    const ison = row.setting !== 'off';
    const allows = row.allows ? `<div class="connsub">Switching it on lets me: ${esc(row.allows)}</div>` : '';
    const needs = row.needs ? `<div class="connsub">Needs: ${esc(row.needs)}</div>` : '';
    const also = (row.detail && Array.isArray(row.detail.also_listed_as) && row.detail.also_listed_as.length > 0)
      ? `<div class="connsub">Also listed as: ${esc(row.detail.also_listed_as.join(', '))}</div>` : '';
    const evidence = row.evidence ? `<div class="connev">${esc(row.evidence)}</div>` : '';

    const btns = (levels || []).map(lv => {
      const on = String(row.setting) === String(lv.value);
      return `<button class="levelbtn${on ? ' on' : ''}" role="button" aria-pressed="${on}" data-level="${esc(lv.value)}" data-id="${esc(row.id)}" data-machine="${esc(machineName)}">${esc(lv.label)}</button>`;
    }).join('');

    return `<div class="connrow one${ison ? ' ison' : ''}" data-id="${esc(row.id)}" data-machine="${esc(machineName)}">
      <div class="rowhead">
        <b>${esc(row.name)}</b>
        <span class="state">${esc(row.state)}</span>
      </div>
      ${evidence}
      ${allows}
      ${needs}
      ${also}
      <div class="levels" role="group" aria-label="Level">${btns}</div>
    </div>`;
  }

  function renderRowsList(rows, machineName, levels, key, isExpanded) {
    const count = rows.length;
    if (count <= 60 || isExpanded) {
      return rows.map(r => {
        if (r.type === 'bundle') return renderBundle(r, machineName, levels, key);
        return renderRowOne(r, machineName, levels);
      }).join('');
    }
    const first60 = rows.slice(0, 60).map(r => {
      if (r.type === 'bundle') return renderBundle(r, machineName, levels, key);
      return renderRowOne(r, machineName, levels);
    }).join('');
    const remaining = count - 60;
    return first60 + `<button class="connmore" data-more="${esc(key)}">Show ${remaining} more</button>`;
  }

  function renderBundle(row, machineName, levels, parentKey) {
    const bKey = `b:${parentKey}|${row.name}`;
    bundleDataMap.set(bKey, { machine: machineName, bundle: row, levels });
    const isOpen = openDetails.has(bKey);
    const summaryText = esc(row.name) + (row.on > 0 ? `, ${row.on} on` : '');
    const content = isOpen ? renderRowsList(row.items || [], machineName, levels, bKey, expandedMore.has(bKey)) : '';
    return `<details class="bundle" data-key="${esc(bKey)}"${isOpen ? ' open' : ''}>
      <summary>${summaryText}</summary>
      <div class="bundlerows"${isOpen ? ' data-loaded="true"' : ''}>${content}</div>
    </details>`;
  }

  function loadDetailsRows(detailsEl) {
    const key = detailsEl.dataset.key;
    if (!key) return;
    if (key.startsWith('k:')) {
      const data = kindDataMap.get(key);
      if (!data) return;
      const container = detailsEl.querySelector('.kindrows');
      if (container && (!container.children.length || container.dataset.loaded !== 'true')) {
        container.innerHTML = renderRowsList(data.kind.rows || [], data.machine, data.levels, key, expandedMore.has(key));
        container.dataset.loaded = 'true';
      }
    } else if (key.startsWith('b:')) {
      const data = bundleDataMap.get(key);
      if (!data) return;
      const container = detailsEl.querySelector('.bundlerows');
      if (container && (!container.children.length || container.dataset.loaded !== 'true')) {
        container.innerHTML = renderRowsList(data.bundle.items || [], data.machine, data.levels, key, expandedMore.has(key));
        container.dataset.loaded = 'true';
      }
    }
  }

  function render(v) {
    currentView = v;
    const body = $('connbody');
    const prevScroll = body.scrollTop;

    // Sync open details from existing DOM
    body.querySelectorAll('details').forEach(d => {
      if (d.dataset.key) {
        if (d.open) openDetails.add(d.dataset.key);
        else openDetails.delete(d.dataset.key);
      }
    });

    kindDataMap.clear();
    bundleDataMap.clear();

    // Summary lines
    $('connsum').innerHTML = (v.summary || []).map(s => `<div class="sumline">${esc(s)}</div>`).join('');

    // Nothing looked yet
    if (v.looked === false) {
      body.innerHTML = `
        <p class="connempty">I haven't looked at this machine yet. A look finds every way I could connect; it changes nothing and switches nothing on.</p>
        <button id="connlooknow" class="connlarge">Look now</button>
      `;
      return;
    }

    const machines = v.machines || [];
    const totalFound = machines.reduce((acc, m) => acc + (m.found || 0), 0);
    const filterActive = Boolean(filterText) || onlyOn;

    if (machines.length === 0 || (filterActive && totalFound === 0)) {
      body.innerHTML = '<p class="connempty">Nothing matches.</p>';
      return;
    }

    const levels = v.levels || [];
    const html = machines.map(mach => {
      const mKey = `m:${mach.machine}`;
      const isMachOpen = openDetails.has(mKey) || (!userClosed.has(mKey) && (machines.length === 1 || filterActive));
      if (isMachOpen) openDetails.add(mKey);

      let infoHtml = '';
      if (mach.at) infoHtml += `<span>Looked ${esc(formatAt(mach.at))}</span>`;
      if (mach.note) infoHtml += `<span>${esc(mach.note)}</span>`;
      if (mach.stopped_early && mach.stopped_early.length > 0) {
        infoHtml += `<span>Part of the look stopped early: ${esc(mach.stopped_early.join(', '))}.</span>`;
      }
      const infoBlock = infoHtml ? `<div class="machinfo">${infoHtml}</div>` : '';

      const places = mach.places || [];
      const skipPlaceHeading = (places.length === 1 && (places[0].place === '' || places[0].place === mach.machine));

      const placesHtml = places.map(p => {
        const placeHeading = skipPlaceHeading ? '' : `<h3 class="placehead">${esc(p.title)}: ${p.found} found, ${p.on} on</h3>`;

        const kindsHtml = (p.kinds || []).map(k => {
          const kKey = `k:${mach.machine}|${p.place}|${k.kind}`;
          kindDataMap.set(kKey, { machine: mach.machine, kind: k, levels });

          const isKindOpen = openDetails.has(kKey) || (!userClosed.has(kKey) && Boolean(filterText));
          if (isKindOpen) openDetails.add(kKey);

          const kindContent = isKindOpen ? renderRowsList(k.rows || [], mach.machine, levels, kKey, expandedMore.has(kKey)) : '';

          return `<details class="kind" data-key="${esc(kKey)}"${isKindOpen ? ' open' : ''}>
            <summary>${esc(k.kind)}: ${k.found}, ${k.on} on</summary>
            <div class="kindrows"${isKindOpen ? ' data-loaded="true"' : ''}>${kindContent}</div>
          </details>`;
        }).join('');

        return placeHeading + kindsHtml;
      }).join('');

      return `<details class="mach" data-key="${esc(mKey)}"${isMachOpen ? ' open' : ''}>
        <summary>${esc(mach.machine)}: ${mach.found} found, ${mach.on} on</summary>
        ${infoBlock}
        ${placesHtml}
      </details>`;
    }).join('');

    body.innerHTML = html;
    body.scrollTop = prevScroll;
  }

  function onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.id === 'connclose') return close();
    if (t.id === 'connlook' || t.id === 'connlooknow') return doLook();
    if (t.id === 'connonly') return toggleOnlyOn();
    if (t.id === 'connalloff') return doAllOff();

    if (t.dataset.more) {
      const key = t.dataset.more;
      expandedMore.add(key);
      const parentContainer = t.parentElement;
      if (key.startsWith('k:')) {
        const data = kindDataMap.get(key);
        if (data && parentContainer) {
          parentContainer.innerHTML = renderRowsList(data.kind.rows || [], data.machine, data.levels, key, true);
        }
      } else if (key.startsWith('b:')) {
        const data = bundleDataMap.get(key);
        if (data && parentContainer) {
          parentContainer.innerHTML = renderRowsList(data.bundle.items || [], data.machine, data.levels, key, true);
        }
      }
      return;
    }

    if (t.classList.contains('levelbtn')) {
      if (t.classList.contains('on')) return;
      const rowId = t.dataset.id;
      const machName = t.dataset.machine;
      const targetLevel = t.dataset.level;
      const row = findRow(machName, rowId);
      if (row) switchLevel(machName, row, targetLevel, false);
      return;
    }
  }

  function onKey(e) {
    if (e.key === 'Escape') {
      close();
      e.stopPropagation();
      return;
    }
    e.stopPropagation();
  }

  function onFilterInput() {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      filterText = ($('connfilter').value || '').trim();
      loadView();
    }, 300);
  }

  function toggleOnlyOn() {
    onlyOn = !onlyOn;
    const btn = $('connonly');
    btn.setAttribute('aria-checked', String(onlyOn));
    btn.classList.toggle('on', onlyOn);
    loadView();
  }

  function open() {
    const p = $('conn');
    p.style.display = 'flex';
    $('connmsg').textContent = '';
    loadView();
  }

  function close() {
    $('conn').style.display = 'none';
  }

  function init() {
    const connEl = $('conn');
    if (connEl) {
      connEl.addEventListener('click', onClick);
      connEl.addEventListener('keydown', onKey);
    }
    const bodyEl = $('connbody');
    if (bodyEl) {
      bodyEl.addEventListener('toggle', (e) => {
        const d = e.target;
        if (d && d.tagName === 'DETAILS' && d.dataset.key) {
          if (d.open) {
            openDetails.add(d.dataset.key);
            userClosed.delete(d.dataset.key);
            loadDetailsRows(d);
          } else {
            openDetails.delete(d.dataset.key);
            userClosed.add(d.dataset.key);
          }
        }
      }, true);
    }
    const filterInput = $('connfilter');
    if (filterInput) filterInput.addEventListener('input', onFilterInput);
    const connBtn = $('connbtn');
    if (connBtn) connBtn.addEventListener('click', () => open());
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.TAUConnections = { open, close };
})();

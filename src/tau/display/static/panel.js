// TAU display: the Settings panel. One section at a time, drawn from rows the server sends
// (tau/settings.py). Every change is written at once and the status line says what changed.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let current = 'brain', busy = false;

  async function load(section) {
    const r = await fetch('settings?section=' + encodeURIComponent(section || current)).then(r => r.json()).catch(() => null);
    if (r && r.rows) render(r);
  }

  async function change(id, value, confirmed) {
    if (busy) return;
    busy = true; setBusy(true);
    try {
      const r = await fetch('settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ section: current, id, value, confirm: !!confirmed }) }).then(r => r.json());
      if (r.settings) render(r.settings);
      if (r.confirm) askConfirm(r.confirm, id, value);
      say(r.message || '', r.ok === false && !r.confirm);
    } catch (_) {
      say('That didn’t reach me. Is TAU still running?', true);
    } finally { busy = false; setBusy(false); }
  }

  function setBusy(on) {
    const p = $('settings');
    p.classList.toggle('busy', on);
    if (on) say('Working on it…');
  }

  function say(text, bad) {
    const m = $('setmsg');
    m.textContent = text; m.className = bad ? 'bad' : '';
    if (text && window.__tauNote) window.__tauNote(text);
  }

  function askConfirm(question, id, value) {
    const box = $('setconfirm');
    box.innerHTML = `<p>${esc(question)}</p><button class="yes" data-yes="1">Yes, allow it</button><button data-no="1">No</button>`;
    box.style.display = 'block';
    box.querySelector('[data-yes]').focus();
    box.onclick = (e) => {
      if (e.target.dataset.yes) { box.style.display = 'none'; change(id, value, true); }
      if (e.target.dataset.no) { box.style.display = 'none'; say('Okay, nothing changed.'); }
    };
  }

  function rowHTML(r, i) {
    const help = (r.help ? `<p class="help">${esc(r.help)}</p>` : '')
      + (r.does ? `<p class="help does"><b>What this lets me do:</b> ${esc(r.does)}</p>` : '')
      + (r.example ? `<p class="help example">${esc(r.example)}</p>` : '');
    const head = `<div class="label">${esc(r.label)}</div>`;
    const data = `data-row="${i}"`;
    switch (r.kind) {
      case 'info':
        return `<div class="row info${r.big ? ' big' : ''}" data-id="${esc(r.id)}">${head}<div class="value">${esc(r.value)}</div>${help}</div>`;
      case 'switch':
        return `<div class="row switch" data-id="${esc(r.id)}">${head}<button class="toggle${r.value ? ' on' : ''}" role="switch"
          aria-checked="${!!r.value}" ${data} data-act="toggle">${r.value ? 'On' : 'Off'}</button>${help}</div>`;
      case 'choice': {
        const opts = (r.options || []);
        const btns = opts.length ? opts.map((o, k) => `<button class="opt${String(o.value) === String(r.value) ? ' on' : ''}"
          aria-pressed="${String(o.value) === String(r.value)}" ${data} data-opt="${k}">${esc(o.label)}</button>`).join('')
          : `<div class="value">${esc(r.empty || 'Nothing to choose from.')}</div>`;
        return `<div class="row choice" data-id="${esc(r.id)}">${head}<div class="opts">${btns}</div>${help}</div>`;
      }
      case 'text':
        return `<div class="row text" data-id="${esc(r.id)}">${head}<div class="inline"><input ${data} value="${esc(r.value)}"
          placeholder="${esc(r.placeholder || '')}" aria-label="${esc(r.label)}"><button ${data} data-act="save">${esc(r.button || 'Save')}</button></div>${help}</div>`;
      case 'button':
        return `<div class="row button" data-id="${esc(r.id)}"><button class="action" ${data} data-act="press">${esc(r.label)}</button>${help}</div>`;
      case 'meter': {
        const pct = Math.round(Math.max(0, Math.min(1, r.value || 0)) * 100);
        return `<div class="row meter" data-id="${esc(r.id)}">${head}<div class="bar"><span style="width:${pct}%"></span></div>
          <div class="value">${pct}% — ${esc(r.text || '')}</div></div>`;
      }
      case 'list': {
        const items = (r.items || []).map((it, k) => `<li><div><b>${esc(it.label)}</b><span>${esc(it.detail || '')}</span></div>${
          (it.buttons || []).map((b, j) => `<button ${data} data-item="${k}" data-btn="${j}">${esc(b.label)}</button>`).join('')}</li>`).join('');
        return `<div class="row list" data-id="${esc(r.id)}">${head}${items ? `<ul>${items}</ul>` : `<div class="value">${esc(r.empty || 'None.')}</div>`}</div>`;
      }
    }
    return '';
  }

  let rows = [], liveTimer = null;
  function render(v) {
    current = v.section;
    rows = v.rows;
    $('setnav').innerHTML = v.sections.map(s => `<button data-sec="${s.id}" class="${s.id === v.section ? 'on' : ''}"
      aria-current="${s.id === v.section}">${esc(s.title)}</button>`).join('');
    $('settitle').textContent = v.title;
    $('setrows').innerHTML = rows.map(rowHTML).join('');
    // A live row (the microphone test) refreshes itself until it stops being live.
    clearTimeout(liveTimer);
    if (rows.some(r => r.live) && $('settings').style.display !== 'none') liveTimer = setTimeout(() => load(current), 150);
  }

  function onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.dataset.sec) { $('setconfirm').style.display = 'none'; say(''); return load(t.dataset.sec); }
    if (t.id === 'setclose') return close();
    const r = rows[+t.dataset.row];
    if (!r) return;
    if (t.dataset.act === 'toggle') return change(r.id, !r.value);
    if (t.dataset.opt !== undefined) return change(r.id, r.options[+t.dataset.opt].value);
    if (t.dataset.act === 'press') return change(r.id, null);
    if (t.dataset.act === 'save') return change(r.id, t.parentElement.querySelector('input').value);
    if (t.dataset.item !== undefined) {
      const b = r.items[+t.dataset.item].buttons[+t.dataset.btn];
      return change(b.id, b.value);
    }
  }

  function onKey(e) {
    if (e.key === 'Escape') { close(); e.stopPropagation(); return; }
    if (e.key === 'Enter' && e.target.tagName === 'INPUT' && e.target.dataset.row !== undefined) {
      e.preventDefault();
      const r = rows[+e.target.dataset.row];
      change(r.id, e.target.value);
    }
    e.stopPropagation();      // Space and / belong to the panel while it's open
  }

  function open(section) {
    const p = $('settings');
    p.style.display = 'flex';
    $('setmsg').textContent = '';
    load(section || current);
  }
  function close() { $('settings').style.display = 'none'; }

  document.addEventListener('DOMContentLoaded', () => {
    $('settings').addEventListener('click', onClick);
    $('settings').addEventListener('keydown', onKey);
    $('setbtn').addEventListener('click', () => open());
  });
  window.TAUSettings = { open, close, load, change };
})();

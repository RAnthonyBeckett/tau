// TAU display: Add a folder, Add an app, Set up work, My work, Check TAU (round 6).
// One question per step, Back and Cancel on every step. Every action goes to POST /work,
// the same path voice and typing use; nothing here decides anything on its own.
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let W = { projects: [], connections: [], jobs: [], active: {} };
  let card = null, stack = [], lastOpen = 0, busy = false;
  const saved = {};   // progress kept when a flow is dismissed

  async function op(body) {
    if (busy) return { ok: false, say: 'One moment, still working on the last step.' };
    busy = true; msg('Working on it…');
    try {
      const r = await fetch('work', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) }).then(r => r.json());
      msg(r.say || '', r.ok === false);
      return r;
    } catch (_) { msg('That didn’t reach me. Is TAU still running?', true); return { ok: false }; }
    finally { busy = false; }
  }
  async function refresh() { const r = await fetch('work').then(r => r.json()).catch(() => null); if (r) W = r; ctx(); }
  function msg(t, bad) { const m = $('workmsg'); m.textContent = t || ''; m.className = bad ? 'bad' : ''; if (t && window.__tauNote) window.__tauNote(t); }
  function show(title, html, back) {
    $('worktitle').textContent = title; $('workbody').innerHTML = html;
    $('work').classList.add('open'); $('workback').style.visibility = back ? 'visible' : 'hidden';
    const f = $('workbody').querySelector('button, input, textarea'); if (f) f.focus();
  }
  function step(fn) { stack.push(fn); fn(); }
  function back() { stack.pop(); const fn = stack[stack.length - 1]; if (fn) fn(); else close(); }
  function close() { $('work').classList.remove('open'); stack = []; }
  const proj = (id) => (W.projects || []).find(p => p.id === id);

  function ctx() {
    const p = proj((W.active || {}).project);
    const run = (W.jobs || []).filter(j => ['Running', 'Starting', 'Waiting for you', 'Status unknown'].includes(j.state)).length;
    $('ctx').innerHTML = p ? `Project: <b>${esc(p.name)}</b> on ${esc(p.machine)}` + (run ? ` · ${run} job${run > 1 ? 's' : ''} going` : '') : '';
    $('workcount').textContent = run ? `: ${run}` : '';
  }

  // -- Add a folder -------------------------------------------------------------------
  function folderStart() {
    show('Add a folder', `<p class="q">Which folder should I work with?</p>
      <p class="ex">For example your comic project, or a folder of reference notes. I won't look anywhere else.</p>
      <button class="yes" id="fpick">Choose a folder…</button><button id="fbrowse">Browse here</button>
      <details><summary>Or paste the folder's path</summary><input id="fpath" placeholder="Paste the full path of the folder">
      <button id="fpaste">Use this path</button></details>
      ${(W.projects || []).length ? '<p class="soft">Already added:</p>' + W.projects.map(p => `<div class="item"><b>${esc(p.name)}</b>
        <span class="soft">${esc(p.access === 'change' ? 'workers may change files' : 'read only')} · ${esc(p.machine)}</span><br>
        <button data-use="${p.id}">Use this one</button><button data-rules="${p.id}">What its instructions say</button>
        <button data-files="${p.id}">Read a file</button><button data-forget="${p.id}">Forget it</button>
        <details><summary>Full path</summary><span class="soft">${esc(p.path)}</span></details></div>`).join('') : ''}`, stack.length > 1);
    const body = $('workbody');
    body.querySelector('#fpick').onclick = async () => {
      const r = await op({ op: 'pick_folder' });
      if (r.path) step(() => folderAccess(r.path));
      else if (!r.native) { msg('This window has no system folder dialog, so browse here instead.'); step(() => browseDirs('')); }
      else msg('Nothing chosen; nothing changed.');
    };
    body.querySelector('#fbrowse').onclick = () => step(() => browseDirs(''));
    body.querySelector('#fpaste').onclick = () => { const v = body.querySelector('#fpath').value.trim(); if (v) step(() => folderAccess(v)); };
    body.querySelectorAll('[data-use]').forEach(b => b.onclick = async () => { await op({ op: 'use_folder', id: b.dataset.use }); await refresh(); msg('Okay, working in ' + proj(b.dataset.use).name + '.'); });
    body.querySelectorAll('[data-forget]').forEach(b => b.onclick = async () => { await op({ op: 'remove_folder', id: b.dataset.forget }); await refresh(); folderStart(); });
    body.querySelectorAll('[data-rules]').forEach(b => b.onclick = () => step(() => rulesView(b.dataset.rules)));
    body.querySelectorAll('[data-files]').forEach(b => b.onclick = () => step(() => filesView(b.dataset.files, '')));
  }
  async function browseDirs(path) {
    const r = await op({ op: 'list_dir', path });
    if (!r.ok) return;
    show('Add a folder', `<p class="q">${r.path ? esc(r.path) : 'Start from:'}</p>
      ${r.path ? '<button class="yes" id="bhere">Use this folder</button>' + (r.parent ? '<button id="bup">Up one level</button>' : '') : ''}
      ${r.dirs.map((d, i) => `<div class="item"><button data-i="${i}">${esc(d.name)}</button></div>`).join('') || '<p class="soft">No folders here.</p>'}`, true);
    const body = $('workbody');
    body.querySelectorAll('[data-i]').forEach(b => b.onclick = () => browseDirs(r.dirs[+b.dataset.i].path));
    if (r.path) body.querySelector('#bhere').onclick = () => step(() => folderAccess(r.path));
    if (r.parent) body.querySelector('#bup').onclick = () => browseDirs(r.parent);
  }
  function folderAccess(path) {
    saved.folder = path;
    show('Add a folder', `<p class="q">What should workers be allowed to do in this folder?</p>
      <p class="soft">${esc(path)}</p>
      <button class="yes" data-a="read">Read for context</button><button data-a="change">Let a worker change files here</button>
      <p class="ex">Reading is the safe start. You can change this later. Keys, passwords and hidden settings folders are never read.</p>
      <details><summary>What kind of folder is it?</summary>
        <label><input type="radio" name="role" value="project" checked> A project I work on</label><br>
        <label><input type="radio" name="role" value="reference"> Reference material</label><br>
        <label><input type="radio" name="role" value="output"> Where results should go</label></details>`, true);
    $('workbody').querySelectorAll('[data-a]').forEach(b => b.onclick = async () => {
      const role = ($('workbody').querySelector('input[name=role]:checked') || {}).value || 'project';
      const r = await op({ op: 'add_folder', path, access: b.dataset.a, role });
      if (r.ok) { delete saved.folder; await refresh(); step(() => rulesView(r.project.id, r.say)); }
    });
  }
  async function rulesView(id, said) {
    const r = await op({ op: 'rules', id });
    const p = proj(id) || {};
    show(p.name || 'Instructions', `${said ? `<p class="q">${esc(said)}</p>` : ''}
      ${(r.found || []).map(f => `<div class="item"><b>${esc(f.name)}</b>${f.truncated ? ' <span class="state">long: workers read the whole file</span>' : ''}
        <pre>${esc(f.text.slice(0, 1500))}</pre><span class="soft">${esc(f.path)}</span></div>`).join('') || '<p>I didn’t find any instructions here. That’s fine; workers just get your request.</p>'}
      ${(r.missing || []).length ? `<p class="soft">Not found: ${esc(r.missing.join(', '))}</p>` : ''}
      <button class="yes" id="rset">Set up work here</button><button id="rdone">Done</button>`, true);
    $('workbody').querySelector('#rset').onclick = () => { stack = []; step(setupStart); };
    $('workbody').querySelector('#rdone').onclick = close;
  }
  async function filesView(id, rel) {
    const r = await op({ op: 'browse', id, rel });
    if (!r.ok) return;
    show((proj(id) || {}).name || 'Files', `<p class="q">${rel ? esc(rel) : 'Pick a file to read or attach'}</p>
      ${r.items.map((it, i) => `<div class="item"><button data-i="${i}">${it.dir ? '📁 ' : ''}${esc(it.name)}</button></div>`).join('') || '<p class="soft">Nothing here.</p>'}
      ${r.hidden ? `<p class="soft">${r.hidden} private or hidden item(s) left out.</p>` : ''}`, true);
    $('workbody').querySelectorAll('[data-i]').forEach(b => b.onclick = async () => {
      const it = r.items[+b.dataset.i];
      if (it.dir) return filesView(id, it.rel);
      const f = await op({ op: 'read_file', id, rel: it.rel });
      show(it.name, `${f.ok ? `<pre>${esc(f.text)}</pre>${f.truncated ? '<p class="soft">Only the start is shown.</p>' : ''}` : `<p>${esc(f.say)}</p>`}
        <button class="yes" id="attach">Attach to my next job</button>`, true);
      $('workbody').querySelector('#attach').onclick = () => { saved.files = Array.from(new Set([...(saved.files || []), it.rel])); msg(`I'll attach ${it.name} to the next job.`); };
    });
  }

  // -- Add an app ---------------------------------------------------------------------
  async function appStart() {
    show('Add an app', '<p class="q">Looking for workers and apps on this PC…</p>', stack.length > 1);
    const r = await op({ op: 'detect' });
    const cs = r.connections || [];
    show('Add an app', `<p class="q">Which one should I connect?</p>
      <p class="ex">A worker gets your request with the project's instructions and sends back its result.</p>
      ${cs.map(c => `<div class="item"><b>${esc(c.label)}</b><span class="state">${esc(c.connected && c.state !== 'Unavailable' ? (c.state === 'Found' ? 'Connected' : c.state) : c.state)}</span>
        <p>${esc(c.can)}</p><p class="soft">${esc(c.why)}</p>
        ${c.state === 'Unavailable' ? `<p>Next: ${esc(c.next)}</p><button data-c="${c.id}">Check again</button>` :
          `<button class="yes" data-c="${c.id}">${c.connected ? 'Check again' : 'Connect'}</button>`}
        <details><summary>Details</summary><span class="soft">${esc(c.path || '')} ${esc(c.version || '')} · ${esc(c.machine)}</span></details></div>`).join('')}
      ${(W.apps || []).map(a => `<div class="item"><b>${esc(a.name)}</b><span class="state">${esc(a.state)}</span>
        <p class="soft">${esc(a.why)}</p>${a.actions.map(x => `<button data-app="${a.id}" data-act="${esc(x.id)}">${esc(x.label)}</button>`).join('')}
        <button data-ast="${a.id}">Status</button><button data-afg="${a.id}">Forget it</button></div>`).join('')}
      <div class="item"><b>Other apps</b><p class="soft">Opening an app isn't the same as controlling it. TAU can drive an app
        only if it offers TAU's connection (it lists what TAU may ask and reports each result).</p>
        <details><summary>Connect an app by its address</summary><input id="aaddr" placeholder="127.0.0.1:PORT">
        <button id="aconn">Connect</button></details></div>`, stack.length > 1);
    const ab = $('workbody');
    ab.querySelectorAll('[data-app]').forEach(b => b.onclick = async () => { await op({ op: 'app_do', id: b.dataset.app, action: b.dataset.act }); });
    ab.querySelectorAll('[data-ast]').forEach(b => b.onclick = async () => { await op({ op: 'app_status', id: b.dataset.ast }); });
    ab.querySelectorAll('[data-afg]').forEach(b => b.onclick = async () => { await op({ op: 'app_forget', id: b.dataset.afg }); await refresh(); appStart(); });
    ab.querySelector('#aconn').onclick = async () => { const r = await op({ op: 'app_connect', address: ab.querySelector('#aaddr').value }); if (r.ok) { await refresh(); appStart(); } };
    $('workbody').querySelectorAll('[data-c]').forEach(b => b.onclick = async () => { await op({ op: 'connect', id: b.dataset.c }); await refresh(); appStart(); });
  }

  // -- Set up work --------------------------------------------------------------------
  function setupStart() {
    const last = [...document.querySelectorAll('#msgs .you')].pop();
    show('Set up work', `<p class="q">What should the worker do?</p>
      <p class="ex">Say it the way you'd say it to me, for example "Tidy the chapter 3 notes and list what's missing."</p>
      <textarea id="wreq" rows="4">${esc(saved.request || (last ? last.textContent : ''))}</textarea>
      <button class="yes" id="wnext">Next</button>`, stack.length > 1);
    $('workbody').querySelector('#wnext').onclick = async () => {
      const request = $('workbody').querySelector('#wreq').value.trim();
      if (!request) return msg('Tell me what to do first.', true);
      saved.request = request;
      const r = await op({ op: 'draft', request, files: saved.files || [] });
      if (r.ok) { card = r.card; step(cardView); }
    };
  }
  function cardView() {
    const c = card;
    if (c.ask === 'project') {
      return show('Set up work', `<p class="q">Which project is this for?</p>
        ${(W.projects || []).map(p => `<button data-p="${p.id}">${esc(p.name)}</button>`).join('') || '<p>You haven’t added a folder yet.</p>'}
        <button id="addf">Add a folder</button>`, true), wire();
    }
    if (c.ask === 'worker') {
      const ok = (W.connections || []).filter(x => x.connected && x.state !== 'Unavailable');
      return show('Set up work', `<p class="q">Who should do it?</p>
        ${ok.map(x => `<button data-w="${x.id}">${esc(x.label)}</button>`).join('') || '<p>No worker is connected yet.</p>'}
        <button id="adda">Add an app</button>`, true), wire();
    }
    const f = c.fields || {};
    show('Set up work', `<p class="q">${esc(c.summary || '')}</p>
      ${c.warning ? `<p class="soft">${esc(c.warning)}</p>` : ''}
      ${c.unenforced ? `<p><label><input type="checkbox" id="wack" style="width:auto"> I understand: ${esc(c.boundary)}</label></p>` : ''}
      <button class="yes" id="wsend">Send</button><button id="wcancel">Cancel</button><button id="wchange">Change the request</button>
      <details><summary>Details</summary><dl>${Object.entries(f).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}
        <dt>Boundary</dt><dd>${esc(c.boundary || '')}</dd></dl>
        <p>Use a different worker: ${(W.connections || []).filter(x => x.connected && x.state !== 'Unavailable').map(x => `<button data-w="${x.id}">${esc(x.label)}</button>`).join('')}</p>
        <p>Use a different project: ${(W.projects || []).map(p => `<button data-p="${p.id}">${esc(p.name)}</button>`).join('')}</p></details>`, true);
    wire();
  }
  function wire() {
    const body = $('workbody');
    body.querySelectorAll('[data-p]').forEach(b => b.onclick = async () => { const r = await op({ op: 'amend', id: card.id, project: b.dataset.p }); if (r.ok) { card = r.card; cardView(); } });
    body.querySelectorAll('[data-w]').forEach(b => b.onclick = async () => { const r = await op({ op: 'amend', id: card.id, worker: b.dataset.w }); if (r.ok) { card = r.card; msg(card.receipt_pref || ''); cardView(); } });
    const q = (s) => body.querySelector(s);
    if (q('#addf')) q('#addf').onclick = () => step(folderStart);
    if (q('#adda')) q('#adda').onclick = () => step(appStart);
    if (q('#wchange')) q('#wchange').onclick = back;
    if (q('#wcancel')) q('#wcancel').onclick = async () => { await op({ op: 'cancel', id: card.id }); delete saved.request; close(); };
    if (q('#wsend')) q('#wsend').onclick = async (e) => {
      e.target.disabled = true;            // one click, one job
      const r = await op({ op: 'send', id: card.id, approval: card.approval, accept_unenforced: !!(q('#wack') && q('#wack').checked) });
      if (r.ok) { delete saved.request; delete saved.files; await refresh(); stack = []; step(myWork); } else e.target.disabled = false;
    };
  }

  // -- My work ------------------------------------------------------------------------
  async function myWork() {
    await refresh();
    const jobs = (W.jobs || []).filter(j => !j.dismissed && j.state !== 'Cancelled' || ['Running'].includes(j.state));
    show('My work', jobs.length ? jobs.map(j => {
      const r = j.result || {}; const p = proj(j.project) || {};
      return `<div class="item"><b>${esc(j.request.split('\n').pop().slice(0, 90))}</b><span class="state">${esc(j.state)}</span>
        <p class="soft">${esc(p.name || '')}${j.note ? ' · ' + esc(j.note) : ''}</p>
        ${r.summary ? `<pre>${esc(r.summary)}</pre>` : ''}${r.issue ? `<p>${esc(r.issue)}</p>` : ''}
        ${r.question ? `<p>It asked: ${esc(r.question)} Answer by typing “tell it …”.</p>` : ''}
        ${j.state === 'Running' ? `<button data-x="${j.id}">Cancel</button><span class="soft">Files it already changed stay changed.</span>` : ''}
        ${j.state === 'Draft' ? `<button data-o="${j.id}">Open the draft</button>` : ''}
        ${['Failed', 'Finished', 'Status unknown'].includes(j.state) ? `<button data-r="${j.id}">Retry</button>` : ''}
        ${['Failed', 'Finished', 'Cancelled'].includes(j.state) ? `<button data-d="${j.id}">Dismiss</button>` : ''}
        ${r.log ? `<details><summary>Technical details</summary><span class="soft">Log: ${esc(r.log)}${r.where ? '<br>Result: ' + esc(r.where) : ''}</span></details>` : ''}</div>`;
    }).join('') : '<p class="q">No work yet.</p><p class="ex">Ask me, for example “have Claude work on this”, or use Set up work.</p><button class="yes" id="mset">Set up work</button>', stack.length > 1);
    const body = $('workbody');
    if (body.querySelector('#mset')) body.querySelector('#mset').onclick = () => step(setupStart);
    body.querySelectorAll('[data-x]').forEach(b => b.onclick = async () => { await op({ op: 'cancel', id: b.dataset.x }); myWork(); });
    body.querySelectorAll('[data-d]').forEach(b => b.onclick = async () => { await op({ op: 'dismiss', id: b.dataset.d }); myWork(); });
    body.querySelectorAll('[data-r]').forEach(b => b.onclick = async () => { const r = await op({ op: 'retry', id: b.dataset.r }); if (r.ok) { card = r.card; step(cardView); } });
    body.querySelectorAll('[data-o]').forEach(b => b.onclick = async () => { const r = await op({ op: 'card', id: b.dataset.o }); if (r.ok) { card = r.card; step(cardView); } });
  }

  // -- Check TAU ----------------------------------------------------------------------
  async function checkView() {
    show('Check TAU and connections', '<p class="q">Checking… (read-only; nothing listens, plays or costs money)</p>', stack.length > 1);
    const r = await op({ op: 'check' });
    if (!r.ok) return;
    show('Check TAU and connections', `${(r.checks || []).map(c => `<div class="item"><b>${esc(c.what)}</b><span class="state">${esc(c.state)}</span>
      <p>${esc(c.say)}</p>${c.next ? `<p class="soft">Next: ${esc(c.next)}</p>` : ''}${c.flow ? `<button data-flow2="${c.flow}">Fix this</button>` : ''}</div>`).join('')}
      <p class="soft">${esc(r.not_checked || '')}</p>`, stack.length > 1);
    $('workbody').querySelectorAll('[data-flow2]').forEach(b => b.onclick = () => open(b.dataset.flow2));
  }

  const FLOWS = { folder: folderStart, app: appStart, setup: setupStart, mywork: myWork, check: checkView };
  async function open(flow) { await refresh(); stack = []; step(FLOWS[flow] || myWork); }
  window.__tauOpenWork = open;
  window.__tauWork = (s) => {
    if (s.work) { W = s.work; ctx(); }
    if (s.open_work && s.open_work.at !== lastOpen) { lastOpen = s.open_work.at; open(s.open_work.flow); }
    if (s.work_card && (!card || s.work_card.id !== card.id || s.work_card.approval !== card.approval) && s.work_card.state === 'Draft') {
      card = s.work_card; stack = []; step(cardView);
    }
  };
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('#workbar [data-flow]').forEach(b => b.onclick = () => open(b.dataset.flow));
    $('workback').onclick = back; $('workclose').onclick = close;
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('work').classList.contains('open')) close(); });
    refresh();
  });
})();

/* CODEPHANTOM AI — Projects tab (X-Ray, Time Machine, Mission Mode, Impact Radar, Memory, Files) + the Evidence drawer. */
(() => {
  const CP = window.CP, V = window.CPViz; if (!CP || !V) return;
  const { $, el, els, ed, flash, jget, jset, usage } = CP;
  const XH = { 'X-Requested-With': 'codephantom' }, JH = { 'Content-Type': 'application/json', ...XH };
  const EXT_LANG = { py: 'Python', js: 'JavaScript', jsx: 'JavaScript', ts: 'TypeScript', tsx: 'TypeScript', java: 'Java', c: 'C', h: 'C', cpp: 'C++', cc: 'C++', cs: 'C#', go: 'Go', rs: 'Rust', php: 'PHP', rb: 'Ruby', kt: 'Kotlin', swift: 'Swift', sh: 'Bash', sql: 'SQL' };
  const EXT_OF = { Python: 'py', JavaScript: 'js', TypeScript: 'ts', Java: 'java', C: 'c', 'C++': 'cpp', 'C#': 'cs', Go: 'go', Rust: 'rs', PHP: 'php', Ruby: 'rb', Kotlin: 'kt', Swift: 'swift', Bash: 'sh', SQL: 'sql' };
  const langOfPath = p => EXT_LANG[(p.split('.').pop() || '').toLowerCase()] || null;

  async function api(url, body, method) {
    const res = await fetch(url, { method: method || (body === undefined ? 'GET' : 'POST'), headers: body === undefined ? XH : JH, body: body === undefined ? undefined : JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.error || `Request failed (${res.status}).`);
    return d;
  }
  /* NDJSON progress stream: calls onEvent for every non-final event, resolves with the "done" payload. */
  async function stream(url, body, onEvent) {
    const res = await fetch(url, { method: 'POST', headers: JH, body: JSON.stringify(body || {}) });
    if (!res.ok || !res.body) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `Request failed (${res.status}).`); }
    const reader = res.body.getReader(), dec = new TextDecoder(); let buf = '', final = null;
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (!line) continue;
        let ev; try { ev = JSON.parse(line); } catch { continue; }
        if (ev.error) throw new Error(ev.error);
        if (ev.done) final = ev.done; else if (onEvent) onEvent(ev);
      }
    }
    if (final === null) throw new Error('The operation ended without a result. Try again.');
    return final;
  }
  const btn = (label, cls, fn) => { const b = el('button', cls || 'ghostbtn', label); b.type = 'button'; if (fn) b.addEventListener('click', () => fn(b)); return b; };
  const badge = (text, cls) => el('span', 'bdg ' + (cls || ''), text);
  const sec = (title, ...nodes) => { const s = el('div', 'lsec'); if (title) s.appendChild(el('h4', '', title)); nodes.forEach(n => n && s.appendChild(n)); return s; };
  const note = (t, cls) => el('div', 'tinfo ' + (cls || ''), t);
  const busy = async (b, label, fn) => { const old = b.textContent; b.disabled = true; b.textContent = label; try { return await fn(); } finally { b.disabled = false; b.textContent = old; } };
  const fail = (box, e) => { box.innerHTML = ''; box.appendChild(el('div', 'lerr', e.message || String(e))); };
  window.CPLab = { api, stream, btn, badge, sec, note, busy, fail, langOfPath, EXT_OF };

  /* ═════════ Evidence drawer ═════════ */
  const EV = { list: [], filter: 'all' }, SL = { verified: '✔ Verified', static: '◆ Static analysis', ai: '✦ AI prediction', uncertain: '? Uncertain' };
  function drawEv() {
    const f = $('#evFilters'), list = $('#evList'); f.innerHTML = ''; list.innerHTML = '';
    const counts = { all: EV.list.length }; Object.keys(SL).forEach(k => { counts[k] = EV.list.filter(x => x.status === k).length; });
    $('#evSummary').textContent = `${counts.verified} verified · ${counts.static} static · ${counts.ai} AI · ${counts.uncertain} uncertain`;
    ['all', ...Object.keys(SL)].forEach(k => { const b = el('button', EV.filter === k ? 'on' : '', `${k === 'all' ? 'All' : SL[k]} (${counts[k]})`); b.type = 'button'; b.addEventListener('click', () => { EV.filter = k; drawEv(); }); f.appendChild(b); });
    const shown = EV.list.filter(x => EV.filter === 'all' || x.status === EV.filter);
    if (!shown.length) list.appendChild(note('Nothing here yet. Run code, a Lab, or a project feature — every important result lands here with its evidence.'));
    shown.slice(0, 80).forEach(x => {
      const c = el('div', 'evitem s-' + x.status); c.append(el('span', 'evb', SL[x.status] || x.status), el('div', 'evc', x.claim), el('small', '', `${x.source || ''} · ${new Date(x.ts).toLocaleTimeString()}`));
      if (x.items && x.items.length) { const ul = el('ul'); x.items.slice(0, 8).forEach(it => { const li = el('li'); li.append(el('b', '', it.type), el('code', '', it.ref), document.createTextNode(' ' + it.text)); ul.appendChild(li); }); c.appendChild(ul); }
      list.appendChild(c);
    });
  }
  CP.evidence = { add(items) { (items || []).forEach(i => EV.list.unshift({ ...i, ts: Date.now() })); EV.list = EV.list.slice(0, 200); $('#evCount').textContent = EV.list.length; if (!$('#evDrawer').hidden) drawEv(); }, all: () => EV.list.slice() };
  $('#evBtn').addEventListener('click', () => { const d = $('#evDrawer'); d.hidden = !d.hidden; if (!d.hidden) drawEv(); });
  $('#evClose').addEventListener('click', () => { $('#evDrawer').hidden = true; });
  $('#evClear').addEventListener('click', () => { EV.list = []; $('#evCount').textContent = '0'; drawEv(); });
  CP.extraCommands.push({ label: 'Open the Evidence panel', run: () => { $('#evDrawer').hidden = false; drawEv(); } });

  /* ═════════ Projects ═════════ */
  const P = { list: [], id: null, d: null, sub: 'xray', xray: null, built: false, missions: {} };
  const root = $('#prjRoot'); let sel, body, info;
  const SUBS = [['xray', 'X-Ray'], ['time', 'Time Machine'], ['mission', 'Mission Mode'], ['impact', 'Impact Radar'], ['memory', 'Memory'], ['files', 'Files']];

  function shell() {
    root.innerHTML = '';
    const bar = el('div', 'prjbar'); sel = el('select'); sel.id = 'prjSel'; sel.addEventListener('change', () => selectProject(sel.value).catch(e => flash(e.message)));
    const zipIn = el('input'); zipIn.type = 'file'; zipIn.accept = '.zip'; zipIn.hidden = true; zipIn.id = 'prjZipIn';
    zipIn.addEventListener('change', async () => { const f = zipIn.files[0]; zipIn.value = ''; if (!f) return; const fd = new FormData(); fd.append('zip', f); fd.append('name', f.name.replace(/\.zip$/i, ''));
      try { flash('Reading the ZIP…'); const res = await fetch('/api/projects', { method: 'POST', headers: XH, body: fd }); const d = await res.json(); if (!res.ok) throw new Error(d.error); flash(`Project created: ${d.files} files${d.skipped ? `, ${d.skipped} skipped (binary/ignored)` : ''}`); await loadList(d.id); } catch (e) { flash(e.message); } });
    const bZip = btn('Upload ZIP', 'ghostbtn', () => zipIn.click()); bZip.id = 'prjZip';
    const bGit = btn('Import from GitHub', 'ghostbtn', async b => { const ref = prompt('Public GitHub repository (owner/repo or full URL):', ''); if (!ref) return;
      await busy(b, 'Importing…', async () => { try { const d = await api('/api/projects', { github: ref }); flash(`Imported ${d.name}: ${d.files} files`); await loadList(d.id); } catch (e) { flash(e.message); } }); }); bGit.id = 'prjGit';
    const bEd = btn('From editor', 'ghostbtn', async () => { const code = ed.get(); if (!code.trim()) return flash('The editor is empty.');
      const path = prompt('File name for this code:', 'main.' + (EXT_OF[els.lang.value] || 'txt')); if (!path) return;
      try { const d = await api('/api/projects', { name: 'Editor project', files: { [path]: code } }); flash('Project created from the editor'); await loadList(d.id); } catch (e) { flash(e.message); } }); bEd.id = 'prjEditor';
    const bDel = btn('Delete', 'ghostbtn', async () => { if (!P.id || !confirm('Delete this project and all its versions?')) return; try { await api('/api/projects/' + P.id, undefined, 'DELETE'); P.id = null; P.d = null; await loadList(); } catch (e) { flash(e.message); } }); bDel.id = 'prjDel';
    info = el('span', 'prjinfo'); bar.append(sel, bZip, zipIn, bGit, bEd, bDel, info);
    const tabs = el('div', 'subtabs'); tabs.id = 'prjTabs';
    SUBS.forEach(([k, label]) => { const b = el('button', k === P.sub ? 'on' : '', label); b.type = 'button'; b.dataset.sub = k; b.addEventListener('click', () => openSub(k)); tabs.appendChild(b); });
    body = el('div', 'lbody'); body.id = 'prjBody'; root.append(bar, tabs, body);
  }
  function openSub(k) { P.sub = k; CP.showTab('projects'); document.querySelectorAll('#prjTabs button').forEach(b => b.classList.toggle('on', b.dataset.sub === k)); if (P.id) refreshProject().catch(() => {}).then(renderSub); else renderSub(); }
  async function loadList(select) {
    const d = await api('/api/projects'); P.list = d.projects; sel.innerHTML = '';
    if (!P.list.length) sel.add(new Option('No projects yet', ''));
    P.list.forEach(p => sel.add(new Option(`${p.name} (${p.files} files, ${p.snapshots} versions)`, p.id)));
    const want = select || (P.list.some(p => p.id === P.id) ? P.id : P.list[0] && P.list[0].id);
    if (want) await selectProject(want); else { P.id = null; P.d = null; info.textContent = ''; renderSub(); }
  }
  async function selectProject(id) { P.id = id; sel.value = id; P.d = await api('/api/projects/' + id); P.xray = null; info.textContent = `entry: ${P.d.entry || '(not set)'} · ${P.d.snapshots.length} version(s)`; renderSub(); }
  async function refreshProject() { const old = P.d && P.d.snapshot; P.d = await api('/api/projects/' + P.id); if (!old || old !== P.d.snapshot) P.xray = null; info.textContent = `entry: ${P.d.entry || '(not set)'} · ${P.d.snapshots.length} version(s)`; }
  CP.hooks.projects = () => {
    if (!P.built) { P.built = true; shell(); loadList().catch(e => { body.innerHTML = ''; body.appendChild(el('div', 'lerr', e.message)); }); return; }
    api('/api/projects').then(d => { if (d.projects.map(p => p.id).join() !== P.list.map(p => p.id).join()) return loadList(); }).catch(() => {});   // pick up projects created elsewhere
  };
  function renderSub() {
    if (!body) return; body.innerHTML = '';
    if (!P.id) { body.appendChild(el('div', 'welcome', 'Create a project to unlock X-Ray, Time Machine, Mission Mode and Impact Radar: upload a ZIP, import a public GitHub repository, or start from the code in the editor.')); return; }
    const run = { xray: renderXray, time: renderTime, mission: renderMission, impact: renderImpact, memory: renderMemory, files: renderFiles }[P.sub];
    Promise.resolve(run(body)).catch(e => fail(body, e));
  }
  async function ensureXray() { if (!P.xray) { P.xray = await api(`/api/projects/${P.id}/xray`); CP.evidence.add(P.xray.evidence); usage.bump('xray'); } return P.xray; }
  async function openFile(path, snapshot) {
    const d = await api(`/api/projects/${P.id}/file?path=${encodeURIComponent(path)}${snapshot ? '&snapshot=' + snapshot : ''}`); const l = langOfPath(path);
    if (l) CP.setLanguage(l); ed.set(d.content); CP.showTab('code'); flash('Opened ' + path);
  }
  const udiffPre = text => { const pre = el('pre', 'udiff'); text.split('\n').forEach(l => { const s = el('div', l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : l.startsWith('@@') ? 'hunk' : '', l || ' '); pre.appendChild(s); }); return pre; };
  function projTests(res, box) {
    box.innerHTML = '';
    if (res.compile_error) { box.appendChild(el('div', 'summary bad', 'Build error: ' + res.compile_error.stderr.slice(0, 300))); return; }
    box.appendChild(el('div', 'summary ' + (res.passed === res.total ? 'ok' : 'bad'), `${res.passed} / ${res.total} recorded tests pass`));
    (res.results || []).forEach(r => { const row = el('div', 'rrow'), h = el('header'); h.append(el('b', r.passed ? 'ok' : 'bad', r.passed ? 'PASS' : 'FAIL'), el('span', '', r.name), el('span', '', r.status)); row.appendChild(h);
      if (!r.passed) { const p = el('pre'); p.append(el('i', '', 'INPUT'), document.createTextNode(r.stdin || '(none)'), el('i', '', 'EXPECTED'), document.createTextNode(r.expected), el('i', '', 'ACTUAL'), document.createTextNode(r.actual || '(empty)')); if (r.stderr) p.append(el('i', '', 'STDERR'), document.createTextNode(r.stderr)); row.appendChild(p); } box.appendChild(row); });
  }

  /* ───── X-Ray ───── */
  async function renderXray(b) {
    b.appendChild(note('Analysing the project…')); const x = await ensureXray(); b.innerHTML = '';
    const s = x.summary, cards = el('div', 'cards');
    [['Files', s.files], ['Lines', s.lines], ['Functions', s.functions], ['Classes', s.classes], ['Imports (edges)', s.edges], ['Endpoints', s.endpoints], ['Packages', s.external], ['Parse errors', s.parse_errors]].forEach(([k, v]) => { const c = el('div', 'card2'); c.append(el('b', '', String(v)), el('span', '', k)); cards.appendChild(c); });
    const langs = el('div', 'badges'); Object.entries(s.languages).forEach(([k, v]) => langs.appendChild(el('span', '', `${k} · ${v}`)));
    const gbox = el('div'), detail = el('div', 'xdetail'); gbox.id = 'xrGraph';
    const onNode = n => { V.graph(gbox, x.graph, { focus: n.id, onClick: onNode }); showNode(n); };
    V.graph(gbox, x.graph, { onClick: onNode });
    function showNode(n) { detail.innerHTML = ''; const fs = x.functions.filter(f => f.path === n.id); detail.append(el('b', '', n.id), note(`${n.lang} · ${n.lines} lines · imports ${n.out} · imported by ${n.in}${n.test ? ' · test file' : ''}`));
      const ul = el('ul'); fs.slice(0, 12).forEach(f => { const li = el('li', '', `${f.name} (line ${f.line}, complexity ${f.cc})`); ul.appendChild(li); }); detail.appendChild(ul); detail.appendChild(btn('Open in editor', 'ghostbtn', () => openFile(n.id).catch(e => flash(e.message)))); }
    // find
    const q = el('input'); q.id = 'xrQ'; q.placeholder = 'Where is … implemented?  e.g. "where is the tax computed"'; const res = el('div'); res.id = 'xrResults';
    const go = btn('Find', 'go', async bt => { await busy(bt, 'Searching…', async () => { try { const d = await api(`/api/projects/${P.id}/search?q=${encodeURIComponent(q.value)}`); res.innerHTML = ''; if (!d.results.length) res.appendChild(note('No match. Try other words from the feature name.'));
      d.results.forEach(r => { const c = el('div', 'hit'); c.append(el('b', '', `${r.name}`), el('small', '', ` ${r.path}:${r.line} · ${r.kind} · score ${r.score}`), el('pre', '', r.snippet), note('Why: ' + r.why.join('; ')), btn('Open', 'ghostbtn', () => openFile(r.path).catch(e => flash(e.message)))); res.appendChild(c); }); } catch (e) { fail(res, e); } }); });
    q.addEventListener('keydown', e => { if (e.key === 'Enter') go.click(); });
    // ask
    const qa = el('textarea'); qa.id = 'xrAsk'; qa.rows = 2; qa.placeholder = 'Ask about how the project works…'; const src = el('div', 'badges'), ans = el('div', 'md'); ans.id = 'xrAnswer';
    const askBtn = btn('Ask the project', 'go', async bt => { if (!qa.value.trim()) return; await busy(bt, 'Reading code…', async () => { src.innerHTML = ''; ans.innerHTML = ''; let text = '';
      try { await stream(`/api/projects/${P.id}/ask`, { question: qa.value }, ev => { if (ev.sources) { ev.sources.forEach(s2 => src.appendChild(el('span', '', `${s2.path}:${s2.start}-${s2.end}`))); }
        if (ev.d) { text += ev.d; ans.replaceChildren(CP.md(text)); } }).catch(e => { if (!/without a result/.test(e.message)) throw e; });
        CP.evidence.add([{ claim: 'Answer about the project (AI, grounded in the retrieved code excerpts)', status: 'ai', source: 'X-Ray', items: [...src.children].slice(0, 6).map(s2 => ({ type: 'source', ref: s2.textContent, text: 'excerpt sent to the AI' })) }]); } catch (e) { fail(ans, e); } }); });
    const epT = el('div', 'kv'); x.endpoints.slice(0, 40).forEach(e => { const r = el('div'); r.append(el('b', '', e.methods.join('/') + ' ' + e.path), el('span', '', `${e.handler} — ${e.path ? '' : ''}${e.line ? 'line ' + e.line : ''}`)); epT.appendChild(r); });
    const ext = el('div', 'badges'); Object.keys(x.external).slice(0, 40).forEach(k => ext.appendChild(el('span', '', `${k} (${x.external[k].length})`)));
    const dups = el('div'); x.duplicates.slice(0, 12).forEach(g => { const c = el('div', 'hit'); c.append(badge(g.kind + ' · ' + Math.round(g.similarity * 100) + '%', g.kind === 'exact' ? 'bad' : 'warn'), badge('static', 'st'));
      g.items.forEach(i => c.appendChild(el('div', 'tinfo', `${i.path}:${i.start}-${i.end} ${i.name}`))); dups.appendChild(c); }); if (!x.duplicates.length) dups.appendChild(note('No duplicate logic found.', 'good'));
    const unu = el('div'); x.unused.slice(0, 20).forEach(u => { const c = el('div', 'hit'); c.append(badge('static', 'st'), badge('verify before deleting', 'warn'), el('b', '', ' ' + u.name), el('small', '', ` ${u.path}:${u.line}`), note(u.why + ' ' + u.caveat)); unu.appendChild(c); }); if (!x.unused.length) unu.appendChild(note('No unused functions found.', 'good'));
    const errs = x.errors.length ? sec('Files that failed to parse', ...x.errors.map(e => note(`${e.path}: ${e.error}`, 'bad'))) : null;
    b.append(cards, langs, sec('Dependency map — click a file to focus it', gbox, detail), sec('Find where a feature is implemented', q, go, res), sec('Ask a question about the project', qa, askBtn, src, ans),
      x.endpoints.length ? sec('Endpoints', epT) : null, Object.keys(x.external).length ? sec('Third-party packages', ext) : null, sec('Duplicate logic', dups), sec('Potentially unused code', note('Static analysis cannot see dynamic calls — treat these as leads, not verdicts.', 'warnc'), unu), errs);
  }

  /* ───── Time Machine ───── */
  function sideBySide(a, b) {
    const ops = CP.lineDiff(a, b) || [], rows = []; let ln = 1, rn = 1;
    for (let k = 0; k < ops.length;) {
      if (ops[k][0] === '=') { rows.push({ l: [ln++, ops[k][1], ''], r: [rn++, ops[k][1], ''] }); k++; continue; }
      const dels = [], adds = []; while (k < ops.length && ops[k][0] === '-') dels.push(ops[k++][1]); while (k < ops.length && ops[k][0] === '+') adds.push(ops[k++][1]);
      for (let m = 0; m < Math.max(dels.length, adds.length); m++) rows.push({ l: m < dels.length ? [ln++, dels[m], 'del'] : null, r: m < adds.length ? [rn++, adds[m], 'add'] : null });
    }
    const t = el('div', 'sbs'); let same = 0;
    const flush = () => { if (same > 6) t.appendChild(el('div', 'skip', `… ${same - 6} unchanged lines …`)); same = 0; };
    rows.forEach((r, i) => { const unchanged = r.l && r.r && !r.l[2]; if (unchanged) { same++; const near = rows.slice(Math.max(0, i - 3), i + 4).some(x => !(x.l && x.r && !x.l[2])); if (!near) return; }
      else flush();
      const row = el('div', 'sbsrow'); [r.l, r.r].forEach(c => { const cell = el('div', 'sbscell ' + (c ? c[2] : 'empty')); cell.append(el('i', '', c ? String(c[0]) : ''), document.createTextNode(c ? c[1] || ' ' : ' ')); row.appendChild(cell); }); t.appendChild(row); });
    return t;
  }
  async function renderTime(b) {
    const snaps = P.d.snapshots, files = P.d.files.map(f => f.path);
    if (snaps.length < 2) b.appendChild(note('This project has one version. Save changes as new versions (Files tab, or "Save editor code" below) so the Time Machine has history to compare.', 'warnc'));
    const opt = (s, v) => { const o = new Option(`#${s.id} — ${s.label} (${new Date(s.ts * 1000).toLocaleString()})`, s.id); return o; };
    const A = el('select'), B = el('select'); A.id = 'tmA'; B.id = 'tmB'; snaps.forEach(s => { A.add(opt(s)); B.add(opt(s)); });
    A.value = snaps.length > 1 ? snaps[snaps.length - 2].id : snaps[0].id; B.value = snaps[snaps.length - 1].id;
    const res = el('div'), viewer = el('div'); res.id = 'tmCompare';
    const cmp = btn('Compare versions', 'go', async bt => busy(bt, 'Comparing…', async () => { try { const d = await api(`/api/projects/${P.id}/compare`, { a: +A.value, b: +B.value }); res.innerHTML = ''; viewer.innerHTML = '';
      if (!d.changed.length) return res.appendChild(note('These two versions are identical.'));
      const ul = el('div', 'kv'); d.changed.forEach(c => { const r = el('div', 'clickable'); r.append(badge(c.status, c.status === 'added' ? 'good' : c.status === 'removed' ? 'bad' : 'warn'), el('b', '', ' ' + c.path), el('span', '', `+${c.adds} −${c.dels}`));
        r.addEventListener('click', async () => { viewer.innerHTML = ''; try { const get = async (sid, ex) => ex ? (await api(`/api/projects/${P.id}/file?path=${encodeURIComponent(c.path)}&snapshot=${sid}`)).content : ''; viewer.append(note(`${c.path}: version #${A.value} (left) vs #${B.value} (right)`), sideBySide(await get(A.value, c.status !== 'added'), await get(B.value, c.status !== 'removed'))); } catch (e) { fail(viewer, e); } }); ul.appendChild(r); });
      res.appendChild(ul); } catch (e) { fail(res, e); } }));
    // recorded inputs
    const entry = el('select'); entry.id = 'tmEntry'; files.forEach(f => entry.add(new Option(f, f))); entry.value = P.d.entry || files[0]; entry.addEventListener('change', save);
    const tests = P.d.tests.length ? P.d.tests.map(t => ({ ...t })) : [{ name: 'Test 1', stdin: '', expected: '' }]; const list = el('div'); list.id = 'tmTests';
    function draw() { list.innerHTML = ''; tests.forEach((t, i) => { const r = el('div', 'trow'); r.appendChild(el('span', '', '#' + (i + 1)));
      const a = el('textarea'); a.placeholder = 'Recorded input (stdin)'; a.value = t.stdin; a.addEventListener('input', () => { t.stdin = a.value; }); const e2 = el('textarea'); e2.placeholder = 'Expected output'; e2.value = t.expected; e2.addEventListener('input', () => { t.expected = e2.value; });
      const x = btn('×', '', () => { if (tests.length > 1) { tests.splice(i, 1); draw(); } }); x.className = ''; r.append(a, e2, x); list.appendChild(r); }); }
    draw();
    async function save() { try { const d = await api(`/api/projects/${P.id}/settings`, { entry: entry.value, tests: tests.filter(t => t.stdin.trim() || t.expected.trim()) }, 'PUT'); P.d.entry = d.entry; P.d.tests = d.tests; info.textContent = `entry: ${d.entry} · ${P.d.snapshots.length} version(s)`; flash('Recorded inputs saved'); } catch (e) { flash(e.message); } }
    const ctl = el('div', 'pact');
    ctl.append(btn('+ Add input', 'ghostbtn', () => { if (tests.length < 8) { tests.push({ name: 'Test ' + (tests.length + 1), stdin: '', expected: '' }); draw(); } }), btn('Save inputs', 'ghostbtn', save),
      btn('Record expected output from version A', 'ghostbtn', async bt => busy(bt, 'Recording…', async () => { try { await save(); const d = await api(`/api/projects/${P.id}/tests/record`, { snapshot: +A.value, inputs: tests.map(t => t.stdin) }); tests.splice(0, tests.length, ...d.tests); P.d.tests = d.tests; draw(); flash(`Recorded the real output of version #${d.snapshot}`); } catch (e) { flash(e.message); } })),
      btn('Import tests from Code tab', 'ghostbtn', () => { const u = CP.usableTests(); if (!u.length) return flash('The Code tab has no test cases.'); tests.splice(0, tests.length, ...u.map((t, i) => ({ name: 'Test ' + (i + 1), stdin: t.input, expected: t.expected }))); draw(); }),
      btn('Save editor code as a new version', 'ghostbtn', async () => { const path = prompt('Which file does the editor code replace?', entry.value); if (!path) return; const label = prompt('Version label:', 'Edited in editor') || 'Edited in editor';
        try { await api(`/api/projects/${P.id}/snapshot`, { label, changes: { [path]: ed.get() } }); await refreshProject(); flash('New version saved'); renderSub(); } catch (e) { flash(e.message); } }));
    // scan + analyze
    const prog = el('div', 'progress'), matrix = el('div'), analysis = el('div'); matrix.id = 'tmMatrix'; analysis.id = 'tmAnalysis';
    const scan = btn('Find when it broke', 'go', async bt => busy(bt, 'Replaying…', async () => { prog.innerHTML = ''; matrix.innerHTML = ''; analysis.innerHTML = '';
      try { await save(); const d = await stream(`/api/projects/${P.id}/timemachine/scan`, {}, ev => { if (ev.step) prog.appendChild(el('div', 'tinfo', ev.msg)); }); prog.innerHTML = ''; CP.evidence.add(d.evidence); renderScan(d); } catch (e) { fail(matrix, e); } }));
    function renderScan(d) {
      const t = el('div', 'kv'); d.matrix.forEach(m => { const r = el('div', m.snapshot === d.first_bad ? 'firstbad' : m.snapshot === d.last_good ? 'lastgood' : ''); const okk = m.passed === m.total && !m.compile_error;
        r.append(el('b', okk ? 'good' : 'bad', okk ? '✔' : '✘'), el('span', '', `#${m.snapshot} ${m.label} — ${m.compile_error ? 'build error' : m.passed + '/' + m.total + ' tests pass'}${m.snapshot === d.first_bad ? '  ← first failing version' : m.snapshot === d.last_good ? '  ← last good version' : ''}`)); t.appendChild(r); });
      matrix.append(sec('Replay results (real runs, same recorded inputs)', t));
      if (d.verdict === 'regression') { matrix.appendChild(el('div', 'summary bad', `Version #${d.first_bad} broke it. Last good version: #${d.last_good}.`));
        matrix.appendChild(btn('Analyze the change with AI', 'go', async bt => busy(bt, 'Analysing…', async () => { analysis.innerHTML = ''; const p2 = el('div', 'progress'); analysis.appendChild(p2);
          try { const r = await stream(`/api/projects/${P.id}/timemachine/analyze`, { good: d.last_good, bad: d.first_bad }, ev => { if (ev.step) p2.appendChild(el('div', 'tinfo', ev.msg)); }); CP.evidence.add(r.evidence); renderAnalysis(r, d); } catch (e) { fail(analysis, e); } }))); }
      else matrix.appendChild(el('div', 'summary ' + (d.verdict === 'no_failure' ? 'ok' : 'bad'), d.verdict === 'no_failure' ? 'All versions pass the recorded inputs — nothing to replay.' : 'The tests fail in every version — no earlier working version to compare with.'));
    }
    function renderAnalysis(r, d) {
      analysis.innerHTML = ''; analysis.appendChild(sec('Root cause (AI)', el('p', '', r.root_cause || '(none given)')));
      const ul = el('div'); r.suspects.forEach(s => { const c = el('div', 'hit'); c.append(badge(s.in_changed_lines ? 'inside the changed lines ✔' : 'NOT in the changed lines ⚠', s.in_changed_lines ? 'good' : 'warn'), el('b', '', ` ${s.path}:${s.line_start}-${s.line_end}`), el('pre', '', s.line_text || '(line not found)'), note(s.reason)); ul.appendChild(c); });
      analysis.appendChild(sec('Suspicious changes', ul.children.length ? ul : note('No specific lines suggested.')));
      if (r.fix) { const f = r.fix; analysis.appendChild(sec('Minimal fix', badge(f.verified ? `verified: ${f.passed}/${f.total} tests pass after the fix` : `not verified: ${f.passed}/${f.total} tests pass`, f.verified ? 'good' : 'bad'), note(f.explanation), udiffPre(f.diff),
        btn('Apply the fix as a new version', 'go', async bt => busy(bt, 'Saving…', async () => { try { await api(`/api/projects/${P.id}/snapshot`, { label: 'Time Machine fix', changes: { [f.path]: f.new_content } }); await refreshProject(); suggestMemory('fix', `Fixed a regression in ${f.path}: ${r.root_cause}`.slice(0, 280)); flash('Fix saved as a new version'); renderSub(); } catch (e) { flash(e.message); } })))); }
      else analysis.appendChild(note('The AI proposed no complete fix.'));
    }
    b.append(sec('Compare two versions side by side', el('div', 'pact', ), (() => { const w = el('div', 'pact'); w.append(el('label', '', 'From '), A, el('label', '', ' to '), B, cmp); return w; })(), res, viewer),
      sec('Recorded inputs (replay a failure)', note('Entry file:'), entry, list, ctl, note('Tip: record the expected output from a version that worked ("it worked yesterday"), then press Find when it broke.')),
      sec('Find the version that introduced the bug', scan, prog, matrix, analysis));
  }

  /* ───── Mission Mode ───── */
  function mState() { return P.missions[P.id] || (P.missions[P.id] = { task: '', plan: null, steps: [], proposal: null, lastTests: null, unfinished: [], log: [] }); }
  async function renderMission(b) {
    const M = mState(), task = el('textarea'); task.id = 'msTask'; task.rows = 3; task.value = M.task; task.placeholder = 'e.g. Add input validation and a /health endpoint to my Flask app';
    const area = el('div'); area.id = 'msArea';
    const planBtn = btn('Create plan', 'go', async bt => busy(bt, 'Planning…', async () => { M.task = task.value; const prog = el('div', 'progress'); area.prepend(prog);
      try { const d = await stream(`/api/projects/${P.id}/mission/plan`, { task: task.value }, ev => { if (ev.step) prog.textContent = ev.msg; }); M.plan = d; M.steps = d.steps.map(s => ({ ...s, status: 'pending' })); M.proposal = null; M.lastTests = null; M.unfinished = []; M.log = []; usage.bump('missions'); draw(); } catch (e) { prog.replaceWith(el('div', 'lerr', e.message)); } }));
    function draw() {
      area.innerHTML = ''; if (!M.plan) return;
      const done = M.steps.filter(s => s.status === 'applied').length, pct = Math.round(100 * done / M.steps.length);
      const bar = el('div', 'pbarline'); const fill = el('u'); fill.style.width = pct + '%'; bar.appendChild(fill);
      area.append(sec(`Plan — ${done}/${M.steps.length} steps applied`, el('p', '', M.plan.summary), bar, M.plan.risks.length ? note('Risks: ' + M.plan.risks.join(' · '), 'warnc') : null));
      M.steps.forEach(s => {
        const c = el('div', 'mstep st-' + s.status); const h = el('header'); h.append(el('b', '', `${s.id}. ${s.title}`), badge(s.status, s.status === 'applied' ? 'good' : s.status === 'failed' ? 'bad' : s.status === 'proposed' ? 'warn' : ''));
        c.append(h, note(s.description), note('Files: ' + (s.files.join(', ') || '(to be decided)') + (s.new_files.length ? ` · new: ${s.new_files.join(', ')}` : '')));
        if (s.status === 'pending' || s.status === 'failed') c.appendChild(btn(s.status === 'failed' ? 'Propose a new attempt' : 'Propose changes', 'ghostbtn', bt => propose(s, bt, '')));
        if (s.result) c.appendChild(resultView(s));
        if (M.proposal && M.proposal.stepId === s.id && s.status === 'proposed') c.appendChild(proposalView(s)); area.appendChild(c);
      });
      area.appendChild(reportView()); area.appendChild(versionsView());
    }
    async function propose(s, bt, failure) {
      await busy(bt, 'Writing changes…', async () => { try { const p = await stream(`/api/projects/${P.id}/mission/propose`, { task: M.task, step: { title: s.title, description: s.description, files: s.files }, failure }, () => {});
        M.proposal = { stepId: s.id, ...p, picks: p.changes.map(c => !c.syntax_error) }; s.status = 'proposed'; s.result = null; (p.unfinished || []).forEach(u => { if (!M.unfinished.includes(u)) M.unfinished.push(u); }); draw(); } catch (e) { flash(e.message); } });
    }
    function proposalView(s) {
      const pr = M.proposal, box = el('div', 'proposal'); box.id = 'msProposal';
      pr.changes.forEach((ch, i) => { const c = el('div', 'chg'), h = el('header'), cb = el('input'); cb.type = 'checkbox'; cb.checked = pr.picks[i]; cb.addEventListener('change', () => { pr.picks[i] = cb.checked; });
        h.append(cb, el('b', '', ch.path), badge(ch.action, ch.action === 'create' ? 'good' : ch.action === 'delete' ? 'bad' : 'warn'), el('small', '', `+${ch.adds} −${ch.dels}`), ch.unplanned ? badge('not in the plan', 'warn') : null);
        c.append(h, note(ch.explanation)); if (ch.syntax_error) c.appendChild(el('div', 'lerr', ch.syntax_error)); const d = el('details'); d.appendChild(el('summary', '', 'Preview the change (diff)')); d.appendChild(udiffPre(ch.diff)); c.appendChild(d); box.appendChild(c); });
      if (pr.notes) box.appendChild(note('Notes: ' + pr.notes));
      const prog = el('div');
      const apply = btn('Approve & apply selected', 'go', async bt => busy(bt, 'Applying…', async () => {
        const picked = pr.changes.filter((c, i) => pr.picks[i]).map(c => ({ path: c.path, action: c.action, content: c.content })); if (!picked.length) return flash('Select at least one change.');
        prog.innerHTML = ''; const p = el('div', 'progress'); prog.appendChild(p);
        try { const d = await stream(`/api/projects/${P.id}/mission/apply`, { changes: picked, label: s.title }, ev => { if (ev.step) p.textContent = ev.msg; }); CP.evidence.add(d.evidence);
          M.lastTests = d.tests; const bad = d.syntax_errors.length || (d.tests && d.tests.passed != null && d.tests.passed !== d.tests.total);
          s.status = bad ? 'failed' : 'applied'; s.result = d; M.proposal = null; await refreshProject(); M.log.push(`${s.title}: ${bad ? 'applied but checks failed' : 'applied'}`);
          if (!bad) suggestMemory('fix', `${s.title} (mission: ${M.task.slice(0, 80)})`); draw();
        } catch (e) { prog.innerHTML = ''; prog.appendChild(el('div', 'lerr', e.message)); } }));
      const rej = btn('Reject', 'ghostbtn', () => { s.status = 'pending'; M.proposal = null; draw(); });
      box.append(prog, el('div', 'pact', ), apply, rej); return box;
    }
    function resultView(s) {
      const d = s.result, box = el('div', 'stepres'); box.id = 'msResult'; const out = el('div');
      if (d.tests && d.tests.results) projTests(d.tests, out); else if (d.tests && d.tests.error) out.appendChild(el('div', 'lerr', d.tests.error)); else out.appendChild(note('No recorded tests, so nothing was re-run automatically. Add recorded inputs in the Time Machine tab.', 'warnc'));
      d.syntax_errors.forEach(e => out.appendChild(el('div', 'lerr', `Syntax error in ${e.path}: ${e.error}`))); box.appendChild(out);
      if (s.status === 'failed') {
        const fails = d.tests && d.tests.results ? d.tests.results.filter(r => !r.passed).map(r => `${r.name}: expected ${JSON.stringify(r.expected)} got ${JSON.stringify(r.actual)} ${r.stderr}`).join('\n') : '';
        box.appendChild(btn('Ask the AI to repair this', 'ghostbtn', bt2 => propose(s, bt2, (fails + '\n' + d.syntax_errors.map(e => e.error).join('\n')).slice(0, 2400) || 'The checks failed.')));
        box.appendChild(btn('Roll back to the previous version', 'ghostbtn', async () => { try { await api(`/api/projects/${P.id}/restore`, { snapshot: d.previous }); await refreshProject(); s.status = 'pending'; s.result = null; flash('Rolled back'); draw(); } catch (e) { flash(e.message); } }));
      }
      return box;
    }
    function reportView() {
      const left = M.steps.filter(s => s.status !== 'applied'), c = el('div');
      c.append(note(`Done: ${M.steps.filter(s => s.status === 'applied').map(s => s.title).join('; ') || 'nothing yet'}`, 'good'), note(`Remaining: ${left.map(s => s.title).join('; ') || 'none — all steps applied'}`, left.length ? 'warnc' : 'good'));
      if (M.unfinished.length) c.appendChild(note('Not covered by the proposals: ' + M.unfinished.join(' · '), 'warnc'));
      if (M.lastTests && M.lastTests.passed != null) c.appendChild(note(`Last automatic test run: ${M.lastTests.passed}/${M.lastTests.total} recorded tests pass`, M.lastTests.passed === M.lastTests.total ? 'good' : 'bad'));
      return sec('Progress report', c);
    }
    function versionsView() {
      const c = el('div', 'kv'); P.d.snapshots.slice().reverse().slice(0, 10).forEach(s => { const r = el('div'); r.append(el('b', '', `#${s.id}`), el('span', '', s.label), btn('Restore', 'ghostbtn', async () => { if (!confirm(`Restore version #${s.id}? (This creates a new version.)`)) return; try { await api(`/api/projects/${P.id}/restore`, { snapshot: s.id }); await refreshProject(); flash('Restored'); draw(); } catch (e) { flash(e.message); } })); c.appendChild(r); });
      return sec('Saved versions (roll back any time)', c);
    }
    b.append(sec('Describe the task', task, planBtn), area); draw();
  }

  /* ───── Impact Radar ───── */
  async function renderImpact(b) {
    const x = await ensureXray(), files = x.files.filter(f => ['Python', 'JavaScript', 'TypeScript', 'Java', 'C', 'C++', 'Go', 'Rust', 'PHP', 'Ruby', 'C#', 'Kotlin', 'Swift', 'Bash'].includes(f.lang)).map(f => f.path);
    if (!files.length) return b.appendChild(note('No source files in this project.'));
    const fs = el('select'); fs.id = 'imFile'; files.forEach(f => fs.add(new Option(f, f))); const fn = el('select'); fn.id = 'imFunc';
    const code = el('textarea'); code.id = 'imCode'; code.rows = 5; code.placeholder = 'Optional: paste the new version of the function to check for breaking API changes (Python)';
    const fill = () => { fn.innerHTML = ''; fn.add(new Option('(whole file)', '')); x.functions.filter(f => f.path === fs.value).forEach(f => fn.add(new Option(`${f.name} (line ${f.line})`, f.name))); }; fs.addEventListener('change', fill); fill();
    const out = el('div'); out.id = 'imOut';
    const run = btn('Analyze impact', 'go', async bt => busy(bt, 'Analysing…', async () => { out.innerHTML = '';
      try { const r = await api(`/api/projects/${P.id}/impact`, { path: fs.value, function: fn.value, new_code: code.value.trim() || undefined }); CP.evidence.add(r.evidence); usage.bump('impact');
        const head = el('div', 'impacthead'); head.append(badge(r.level.toUpperCase() + ' RISK', r.level === 'high' ? 'bad' : r.level === 'medium' ? 'warn' : 'good'), el('b', '', ` score ${r.score}`), el('span', '', `  ${r.function || r.path}`));
        const fac = el('div'); r.factors.forEach(f => { const c = el('div', 'hit'); c.append(el('b', '', `+${f.points} ${f.name}`), badge('static', 'st')); f.evidence.slice(0, 5).forEach(e2 => c.appendChild(el('div', 'tinfo', e2))); fac.appendChild(c); }); if (!r.factors.length) fac.appendChild(note('No risk factors found — a small, isolated change.', 'good'));
        const brk = el('div'); r.breaking.forEach(x2 => brk.appendChild(el('div', 'lerr', x2.detail))); if (code.value.trim() && !r.breaking.length) brk.appendChild(note('No breaking signature change detected.', 'good'));
        const tests = el('div'); r.tests.forEach(t => tests.appendChild(note(`${t.path} — ${t.reason}`))); if (!r.tests.length) tests.appendChild(note('No test file reaches this code.', 'warnc')); if (r.recorded_tests_reach) tests.appendChild(note('The project\'s recorded tests exercise this entry point.', 'good'));
        const runT = P.d.tests.length ? btn('Run the recorded tests now', 'ghostbtn', async bt2 => busy(bt2, 'Running…', async () => { try { const tr = await api(`/api/projects/${P.id}/tests/run`, {}); projTests(tr, tout); } catch (e) { fail(tout, e); } })) : null; const tout = el('div');
        const gbox = el('div'); V.graph(gbox, x.graph, { highlight: new Set(r.affected), focus: null });
        const callers = el('div', 'kv'); r.callers.slice(0, 10).forEach(c => { const row = el('div'); row.append(el('b', '', c.name), el('span', '', `${c.path}:${c.line}`)); callers.appendChild(row); });
        const eps = el('div', 'kv'); r.endpoints.forEach(e2 => { const row = el('div'); row.append(el('b', '', e2.methods.join('/') + ' ' + e2.path), el('span', '', e2.handler)); eps.appendChild(row); });
        out.append(head, sec('Why (every factor shows its evidence)', fac), r.breaking.length || code.value.trim() ? sec('Breaking API changes', brk) : null, r.callers.length ? sec('Callers' + (r.approximate_callers ? ' (approximate for this language)' : ''), callers) : null, r.endpoints.length ? sec('Affected API endpoints', eps) : null,
          sec('Tests to rerun', tests, runT, tout), sec('Affected components (red) in the dependency map', gbox), note(r.note)); } catch (e) { fail(out, e); } }));
    b.append(sec('What could this change break?', el('div', 'pact', ), (() => { const w = el('div', 'pact'); w.append(fs, fn); return w; })(), code, run), out);
  }

  /* ───── Memory ───── */
  function suggestMemory(kind, text) {
    if (!P.id || !P.d) return; const mem = P.d.memory || (P.d.memory = []);
    if (mem.some(m => m.text === text)) return; mem.push({ id: Math.random().toString(36).slice(2, 10), kind, text, approved: false, source: 'auto', ts: Date.now() / 1000 });
    api(`/api/projects/${P.id}/memory`, { entries: mem }, 'PUT').then(d => { P.d.memory = d.entries; }).catch(() => {}); flash('Suggested a memory note — approve it in Projects → Memory');
  }
  async function renderMemory(b) {
    const mem = P.d.memory || (P.d.memory = []); const list = el('div'); list.id = 'mmList';
    let timer = 0; const save = () => { clearTimeout(timer); timer = setTimeout(async () => { try { const d = await api(`/api/projects/${P.id}/memory`, { entries: mem }, 'PUT'); P.d.memory = d.entries; count(); } catch (e) { flash(e.message); } }, 400); };
    const cnt = el('b'); const count = () => { cnt.textContent = `${mem.filter(m => m.approved).length} approved entr${mem.filter(m => m.approved).length === 1 ? 'y' : 'ies'} are sent to the AI`; };
    function draw() { list.innerHTML = ''; if (!mem.length) list.appendChild(note('No memory yet. Add a note, or let the analyzer suggest what it found.'));
      mem.forEach((m, i) => { const r = el('div', 'memrow' + (m.approved ? ' on' : '')), k = el('select'); ['architecture', 'convention', 'dependency', 'fix', 'note'].forEach(x => k.add(new Option(x, x))); k.value = m.kind; k.addEventListener('change', () => { m.kind = k.value; save(); });
        const t = el('input'); t.value = m.text; t.maxLength = 300; t.addEventListener('input', () => { m.text = t.value; save(); }); const ap = el('input'); ap.type = 'checkbox'; ap.checked = m.approved; ap.title = 'Approve: send to the AI'; ap.addEventListener('change', () => { m.approved = ap.checked; m.source = 'user'; r.classList.toggle('on', m.approved); save(); count(); });
        const lab = el('label', 'chk'); lab.append(ap, document.createTextNode('use')); const del = btn('×', '', () => { mem.splice(i, 1); draw(); save(); }); del.className = '';
        r.append(k, t, lab, badge(m.source === 'user' ? 'you' : 'auto', m.source === 'user' ? 'good' : ''), del); if (m.evidence) r.appendChild(el('small', '', `evidence: ${m.evidence}`)); list.appendChild(r); }); count(); }
    draw();
    const add = btn('+ Add note', 'ghostbtn', () => { mem.push({ id: Math.random().toString(36).slice(2, 10), kind: 'note', text: 'New note', approved: true, source: 'user', ts: Date.now() / 1000 }); draw(); save(); });
    const suggest = (ai) => async bt => busy(bt, 'Analysing…', async () => { try { const d = await api(`/api/projects/${P.id}/memory/suggest`, { use_ai: ai }); let n = 0; d.suggestions.forEach(s => { if (!mem.some(m => m.text === s.text)) { mem.push(s); n++; } }); draw(); save(); flash(`${n} suggestion(s) added — approve the ones you want`); } catch (e) { flash(e.message); } });
    b.append(sec('Project memory', note('Architecture decisions, conventions, dependencies and previous fixes. Only entries you tick "use" for are ever sent to the AI. Edit or delete anything.'), cnt, list, el('div', 'pact', ), (() => { const w = el('div', 'pact'); w.append(add, btn('Suggest from the code (static)', 'ghostbtn', suggest(false)), btn('Suggest with AI', 'ghostbtn', suggest(true))); return w; })()));
  }

  /* ───── Files ───── */
  async function renderFiles(b) {
    const list = el('div', 'kv'), ed2 = el('textarea'), name = el('b'); ed2.id = 'flText'; ed2.rows = 14; ed2.spellcheck = false; ed2.hidden = true; let cur = null;
    P.d.files.forEach(f => { const r = el('div', 'clickable'); r.append(el('b', '', f.path), el('span', '', `${f.lang} · ${f.lines} lines`)); r.addEventListener('click', async () => { try { const d = await api(`/api/projects/${P.id}/file?path=${encodeURIComponent(f.path)}`); cur = f.path; name.textContent = f.path; ed2.value = d.content; ed2.hidden = false; } catch (e) { flash(e.message); } }); list.appendChild(r); });
    const save = btn('Save as a new version', 'go', async () => { if (!cur) return flash('Pick a file first.'); const label = prompt('Version label:', 'Edited ' + cur) || ('Edited ' + cur);
      try { await api(`/api/projects/${P.id}/snapshot`, { label, changes: { [cur]: ed2.value } }); await refreshProject(); flash('New version saved'); renderSub(); } catch (e) { flash(e.message); } });
    const addf = btn('+ New file', 'ghostbtn', () => { const p = prompt('New file path (e.g. src/helpers.py):', ''); if (!p) return; cur = p; name.textContent = p + ' (new)'; ed2.value = ''; ed2.hidden = false; });
    const open = btn('Open in the editor', 'ghostbtn', () => { if (cur) openFile(cur).catch(e => flash(e.message)); });
    b.append(sec(`Files in the latest version (${P.d.files.length})`, list), sec('Edit a file', name, ed2, el('div', 'pact', ), (() => { const w = el('div', 'pact'); w.append(save, addf, open); return w; })()));
  }

  [['xray', 'X-Ray: map the project'], ['time', 'Time Machine: replay a bug'], ['mission', 'Mission Mode: plan and apply a task'], ['impact', 'Impact Radar: what could this break?'], ['memory', 'Project memory'], ['files', 'Project files']]
    .forEach(([k, label]) => CP.extraCommands.push({ label: 'Projects → ' + label, run: () => { CP.showTab('projects'); openSub(k); } }));
  CP.openProjectSub = openSub;
  window.CPLab.project = () => (P.id ? { id: P.id, name: (P.d || {}).name || 'project' } : null);
})();

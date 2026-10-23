/* CODEPHANTOM AI — extra features (v4). Uses the window.CP handle exposed by arena.js. */
(() => {
  const CP = window.CP; if (!CP) return;
  const { $, el, esc, els, ed, MODES, post, flash, jget, jset, usage } = CP;
  const norm = s => String(s).replace(/\r\n/g, '\n').split('\n').map(l => l.replace(/\s+$/, '')).join('\n').trim();
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const fetchJSON = async (url, opt) => { const r = await fetch(url, opt); const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || `Request failed (${r.status}).`); return d; };
  const H = { 'Content-Type': 'application/json', 'X-Requested-With': 'codephantom' };

  /* ═════════ theme, motion, split, full screen ═════════ */
  const root = document.documentElement;
  function applyTheme(light) { root.dataset.theme = light ? 'light' : ''; $('#themeBtn').classList.toggle('on', light); ed.theme(light); jset('codephantom.theme', light ? 'light' : 'dark'); }
  function applyMotion(reduced) { root.dataset.motion = reduced ? 'reduced' : ''; $('#motionBtn').classList.toggle('on', reduced); jset('codephantom.motion', reduced ? 'reduced' : 'full'); }
  const toggleTheme = () => applyTheme(!CP.isLight());
  const toggleMotion = () => applyMotion(root.dataset.motion !== 'reduced');
  function toggleSplit() {
    const m = els.main;
    if (!m.classList.contains('split') && window.innerWidth < 1100) return flash('Split view needs a wider window (1100 px+)');
    m.classList.toggle('split'); $('#splitBtn').classList.toggle('on', m.classList.contains('split'));
    CP.showTab(['assist', 'code'].includes(CP.current) ? CP.current : 'assist');
  }
  let fsHold = null;
  function toggleFullscreen() {
    const w = els.editorWrap;
    if (!w.classList.contains('fs')) {
      CP.showTab('code'); fsHold = document.createComment('editor'); w.before(fsHold); document.body.appendChild(w); w.classList.add('fs');
      const bar = el('div', 'fsbar'); bar.id = 'fsBar';
      const r = el('button', '', 'Run'); r.type = 'button'; r.addEventListener('click', () => CP.runCode());
      const x = el('button', '', 'Exit full screen (Esc)'); x.type = 'button'; x.addEventListener('click', toggleFullscreen);
      bar.append(r, x); w.appendChild(bar);
    } else {
      w.classList.remove('fs'); const b = $('#fsBar'); if (b) b.remove();
      if (fsHold && fsHold.parentNode) fsHold.replaceWith(w);
    }
  }
  $('#themeBtn').addEventListener('click', toggleTheme);
  $('#motionBtn').addEventListener('click', toggleMotion);
  $('#splitBtn').addEventListener('click', toggleSplit);
  $('#fsBtn').addEventListener('click', toggleFullscreen);
  const savedTheme = jget('codephantom.theme', 'dark'); if (savedTheme === 'light') applyTheme(true);
  const savedMotion = jget('codephantom.motion', window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches ? 'reduced' : 'full');
  applyMotion(savedMotion === 'reduced');

  /* ═════════ diff, benchmark, validate (buttons on AI code blocks) ═════════ */
  function lineDiff(a, b) {
    const A = a.split('\n'), B = b.split('\n'), n = A.length, m = B.length;
    if (n * m > 400000) return null;
    const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    const out = []; let i = 0, j = 0;
    while (i < n && j < m) { if (A[i] === B[j]) { out.push(['=', A[i]]); i++; j++; } else if (dp[i + 1][j] >= dp[i][j + 1]) out.push(['-', A[i++]]); else out.push(['+', B[j++]]); }
    while (i < n) out.push(['-', A[i++]]); while (j < m) out.push(['+', B[j++]]);
    return out;
  }
  function extra(wrap, cls) {
    let box = wrap.querySelector('.cb__extra.' + cls);
    if (box) { box.remove(); return null; }          // clicking again hides it
    box = el('div', 'cb__extra ' + cls); wrap.appendChild(box); return box;
  }
  CP.actions.diff = (before, after, wrap) => {
    const box = extra(wrap, 'diffbox'); if (!box) return;
    const d = lineDiff(before, after);
    if (!d) return box.appendChild(el('div', 'tinfo', 'The files are too large to compare here.'));
    const add = d.filter(x => x[0] === '+').length, del = d.filter(x => x[0] === '-').length;
    box.appendChild(el('div', 'diffhead', `Before → after: +${add} added, −${del} removed`));
    const v = el('div', 'diff');
    d.forEach(([k, t]) => { const r = el('div', k === '+' ? 'add' : k === '-' ? 'del' : ''); r.append(el('i', '', k === '=' ? ' ' : k), document.createTextNode(t || ' ')); v.appendChild(r); });
    box.appendChild(v);
  };
  const median = a => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
  CP.actions.benchmark = async (before, after, lang, wrap) => {
    const box = extra(wrap, 'benchbox'); if (!box) return;
    box.textContent = 'Benchmarking (3 runs each)…';
    try {
      const stdin = els.stdin.value, res = { before: [], after: [] }, outs = {};
      for (let k = 0; k < 3; k++) for (const [name, code] of [['before', before], ['after', after]]) {
        const r = await post('/api/run', { language: lang, code, stdin });
        if (r.status !== 'ok') throw new Error(`The ${name === 'before' ? 'original' : 'new'} code did not run cleanly (${r.status.replace('_', ' ')}). Fix that first.`);
        res[name].push(r.time_ms ?? 0); outs[name] = norm(r.stdout);
      }
      const a = median(res.before), b = median(res.after);
      box.innerHTML = '';
      box.appendChild(el('div', '', `Measured median time — original: ${a} ms · new: ${b} ms · ${b <= a ? 'new is ' + (a / Math.max(b, 1)).toFixed(2) + '× faster' : 'new is ' + (b / Math.max(a, 1)).toFixed(2) + '× slower'}`));
      box.appendChild(el('div', outs.before === outs.after ? 'good' : 'bad', outs.before === outs.after ? '✓ Both print the same output for this input.' : '✗ The outputs differ for this input — the new code changes behaviour.'));
      box.appendChild(el('div', 'tinfo', 'Real runs on this runner, including process start-up. For tiny inputs this mostly measures start-up; put a large input in the stdin box for meaningful numbers.'));
    } catch (e) { box.textContent = e.message; box.className += ' bad'; }
  };
  CP.actions.validate = async (turn, converted, wrap) => {
    const box = extra(wrap, 'valbox'); if (!box) return;
    box.textContent = 'Running both programs on the same inputs…';
    try {
      const inputs = [...new Set([els.stdin.value, ...CP.usableTests().map(t => t.input)])].slice(0, 4);
      box.innerHTML = ''; let same = 0;
      for (const inp of inputs) {
        const [a, b] = await Promise.all([post('/api/run', { language: turn.lang, code: turn.code, stdin: inp }), post('/api/run', { language: turn.target, code: converted, stdin: inp })]);
        const ok = a.status === 'ok' && b.status === 'ok' && norm(a.stdout) === norm(b.stdout); same += ok;
        const row = el('div', ok ? 'good' : 'bad', `${ok ? '✓' : '✗'} input ${JSON.stringify(inp.slice(0, 40)) || '""'} → ${ok ? 'identical output' : `original: ${a.status} ${JSON.stringify(norm(a.stdout).slice(0, 60))} · converted: ${b.status} ${JSON.stringify(norm(b.stdout).slice(0, 60))}`}`);
        box.appendChild(row);
      }
      box.prepend(el('div', same === inputs.length ? 'good' : 'warnc', `${same} / ${inputs.length} inputs give identical output (real runs of both programs)`));
    } catch (e) { box.textContent = e.message; box.className += ' bad'; }
  };

  /* ═════════ trace (real step-through, Python) ═════════ */
  function showTrace(d) {
    const P = $('#tracePanel'); P.hidden = false; P.innerHTML = '';
    const steps = d.steps || [], V = window.CPViz;
    const head = el('h5'); head.innerHTML = 'Trace <em>· real execution, not an AI guess</em>';
    if (!steps.length) { P.append(head, el('div', 'tinfo', 'Nothing was executed.')); return; }
    let i = 0, timer = 0, pane = 'vars'; const bps = new Set();
    const mk = (label, title) => { const b = el('button', '', label); b.type = 'button'; if (title) b.title = title; return b; };
    const bStart = mk('⏮', 'Back to the start'), bBack = mk('◀ Back'), bInto = mk('Step into ▶', 'Next step'), bOver = mk('Step over ⤼', 'Skip the body of a call'),
      bOut = mk('Step out ⤒', 'Run until this function returns'), bCont = mk('Continue ⏵', 'Run to the next breakpoint (click a line number to set one)'), play = mk('▶ Play'), close = mk('Close');
    const range = el('input'); range.type = 'range'; range.min = 0; range.max = steps.length - 1; range.value = 0;
    const ctl = el('div', 'trace__ctl'); ctl.append(bStart, bBack, bInto, bOver, bOut, bCont, play, range, close);
    const code = el('div', 'tcode'), side = el('div', 'tside'), grid = el('div', 'trace__grid'); grid.append(code, side);
    const lines = d.source.split('\n').map((t, k) => { const r = el('div'); const n = el('i', '', String(k + 1)); n.title = 'Click to toggle a breakpoint';
      n.addEventListener('click', () => { bps.has(k + 1) ? bps.delete(k + 1) : bps.add(k + 1); r.classList.toggle('bp', bps.has(k + 1)); }); r.append(n, document.createTextNode(t || ' ')); code.appendChild(r); return r; });
    const tabs = el('div', 'tpanes'), body = el('div', 'tbody');
    const panes = { vars: 'Variables', ds: 'Data structures', calls: 'Call tree' }, pbtn = {};
    Object.entries(panes).forEach(([k, label]) => { const b = el('button', k === pane ? 'on' : '', label); b.type = 'button'; b.addEventListener('click', () => { pane = k; Object.entries(pbtn).forEach(([n, x]) => x.classList.toggle('on', n === k)); paint(); }); pbtn[k] = b; tabs.appendChild(b); });
    side.append(tabs, body);
    const stack = el('div', 'tstack'), info = el('div', 'tinfo');
    P.append(head, ctl, grid, stack, info);
    function paint() {
      const s = steps[i], prev = i ? steps[i - 1].v : {}; range.value = i;
      lines.forEach((r, k) => r.classList.toggle('cur', k + 1 === s.l));
      const cur = lines[s.l - 1]; if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' });
      body.innerHTML = '';
      if (pane === 'vars') {
        const vars = el('div', 'tvars'); const ents = Object.entries(s.v);
        if (!ents.length) vars.appendChild(el('div', 'tinfo', '(no variables yet)'));
        ents.forEach(([k, v]) => { const r = el('div', JSON.stringify(prev[k]) !== JSON.stringify(v) ? 'chg' : ''); r.append(el('b', '', k), document.createTextNode(V.fmt(v))); vars.appendChild(r); });
        body.appendChild(vars);
      } else if (pane === 'ds') body.appendChild(V.structures(s.v));
      else body.appendChild(V.callTree(steps, i));
      stack.innerHTML = ''; stack.appendChild(el('small', '', 'Call stack:')); s.s.forEach(n => stack.appendChild(el('span', '', n)));
      const what = s.e === 'call' ? `Calling ${s.s[s.s.length - 1] || ''}` : s.e === 'return' ? `Returning ${s.r}` : s.e === 'error' ? `Exception ${s.r}` : `About to run line ${s.l}`;
      info.textContent = `Step ${i + 1} / ${steps.length} · depth ${s.d} · ${what}${bps.size ? ' · breakpoints: ' + [...bps].sort((a, b) => a - b).join(', ') : ''}`;
    }
    const stop = () => { clearInterval(timer); timer = 0; play.textContent = '▶ Play'; };
    const find = (from, test) => { for (let j = from + 1; j < steps.length; j++) if (test(steps[j], j)) return j; return steps.length - 1; };
    const go = n => { stop(); i = Math.max(0, Math.min(steps.length - 1, n)); paint(); };
    bStart.addEventListener('click', () => go(0)); bBack.addEventListener('click', () => go(i - 1)); bInto.addEventListener('click', () => go(i + 1));
    bOver.addEventListener('click', () => { const s = steps[i]; go(s.e === 'call' ? find(i, x => x.e === 'return' && x.d === s.d) : find(i, x => x.d <= s.d)); });
    bOut.addEventListener('click', () => { const s = steps[i]; go(find(i, x => x.d < s.d)); });
    bCont.addEventListener('click', () => go(find(i, x => x.e === 'line' && bps.has(x.l))));
    range.addEventListener('input', () => go(+range.value));
    play.addEventListener('click', () => { if (timer) return stop(); if (i >= steps.length - 1) i = 0; play.textContent = '❚❚ Pause'; timer = setInterval(() => { if (i >= steps.length - 1) return stop(); i++; paint(); }, 380); });
    close.addEventListener('click', () => { stop(); P.hidden = true; });
    paint();
    if (d.truncated) P.appendChild(el('div', 'warnc', 'Trace stopped early (long loop or large data). The program still ran to the end.'));
    if (d.error) P.appendChild(el('div', 'bad', 'Program raised: ' + d.error));
    if (d.stdout) { P.appendChild(el('div', 'tinfo', 'Program output:')); P.appendChild(el('div', 'ex', d.stdout)); }
    if (CP.evidence) CP.evidence.add([{ claim: `Executed and recorded ${steps.length} real step(s) of the program`, status: 'verified', source: 'Live Trace', items: [{ type: 'execution', ref: 'trace', text: `max call depth ${Math.max(...steps.map(x => x.d))}${d.error ? ', raised ' + d.error : ''}` }] }]);
  }
  $('#traceBtn').addEventListener('click', async () => {
    const b = $('#traceBtn'); if (b.disabled) return;
    if (els.lang.value !== 'Python') return flash('Trace works for Python code. Switch the language to Python.');
    if (!ed.get().trim()) return flash('Write some Python code first.');
    b.disabled = true; b.textContent = 'Tracing…';
    try { showTrace(await post('/api/trace', { code: ed.get(), language: 'Python', stdin: els.stdin.value })); usage.bump('traces'); }
    catch (e) { const P = $('#tracePanel'); P.hidden = false; P.innerHTML = ''; P.appendChild(el('div', 'bad', e.message)); }
    finally { b.disabled = false; b.textContent = 'Trace'; }
  });
  CP.hooks.runState = () => { $('#traceBtn').disabled = !(CP.canRun('python')); };

  /* ═════════ AI tests + dependency check ═════════ */
  $('#genTestsBtn').addEventListener('click', async () => {
    const b = $('#genTestsBtn'); if (b.disabled) return;
    if (!ed.get().trim()) return flash('Write some code first.');
    b.disabled = true; b.textContent = 'Thinking…';
    try {
      const d = await post('/api/gen-tests', { code: ed.get(), language: els.lang.value });
      CP.tests.splice(0, CP.tests.length, ...d.tests.slice(0, 8).map(t => ({ input: t.input, expected: t.expected, ai: true, note: t.note })));
      CP.renderTestRows(); flash(`AI suggested ${d.tests.length} tests — expected values are predictions, verify them`);
    } catch (e) { flash(e.message); }
    finally { b.disabled = false; b.textContent = 'AI tests'; }
  });
  $('#depBtn').addEventListener('click', async () => {
    const out = $('#depOut'); out.innerHTML = ''; out.textContent = 'Checking OSV.dev…';
    try {
      const d = await post('/api/deps', { text: $('#depText').value }); out.innerHTML = '';
      const bad = d.checked.filter(p => p.vulns.length);
      out.appendChild(el('div', bad.length ? 'bad' : 'good', bad.length ? `${bad.length} of ${d.checked.length} pinned packages have known vulnerabilities` : `No known vulnerabilities found in ${d.checked.length} pinned packages`));
      const ul = el('ul');
      d.checked.forEach(p => { const li = el('li'); li.append(document.createTextNode(`${p.name} ${p.version}: ${p.vulns.length ? '' : 'clean'}`)); p.vulns.forEach((id, k) => { const a = el('a', '', id + ' '); a.href = p.links[k]; a.target = '_blank'; a.rel = 'noopener'; li.appendChild(a); }); ul.appendChild(li); });
      out.appendChild(ul);
      if (d.unpinned.length) out.appendChild(el('div', 'tinfo', 'Not checked (version not pinned with ==): ' + d.unpinned.join(', ')));
      out.appendChild(el('div', 'tinfo', d.note));
    } catch (e) { out.textContent = e.message; out.className = 'bad'; }
  });

  /* ═════════ accounts + snippet library (local when signed out, cloud when signed in) ═════════ */
  let user = null;
  const LS = 'codephantom.snippets.v1';
  let snips = [];
  const snipWhere = () => user ? 'Saved to your account' : 'Saved on this device — sign in to keep them in your account';
  async function loadSnips() {
    try { snips = user ? (await fetchJSON('/api/snippets')).snippets : jget(LS, []); } catch (_) { snips = []; }
    renderSnips();
  }
  function renderSnips() {
    const q = $('#snipSearch').value.trim().toLowerCase(), proj = $('#snipProject').value;
    const projects = [...new Set(snips.map(s => s.project).filter(Boolean))];
    const sel = $('#snipProject'); const keep = sel.value; sel.innerHTML = '<option value="">All projects</option>';
    projects.forEach(p => sel.add(new Option(p, p))); sel.value = projects.includes(keep) ? keep : '';
    $('#snipWhere').textContent = snipWhere();
    const ul = $('#snipList'); ul.innerHTML = '';
    const list = snips.filter(s => (!q || (s.title + ' ' + s.language + ' ' + s.project + ' ' + s.code).toLowerCase().includes(q)) && (!proj || s.project === proj))
      .sort((a, b) => (b.favorite ? 1 : 0) - (a.favorite ? 1 : 0) || b.updated - a.updated);
    if (!list.length) { ul.appendChild(el('li', '', 'No snippets yet. Press Save to keep the code in the editor.')); return; }
    list.forEach(s => {
      const li = el('li'), star = el('button', s.favorite ? 'on' : '', s.favorite ? '★' : '☆'), name = el('span', '', s.title), meta = el('small', '', `${s.language}${s.project ? ' · ' + s.project : ''}`);
      const open = el('button', '', 'Open'), del = el('button', '', 'Delete'); [star, open, del].forEach(b => { b.type = 'button'; });
      star.addEventListener('click', async () => { await saveSnip({ ...s, favorite: !s.favorite }); });
      open.addEventListener('click', () => { CP.setLanguage(s.language); ed.set(s.code); CP.showTab('code'); flash('Snippet opened'); });
      del.addEventListener('click', async () => {
        if (!confirm('Delete this snippet?')) return;
        try { if (user) await fetchJSON('/api/snippets/' + s.id, { method: 'DELETE', headers: H }); else jset(LS, jget(LS, []).filter(x => x.id !== s.id)); } catch (e) { return flash(e.message); }
        loadSnips();
      });
      li.append(star, name, meta, open, del); ul.appendChild(li);
    });
  }
  async function saveSnip(obj) {
    try {
      if (user) await post('/api/snippets', { id: obj.id, title: obj.title, language: obj.language, project: obj.project, code: obj.code, favorite: obj.favorite });
      else {
        const all = jget(LS, []), i = all.findIndex(x => x.id === obj.id);
        if (i >= 0) all[i] = { ...obj, updated: Date.now() }; else { if (all.length >= 100) throw new Error('Snippet limit reached (100). Delete some first.'); all.push({ ...obj, id: 'l' + Date.now().toString(36), updated: Date.now() }); }
        jset(LS, all);
      }
    } catch (e) { return flash(e.message); }
    loadSnips();
  }
  $('#saveSnipBtn').addEventListener('click', async () => {
    const code = ed.get(); if (!code.trim()) return flash('Nothing to save — the editor is empty.');
    const title = (prompt('Snippet title:', (code.split('\n').find(l => l.trim()) || 'Untitled').slice(0, 50)) || '').trim(); if (!title) return;
    const project = (prompt('Project (optional):', '') || '').trim();
    await saveSnip({ title, language: els.lang.value, project, code, favorite: false }); usage.bump('snippets'); $('#snipPanel').hidden = false; flash('Snippet saved');
  });
  $('#snipsBtn').addEventListener('click', () => { const p = $('#snipPanel'); p.hidden = !p.hidden; if (!p.hidden) loadSnips(); });
  $('#snipSearch').addEventListener('input', renderSnips); $('#snipProject').addEventListener('change', renderSnips);

  const authBox = $('#authModal'); let authMode = 'login';
  function drawAccount() {
    const a = $('#account'); a.innerHTML = '';
    if (user) {
      a.append(el('b', '', user.email), el('small', '', 'Signed in — snippets sync to your account.'));
      const out = el('button', '', 'Sign out'); out.type = 'button'; out.addEventListener('click', signOut); a.appendChild(out);
    } else {
      a.appendChild(el('small', '', 'Optional account: keep snippets in the cloud.'));
      const row = el('div', 'row'), i = el('button', '', 'Sign in'), r = el('button', '', 'Create account'); [i, r].forEach(b => { b.type = 'button'; });
      i.addEventListener('click', () => openAuth('login')); r.addEventListener('click', () => openAuth('register')); row.append(i, r); a.appendChild(row);
    }
  }
  function openAuth(m) {
    authMode = m; $('#authTitle').textContent = m === 'login' ? 'Sign in' : 'Create account'; $('#authSwitch').textContent = m === 'login' ? 'No account yet? Create one' : 'Have an account? Sign in';
    $('#authPass').autocomplete = m === 'login' ? 'current-password' : 'new-password'; $('#authMsg').textContent = ''; authBox.hidden = false; $('#authEmail').focus();
  }
  async function submitAuth() {
    const go = $('#authGo'); go.disabled = true; $('#authMsg').textContent = '';
    try { const d = await post('/api/auth/' + authMode, { email: $('#authEmail').value, password: $('#authPass').value }); user = d.user; authBox.hidden = true; $('#authPass').value = ''; drawAccount(); loadSnips(); flash('Signed in as ' + user.email); }
    catch (e) { $('#authMsg').textContent = e.message; } finally { go.disabled = false; }
  }
  async function signOut() { try { await fetch('/api/auth/logout', { method: 'POST', headers: H }); } catch (_) {} user = null; drawAccount(); loadSnips(); flash('Signed out'); }
  $('#authGo').addEventListener('click', submitAuth); $('#authCancel').addEventListener('click', () => { authBox.hidden = true; });
  $('#authSwitch').addEventListener('click', () => openAuth(authMode === 'login' ? 'register' : 'login'));
  $('#authPass').addEventListener('keydown', e => { if (e.key === 'Enter') submitAuth(); });
  authBox.addEventListener('click', e => { if (e.target === authBox) authBox.hidden = true; });
  drawAccount();
  fetchJSON('/api/auth/me').then(d => { user = d.user; drawAccount(); loadSnips(); }).catch(() => {});

  /* ═════════ coding memory + attach files ═════════ */
  const MEM = 'codephantom.memory.v1', mem = jget(MEM, { text: '', on: false });
  $('#memText').value = mem.text; $('#memOn').checked = mem.on; $('#memState').textContent = mem.on ? 'on' : 'off';
  const saveMem = () => { jset(MEM, { text: $('#memText').value.slice(0, 1500), on: $('#memOn').checked }); $('#memState').textContent = $('#memOn').checked ? 'on' : 'off'; };
  $('#memText').addEventListener('input', saveMem); $('#memOn').addEventListener('change', saveMem);

  function drawChips() {
    const box = $('#fileChips'); box.innerHTML = '';
    CP.attached.forEach((f, k) => { const c = el('span', 'chip', f.name), x = el('button', '', '×'); x.type = 'button'; x.addEventListener('click', () => { CP.attached = CP.attached.filter((_, n) => n !== k); drawChips(); }); c.appendChild(x); box.appendChild(c); });
  }
  $('#attachBtn').addEventListener('click', () => $('#fileIn').click());
  $('#fileIn').addEventListener('change', async e => {
    const list = [...e.target.files].slice(0, 12); let total = CP.attached.reduce((n, f) => n + f.text.length, 0); const next = [...CP.attached];
    for (const f of list) {
      const text = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = () => res(null); r.readAsText(f); });
      if (text == null || text.includes('\u0000')) { flash(`${f.name} looks like a binary file — skipped`); continue; }
      if (total + text.length > 60000) { flash('File size limit reached (60 KB total) — some files were skipped'); break; }
      total += text.length; next.push({ name: f.name, text });
    }
    CP.attached = next.slice(0, 12); drawChips(); e.target.value = '';
  });

  /* ═════════ practice (DSA) + interview pressure mode ═════════ */
  const TOPICS = ['Arrays', 'Strings', 'Hashing', 'Linked lists', 'Stacks and queues', 'Trees', 'Graphs', 'Dynamic programming', 'Sorting and searching', 'Recursion', 'Math', 'Two pointers'];
  TOPICS.forEach(t => $('#pTopic').add(new Option(t, t)));
  const PROG = 'codephantom.progress.v1';
  let problem = null, hints = 0, timer = 0, endsAt = 0, startedAt = 0;
  const progress = () => jget(PROG, { problems: {} });
  function record(p, fn) {
    const pr = progress(), cur = pr.problems[p.id] || { topic: p.topic, difficulty: p.difficulty, title: p.title, attempts: 0, fails: 0, solved: false, revealed: false, hints: 0, ts: Date.now() };
    fn(cur); cur.ts = Date.now(); pr.problems[p.id] = cur;
    const keys = Object.keys(pr.problems); if (keys.length > 300) keys.sort((a, b) => pr.problems[a].ts - pr.problems[b].ts).slice(0, keys.length - 300).forEach(k => delete pr.problems[k]);
    jset(PROG, pr);
  }
  const MINUTES = { Easy: 20, Medium: 30, Hard: 45 };
  const pressure = () => $('#pPressure').checked;
  function stopTimer() { clearInterval(timer); timer = 0; }
  function startTimer(p) {
    stopTimer(); const t = $('#pTimer'); t.textContent = '';
    if (!pressure()) return;
    endsAt = Date.now() + (MINUTES[p.difficulty] || 30) * 60000;
    const tick = () => {
      const left = Math.max(0, endsAt - Date.now()), m = Math.floor(left / 60000), s = Math.floor(left / 1000) % 60;
      t.textContent = left ? `⏱ ${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : '⏱ TIME UP';
      if (!left) { stopTimer(); flash("Time's up! Submit what you have."); }
    };
    tick(); timer = setInterval(tick, 1000);
  }
  $('#pPressure').addEventListener('change', () => { if (!pressure()) { stopTimer(); $('#pTimer').textContent = ''; } else if (problem) startTimer(problem); });
  const COMMENT = { Python: '#', Ruby: '#', Bash: '#', R: '#', SQL: '--' };

  async function loadProblem(fn) {
    const body = $('#pBody'); body.innerHTML = ''; body.appendChild(el('div', 'welcome', 'Creating a problem and verifying it by running a reference solution… this takes 10–20 seconds.'));
    [$('#pNew'), $('#pDaily')].forEach(b => { b.disabled = true; });
    try { const d = await fn(); problem = d.problem; hints = 0; startedAt = Date.now(); drawProblem(); startTimer(problem); usage.bump('practice'); }
    catch (e) { body.innerHTML = ''; body.appendChild(el('div', 'msg--err', e.message)); }
    finally { [$('#pNew'), $('#pDaily')].forEach(b => { b.disabled = false; }); }
  }
  $('#pNew').addEventListener('click', () => loadProblem(() => post('/api/practice/new', { topic: $('#pTopic').value, difficulty: $('#pDiff').value })));
  $('#pDaily').addEventListener('click', () => loadProblem(() => fetchJSON('/api/practice/daily')));

  function drawProblem() {
    const p = problem, b = $('#pBody'); b.innerHTML = '';
    b.appendChild(el('h3', '', p.title));
    const badges = el('div', 'badges'); [p.topic, p.difficulty, p.daily ? 'Daily challenge ' + p.daily : '', 'Tests verified by execution'].filter(Boolean).forEach(t => badges.appendChild(el('span', '', t))); b.appendChild(badges);
    b.appendChild(CP.md(p.statement));
    if (p.input_format) b.appendChild(el('p', 'tinfo', 'Input: ' + p.input_format)); if (p.output_format) b.appendChild(el('p', 'tinfo', 'Output: ' + p.output_format));
    p.tests.slice(0, 2).forEach((t, k) => { const ex = el('div', 'ex'); ex.append(el('i', '', `EXAMPLE ${k + 1} · INPUT`), document.createTextNode(t.input), el('i', '', 'OUTPUT'), document.createTextNode(t.expected)); b.appendChild(ex); });
    const act = el('div', 'pact'), hintBox = el('div'), solBox = el('div'), resBox = el('div'); resBox.id = 'pResult';
    const mk = (label, cls, fn) => { const x = el('button', cls || 'ghostbtn', label); x.type = 'button'; x.addEventListener('click', () => fn(x)); act.appendChild(x); return x; };
    mk('Load into editor', 'ghostbtn', () => {
      const c = COMMENT[els.lang.value] || '//', head = [`Problem: ${p.title}`, `Input: ${p.input_format}`, `Output: ${p.output_format}`, 'Read from standard input, print to standard output.'].map(l => `${c} ${l}`).join('\n') + '\n\n';
      ed.set(head); CP.tests.splice(0, CP.tests.length, ...p.tests.slice(0, 2).map(t => ({ input: t.input, expected: t.expected }))); CP.renderTestRows(); CP.showTab('code'); flash('Problem loaded — write your solution, then come back and Submit');
    });
    mk('Submit', 'go', async x => {
      const code = ed.get(), lang = els.lang.value;
      if (!code.trim() || /^\s*(#|\/\/|--)/.test(code) && code.split('\n').every(l => !l.trim() || /^\s*(#|\/\/|--)/.test(l))) return flash('Write your solution in Code & Run first.');
      if (!CP.canRun(lang)) return flash(`${lang} cannot be run on this server — switch language.`);
      x.disabled = true; x.textContent = 'Judging…'; resBox.textContent = 'Running all tests…';
      try {
        const d = await post('/api/test', { language: lang, code, tests: p.tests.map(t => ({ input: t.input, expected: t.expected })) });
        CP.renderTestResults(d, resBox, idx => idx <= 2);
        const all = !d.compile_error && d.passed === d.total;
        record(p, c => { c.attempts++; if (all) c.solved = true; else c.fails++; });
        if (all) {
          stopTimer(); const secs = Math.round((Date.now() - startedAt) / 1000);
          resBox.prepend(el('div', 'summary ok', `🎉 Solved in ${Math.floor(secs / 60)}m ${secs % 60}s${hints ? ` with ${hints} hint${hints > 1 ? 's' : ''}` : ''}`));
          const r1 = el('button', 'ghostbtn', 'AI complexity review'), r2 = el('button', 'ghostbtn', 'Mock interview on my solution'); [r1, r2].forEach(z => { z.type = 'button'; });
          r1.addEventListener('click', () => CP.runTurn({ mode: 'complexity', lang, target: '', level: '', prompt: '', code }));
          r2.addEventListener('click', () => interview(p, true)); const row = el('div', 'pact'); row.append(r1, r2); resBox.appendChild(row);
        }
        usage.bump('submits');
      } catch (e) { resBox.textContent = e.message; } finally { x.disabled = false; x.textContent = 'Submit'; }
    });
    const hb = mk('Hint (0/3)', 'ghostbtn', () => {
      if (timer) return flash('Hints are disabled in pressure mode.');
      if (hints >= 3) return; hintBox.appendChild(el('div', 'hint', `Hint ${hints + 1}: ${p.hints[hints]}`)); hints++; hb.textContent = `Hint (${hints}/3)`; record(p, c => { c.hints = Math.max(c.hints, hints); });
    });
    mk('Reveal solution', 'ghostbtn', () => {
      if (timer) return flash('The solution is locked in pressure mode.');
      if (!confirm('Reveal the reference solution? This problem will not count as solved.')) return;
      record(p, c => { c.revealed = true; });
      solBox.innerHTML = ''; solBox.appendChild(CP.md(`**Reference solution (Python)**\n\n\`\`\`python\n${p.solution}\n\`\`\`\n\n${p.explanation}\n\n**Complexity:** time ${p.time_complexity}, space ${p.space_complexity}`));
    });
    mk('Start mock interview', 'ghostbtn', () => interview(p, false));
    b.append(act, hintBox, solBox, resBox);
  }
  function interview(p, withCode) {
    CP.setMode('interview');
    CP.runTurn({ mode: 'interview', lang: els.lang.value, target: '', level: '', code: withCode ? ed.get() : '',
      prompt: `Mock interview. We are discussing this problem: ${p.title}\n\n${p.statement}\n\nI'm ready. ${withCode ? 'I solved it — ask me follow-up questions about my solution (complexity, edge cases, alternatives).' : 'Ask me your first question.'}` });
  }

  /* ═════════ dashboard ═════════ */
  function bars(rows) {
    const box = el('div');
    rows.forEach(([label, a, b]) => { const r = el('div', 'bar'); const i = el('i'), u = el('u'); u.style.width = b ? Math.round(100 * a / b) + '%' : '0%'; i.appendChild(u); r.append(el('span', '', label), i, el('span', '', `${a}/${b}`)); box.appendChild(r); });
    return box;
  }
  function renderDash() {
    const d = $('#dashBody'); d.innerHTML = '';
    const u = usage.all(), pr = Object.values(progress().problems), hist = jget('codephantom.history.v1', []);
    const sec = (title, node) => { const s = el('div'); s.appendChild(el('h4', '', title)); s.appendChild(node); d.appendChild(s); };
    const cards = el('div', 'cards');
    [['AI answers', u.ai || 0], ['Code runs', u.runs || 0], ['Test runs', u.tests || 0], ['Traces', u.traces || 0], ['Voice requests', u.voice || 0], ['Snippets saved', u.snippets || 0], ['Missions saved', hist.length], ['Problems solved', pr.filter(p => p.solved).length]]
      .forEach(([k, v]) => { const c = el('div', 'card2'); c.append(el('b', '', String(v)), el('span', '', k)); cards.appendChild(c); });
    sec('Usage (this browser)', cards);
    if (!pr.length) sec('Practice progress', el('p', 'tinfo', 'No practice yet. Open the Practice tab and solve a problem or today\'s challenge.'));
    else {
      const solved = pr.filter(p => p.solved).length;
      sec('Practice progress', el('p', '', `${solved} solved out of ${pr.length} attempted problems (${Math.round(100 * solved / pr.length)}% success rate)`));
      sec('By difficulty', bars(['Easy', 'Medium', 'Hard'].map(k => [k, pr.filter(p => p.difficulty === k && p.solved).length, pr.filter(p => p.difficulty === k).length])));
      const topics = [...new Set(pr.map(p => p.topic))].map(t => { const ps = pr.filter(p => p.topic === t); return { t, n: ps.length, s: ps.filter(p => p.solved).length, f: ps.reduce((a, p) => a + p.fails, 0) }; });
      sec('By topic', bars(topics.map(x => [x.t, x.s, x.n])));
      const weak = topics.filter(x => (x.n >= 2 && x.s / x.n < 0.5) || x.f >= 3).sort((a, b) => (b.f + b.n - b.s) - (a.f + a.n - a.s)).slice(0, 3);
      const w = el('div');
      if (!weak.length) w.appendChild(el('p', 'good', 'No weak topics detected yet — keep going!'));
      weak.forEach(x => { const r = el('div', 'pact'), b = el('button', 'ghostbtn', `Practice ${x.t}`); b.type = 'button'; r.append(el('span', 'warnc', `${x.t}: ${x.s}/${x.n} solved, ${x.f} failed submissions`), b); b.addEventListener('click', () => { $('#pTopic').value = x.t; CP.showTab('practice'); }); w.appendChild(r); });
      sec('Weak topics', w);
    }
    const recent = el('div'); hist.slice(0, 5).forEach(h => recent.appendChild(el('p', 'tinfo', `${(MODES[h.mode] || {}).name || h.mode} · ${h.title}`)));
    if (!hist.length) recent.appendChild(el('p', 'tinfo', 'No missions yet.'));
    sec('Recent missions', recent);
    sec('Account', el('p', 'tinfo', user ? 'Signed in as ' + user.email : 'Not signed in (optional).'));
  }
  CP.hooks.dash = renderDash;

  /* ═════════ command palette + keyboard shortcuts ═════════ */
  const pal = { box: $('#palette'), input: $('#palInput'), list: $('#palList') };
  let palItems = [], palSel = 0;
  function commands() {
    const go = t => () => CP.showTab(t), c = [];
    Object.entries(MODES).filter(([, m]) => !m.hidden).forEach(([k, m]) => c.push({ label: 'Mode: ' + m.name, hint: 'AI', run: () => CP.setMode(k) }));
    c.push({ label: 'Go to AI Assistant', hint: 'Alt+1', run: go('assist') }, { label: 'Go to Code & Run', hint: 'Alt+2', run: go('code') }, { label: 'Go to Practice', hint: 'Alt+3', run: go('practice') }, { label: 'Go to Projects', hint: 'Alt+4', run: go('projects') }, { label: 'Go to Labs', hint: 'Alt+5', run: go('labs') }, { label: 'Go to Dashboard', hint: 'Alt+6', run: go('dash') },
      { label: 'Run code', hint: 'Ctrl+Enter', run: () => { CP.showTab('code'); CP.runCode(); } }, { label: 'Run tests', run: () => { CP.showTab('code'); els.testBtn.click(); } },
      { label: 'Trace Python step by step', run: () => { CP.showTab('code'); $('#traceBtn').click(); } }, { label: 'AI-suggested tests', run: () => { CP.showTab('code'); $('#genTestsBtn').click(); } },
      { label: 'New mission', run: () => els.newChat.click() }, { label: 'Save snippet', run: () => { CP.showTab('code'); $('#saveSnipBtn').click(); } }, { label: 'Open snippet library', run: () => { CP.showTab('code'); $('#snipPanel').hidden = false; loadSnips(); } },
      { label: 'Toggle full-screen editor', run: toggleFullscreen }, { label: 'Toggle split view', run: toggleSplit }, { label: 'Toggle light / dark theme', run: toggleTheme }, { label: 'Toggle reduced motion', run: toggleMotion },
      { label: 'Export report (.md)', run: () => CP.exportReport() }, { label: 'Clear history', run: () => els.clearHistory.click() }, { label: 'New practice problem', run: () => { CP.showTab('practice'); $('#pNew').click(); } },
      { label: 'Daily challenge', run: () => { CP.showTab('practice'); $('#pDaily').click(); } }, { label: 'Focus the prompt box', run: () => { CP.showTab('assist'); els.prompt.focus(); } },
      user ? { label: 'Sign out', run: signOut } : { label: 'Sign in / create account', run: () => openAuth('login') }, ...(CP.extraCommands || []));
    return c;
  }
  function drawPal() {
    const words = pal.input.value.toLowerCase().split(/\s+/).filter(Boolean);
    palItems = commands().filter(c => words.every(w => (c.label + ' ' + (c.hint || '')).toLowerCase().includes(w)));
    palSel = Math.min(palSel, Math.max(0, palItems.length - 1)); pal.list.innerHTML = '';
    palItems.forEach((c, k) => { const li = el('li', k === palSel ? 'sel' : ''); li.append(el('span', '', c.label), el('small', '', c.hint || '')); li.addEventListener('click', () => exec(k)); pal.list.appendChild(li); });
    if (!palItems.length) pal.list.appendChild(el('li', '', 'No matching command'));
  }
  function openPal() { pal.box.hidden = false; pal.input.value = ''; palSel = 0; drawPal(); pal.input.focus(); }
  function closePal() { pal.box.hidden = true; }
  function exec(k) { const c = palItems[k]; if (!c) return; closePal(); c.run(); }
  pal.input.addEventListener('input', () => { palSel = 0; drawPal(); });
  pal.input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); palSel = Math.min(palItems.length - 1, palSel + 1); drawPal(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); palSel = Math.max(0, palSel - 1); drawPal(); }
    else if (e.key === 'Enter') { e.preventDefault(); exec(palSel); }
  });
  pal.box.addEventListener('click', e => { if (e.target === pal.box) closePal(); });
  $('#paletteBtn').addEventListener('click', openPal);
  window.addEventListener('keydown', e => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); e.stopPropagation(); pal.box.hidden ? openPal() : closePal(); }
    else if (e.key === 'Escape') { if (!pal.box.hidden) closePal(); else if (!authBox.hidden) authBox.hidden = true; else if (els.editorWrap.classList.contains('fs')) toggleFullscreen(); }
    else if (e.altKey && ['1', '2', '3', '4', '5', '6'].includes(e.key)) { e.preventDefault(); CP.showTab(['assist', 'code', 'practice', 'projects', 'labs', 'dash'][+e.key - 1]); }
    else if (mod && e.key === 'Enter' && !els.viewCode.hidden && document.activeElement !== els.prompt) { e.preventDefault(); CP.runCode(); }
  }, true);

  CP.lineDiff = lineDiff;
  CP.hooks.runState && CP.hooks.runState();
})();

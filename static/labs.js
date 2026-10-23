/* CODEPHANTOM AI — Labs tab: Proof Mode, Review Tribunal, Spec → Test, Merge Resolver, Security Attack Simulator. */
(() => {
  const CP = window.CP, L = window.CPLab; if (!CP || !L) return;
  const { $, el, els, ed, flash, usage } = CP;
  const { api, stream, btn, badge, sec, note, busy, fail } = L;
  const root = $('#labRoot'), SUBS = [['proof', 'Proof Mode'], ['tribunal', 'Review Tribunal'], ['spec', 'Spec → Test'], ['merge', 'Merge Resolver'], ['attack', 'Attack Simulator']];
  let sub = 'proof', built = false, body;

  const colorDiff = text => { const pre = el('pre', 'udiff'); text.split('\n').forEach(l => pre.appendChild(el('div', l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : l.startsWith('@@') ? 'hunk' : '', l || ' '))); return pre; };
  const code = () => ed.get().replace(/\s+$/, '');
  const useInfo = () => { const c = code(), w = el('div', 'useinfo'); w.append(document.createTextNode(c ? `Using ${els.lang.value} code from the editor · ${c.split('\n').length} lines · ` : 'The editor is empty — paste code in Code & Run first. '), btn('Open the editor', 'ghostbtn', () => CP.showTab('code'))); return w; };
  const need = () => { if (!code()) { flash('Put some code in the Code & Run editor first.'); return false; } return true; };
  const progress = () => { const p = el('div', 'progress'); p.set = t => { p.textContent = t; }; return p; };
  const sevCls = s => (s === 'critical' || s === 'high' ? 'bad' : s === 'medium' ? 'warn' : 'good');

  function shell() {
    root.innerHTML = ''; const tabs = el('div', 'subtabs'); tabs.id = 'labTabs';
    SUBS.forEach(([k, label]) => { const b = el('button', k === sub ? 'on' : '', label); b.type = 'button'; b.dataset.sub = k; b.addEventListener('click', () => openSub(k)); tabs.appendChild(b); });
    body = el('div', 'lbody'); body.id = 'labBody'; root.append(tabs, body);
  }
  function openSub(k) { sub = k; CP.showTab('labs'); document.querySelectorAll('#labTabs button').forEach(b => b.classList.toggle('on', b.dataset.sub === k)); render(); }
  function render() { if (!body) return; body.innerHTML = ''; ({ proof, tribunal, spec, merge, attack }[sub])(body); }
  CP.hooks.labs = () => { if (!built) { built = true; shell(); } render(); };

  /* ═════════ Proof Mode ═════════ */
  function proof(b) {
    const desc = el('textarea'); desc.id = 'pfDesc'; desc.rows = 2; desc.placeholder = 'Optional: what must this code do? e.g. "adds two integers read from stdin"'; const out = el('div'); out.id = 'pfOut';
    const run = btn('Prove it works', 'go', bt => busy(bt, 'Proving…', async () => {
      if (!need()) return; out.innerHTML = ''; const p = progress(); out.appendChild(p);
      try { const d = await stream('/api/labs/proof', { code: code(), language: els.lang.value, description: desc.value }, ev => { if (ev.step) p.set(ev.msg); }); CP.evidence.add(d.evidence); usage.bump('proof'); report(d, out); } catch (e) { fail(out, e); } }));
    b.append(sec('Phantom Proof Mode — show me that it works', note('The AI designs normal, boundary and edge-case tests, the code is really executed against them, and every disagreement between expected and actual output is shown. Expected values are AI predictions; the actual results are real.'), useInfo(), desc, run), out);
  }
  function report(d, out) {
    out.innerHTML = ''; const ok = d.passed === d.total;
    const cards = el('div', 'cards'); [['Tests', d.total], ['Passing', d.passed], ['Failing', d.total - d.passed], ['Mode', d.mode]].forEach(([k, v]) => { const c = el('div', 'card2'); c.append(el('b', '', String(v)), el('span', '', k)); cards.appendChild(c); });
    out.append(el('div', 'summary ' + (ok ? 'ok' : 'bad'), ok ? `All ${d.total} tests pass (real execution)` : `${d.passed} of ${d.total} tests pass after the last round`), cards);
    d.rounds.forEach(r => {
      const box = el('div'); if (r.compile_error) box.appendChild(el('div', 'lerr', 'Compile error: ' + r.compile_error.stderr.slice(0, 400)));
      r.results.forEach(t => { const row = el('div', 'rrow'), h = el('header'); h.append(el('b', t.passed ? 'ok' : 'bad', t.passed ? 'PASS' : 'FAIL'), badge(t.category, ''), el('span', '', t.name), el('span', '', `${t.status}${t.time_ms != null ? ' · ' + t.time_ms + ' ms' : ''}`)); row.appendChild(h);
        const pre = el('pre'); if (t.mode === 'unit') pre.append(el('i', '', 'TEST CODE'), document.createTextNode(t.snippet), el('i', '', 'RESULT'), document.createTextNode(t.passed ? 'assertion held' : (t.actual || t.stderr || 'failed')));
        else pre.append(el('i', '', 'INPUT'), document.createTextNode(t.input || '(none)'), el('i', '', 'EXPECTED (AI prediction)'), document.createTextNode(t.expected), el('i', '', 'ACTUAL (real run)'), document.createTextNode(t.actual || '(empty)'), ...(t.stderr ? [el('i', '', 'STDERR'), document.createTextNode(t.stderr)] : []));
        row.appendChild(pre); box.appendChild(row); });
      out.appendChild(sec(r.label, box)); });
    if (d.disagreements.length) { const dg = el('div'); d.disagreements.forEach(x => dg.appendChild(el('div', 'lerr', `${x.test}: expected ${JSON.stringify(x.expected)} but the program printed ${JSON.stringify(x.actual)}`))); out.appendChild(sec('Disagreement between expected and actual output', dg)); }
    if (d.verdicts.length) { const v = el('div'); d.verdicts.forEach(x => { const c = el('div', 'hit'); c.append(badge(x.cause, x.cause === 'code_bug' ? 'bad' : x.cause === 'wrong_expectation' ? 'warn' : ''), badge('AI', 'ai'), el('b', '', ' ' + x.test), note(x.explanation)); v.appendChild(c); }); out.appendChild(sec('Who is wrong? (AI diagnosis)', v)); }
    if (d.fixed_code) out.appendChild(sec('Fix (all tests re-run on it)', colorDiff(d.diff), btn('Apply the fixed code to the editor', 'go', () => { ed.set(d.fixed_code); flash('Fixed code applied'); CP.showTab('code'); })));
    else if (d.summary) out.appendChild(note(d.summary));
  }

  /* ═════════ Review Tribunal ═════════ */
  function tribunal(b) {
    const ctx = el('textarea'); ctx.id = 'trCtx'; ctx.rows = 2; ctx.placeholder = 'Optional context: what changed, where this code runs…'; const out = el('div'); out.id = 'trOut';
    const run = btn('Convene the tribunal', 'go', bt => busy(bt, 'Reviewing…', async () => {
      if (!need()) return; out.innerHTML = ''; const p = progress(); out.appendChild(p);
      try { const d = await stream('/api/labs/tribunal', { code: code(), language: els.lang.value, context: ctx.value }, ev => { if (ev.step) p.set(ev.msg); }); CP.evidence.add(d.evidence); usage.bump('tribunal'); tribReport(d, out); } catch (e) { fail(out, e); } }));
    b.append(sec('AI Code Review Tribunal', note('Three specialists review the same code — correctness, performance, security. Conflicts between them are shown, not hidden. Each finding\'s quoted line is checked against your real code.'), useInfo(), ctx, run), out);
  }
  function tribReport(d, out) {
    out.innerHTML = ''; const grid = el('div', 'tribgrid');
    d.reviews.forEach(r => { const col = el('div', 'tcol'), h = el('header');
      if (r.error) { h.append(el('b', '', r.role.toUpperCase()), badge('failed', 'bad')); col.append(h, el('div', 'lerr', r.error)); grid.appendChild(col); return; }
      h.append(el('b', '', r.role.toUpperCase()), badge(r.verdict.replace('_', ' '), r.verdict === 'approve' ? 'good' : r.verdict === 'reject' ? 'bad' : 'warn')); col.append(h, note(r.summary));
      if (!r.findings.length) col.appendChild(note('No findings in this reviewer\'s area.', 'good'));
      r.findings.forEach(f => { const c = el('div', 'hit'); c.append(badge(f.severity, sevCls(f.severity)), el('b', '', ' ' + f.title));
        if (f.line) { const ln = btn(`line ${f.line}`, '', () => { CP.showTab('code'); ed.goto(f.line); }); ln.className = 'linkbtn'; c.append(ln, badge(f.location_verified ? 'location verified ✔' : 'location NOT verified ⚠', f.location_verified ? 'good' : 'warn')); if (f.actual_line) c.appendChild(el('pre', '', f.actual_line)); }
        c.appendChild(note(f.detail)); if (f.suggestion) c.appendChild(note('Suggestion: ' + f.suggestion, 'good')); col.appendChild(c); }); grid.appendChild(col); });
    out.appendChild(grid);
    if (d.consensus) out.appendChild(sec('Consensus (AI judge)', el('p', '', d.consensus)));
    const dis = el('div'); d.disagreements.forEach(x => { const c = el('div', 'disagree'); c.append(el('b', '', '⚡ ' + x.topic), badge(x.source, x.source === 'judge' ? 'ai' : 'st')); (x.positions || []).forEach(p => c.appendChild(el('div', 'tinfo', `${p.reviewer}: ${p.stance}`))); if (x.advice) c.appendChild(note('Advice: ' + x.advice, 'good')); dis.appendChild(c); });
    out.appendChild(sec(`Disagreements (${d.disagreements.length})`, d.disagreements.length ? dis : note('The reviewers do not conflict.', 'good')));
    if (d.priority.length) { const ol = el('ol'); d.priority.forEach(p => ol.appendChild(el('li', '', p))); out.appendChild(sec('Fix in this order', ol)); }
    out.appendChild(note(`${d.verified_locations} of ${d.total_findings} finding locations were verified against the real code.`));
  }

  /* ═════════ Spec → Test ═════════ */
  function spec(b) {
    const req = el('textarea'); req.id = 'spReq'; req.rows = 5; req.placeholder = 'One requirement per line, e.g.\nA withdrawal must never make the balance negative\nDepositing a negative amount must be rejected'; const out = el('div'); out.id = 'spOut';
    const run = btn('Generate tests & check', 'go', bt => busy(bt, 'Checking…', async () => {
      if (!need()) return; out.innerHTML = ''; const p = progress(); out.appendChild(p);
      try { const d = await stream('/api/labs/spec', { code: code(), language: els.lang.value, requirements: req.value }, ev => { if (ev.step) p.set(ev.msg); }); CP.evidence.add(d.evidence); usage.bump('spec'); specReport(d, out); } catch (e) { fail(out, e); } }));
    b.append(sec('Specification → tests', note('Describe the required behaviour. Tests are generated per requirement and really executed against the code in the editor: each requirement ends satisfied, violated, or untestable.'), useInfo(), req, run), out);
  }
  function specReport(d, out) {
    out.innerHTML = ''; out.appendChild(el('div', 'summary ' + (d.violated ? 'bad' : 'ok'), `${d.satisfied} satisfied · ${d.violated} violated · ${d.untestable} not testable`));
    d.requirements.forEach(r => { const c = el('div', 'hit req-' + r.verdict); c.append(el('b', '', r.id + '  '), badge(r.verdict, r.verdict === 'satisfied' ? 'good' : r.verdict === 'violated' ? 'bad' : 'warn'), el('div', '', r.text));
      if (r.verdict === 'untestable') c.appendChild(note(r.reason, 'warnc'));
      r.tests.forEach(t => { const row = el('div', 'rrow'), h = el('header'); h.append(el('b', t.passed ? 'ok' : 'bad', t.passed ? 'PASS' : 'FAIL'), el('span', '', t.name)); row.appendChild(h);
        const pre = el('pre'); if (t.mode === 'unit') pre.append(el('i', '', 'TEST CODE'), document.createTextNode(t.snippet), ...(t.passed ? [] : [el('i', '', 'FAILURE'), document.createTextNode(t.actual || t.stderr)])); else pre.append(el('i', '', 'INPUT'), document.createTextNode(t.input), el('i', '', 'EXPECTED'), document.createTextNode(t.expected), el('i', '', 'ACTUAL'), document.createTextNode(t.actual)); row.appendChild(pre); c.appendChild(row); });
      out.appendChild(c); });
  }

  /* ═════════ Merge Resolver ═════════ */
  function merge(b) {
    const txt = el('textarea'); txt.id = 'mgText'; txt.rows = 10; txt.spellcheck = false; txt.placeholder = 'Paste the conflicted file, including the <<<<<<< ======= >>>>>>> markers'; txt.value = '';
    const ours = el('textarea'), theirs = el('textarea'); ours.id = 'mgOurs'; theirs.id = 'mgTheirs'; ours.rows = theirs.rows = 5; ours.placeholder = 'Our version'; theirs.placeholder = 'Their version';
    const build = btn('Build conflict markers from the two versions', 'ghostbtn', async bt => busy(bt, 'Building…', async () => { try { const d = await api('/api/labs/merge/build', { ours: ours.value, theirs: theirs.value }); txt.value = d.content; flash(`${d.conflicts} conflict(s) found`); } catch (e) { flash(e.message); } }));
    const det = el('details'); det.append(el('summary', '', 'Don\'t have conflict markers? Paste both versions instead'), ours, theirs, build); const out = el('div'); out.id = 'mgOut';
    const run = btn('Explain and resolve', 'go', bt => busy(bt, 'Resolving…', async () => {
      out.innerHTML = ''; const p = progress(); out.appendChild(p);
      try { const d = await stream('/api/labs/merge', { code: txt.value, language: els.lang.value, tests: CP.usableTests() }, ev => { if (ev.step) p.set(ev.msg); }); CP.evidence.add(d.evidence); usage.bump('merge'); mergeReport(d, out); } catch (e) { fail(out, e); } }));
    b.append(sec('AI Merge Conflict Resolver', note(`Language: ${els.lang.value}. The merged file is checked for syntax and, if the Code tab has test cases, ours, theirs and the merged result are all really run before a recommendation is made.`), txt, det, run), out);
  }
  function mergeReport(d, out) {
    out.innerHTML = ''; const rec = d.recommendation;
    out.appendChild(el('div', 'summary ' + (rec === 'accept' ? 'ok' : 'bad'), rec === 'accept' ? 'Recommendation: accept the merge' : rec === 'review' ? 'Recommendation: review manually before accepting' : 'Recommendation: do not accept as is'));
    d.reasons.forEach(r => out.appendChild(note(r, rec === 'accept' ? 'good' : 'warnc')));
    out.appendChild(sec('Why the conflict happened (AI)', el('p', '', d.explanation || '(no explanation)')));
    const hs = el('div'); d.hunks.forEach(h => { const c = el('div', 'hit'), g = el('div', 'tribgrid'); [['Ours', h.ours], ['Theirs', h.theirs], ['Resolution', h.resolution]].forEach(([k, v]) => { const col = el('div', 'tcol'); col.append(el('b', '', k), el('pre', '', v || '(empty)')); g.appendChild(col); });
      c.append(el('b', '', `Conflict ${h.id + 1} `), badge(h.strategy, ''), badge('AI', 'ai'), note(h.why), g); hs.appendChild(c); }); out.appendChild(sec('Conflicts and proposed resolutions', hs));
    const v = el('div'); ['merged', 'ours', 'theirs'].forEach(k => v.appendChild(note(`${k}: ${d.syntax[k].message}${d.tests && d.tests[k] != null ? ` · tests ${d.tests[k]}/${d.tests.count}` : ''}`, d.syntax[k].ok === false ? 'bad' : '')));
    out.appendChild(sec('Verification (real checks)', v));
    const res = el('textarea'); res.rows = 10; res.readOnly = true; res.value = d.merged; res.id = 'mgMerged';
    out.appendChild(sec('Merged file', res, btn('Use the merged file in the editor', 'go', () => { ed.set(d.merged); flash('Merged code placed in the editor'); CP.showTab('code'); })));
  }

  /* ═════════ Attack Simulator ═════════ */
  function attack(b) {
    const where = el('select'); where.id = 'atSource'; where.add(new Option('Code in the editor', 'editor')); const pj = L.project && L.project(); if (pj) where.add(new Option('Project: ' + pj.name, pj.id));
    const out = el('div'), demo = el('div'); out.id = 'atScan'; demo.id = 'atDemo';
    const run = btn('Scan for weaknesses', 'go', bt => busy(bt, 'Scanning…', async () => {
      out.innerHTML = ''; try { const body = where.value === 'editor' ? (need() ? { code: code(), language: els.lang.value } : null) : { project_id: where.value }; if (!body) return;
        const d = await api('/api/labs/attack/scan', body); CP.evidence.add(d.evidence); usage.bump('attack');
        out.appendChild(el('div', 'summary ' + (d.findings.length ? 'bad' : 'ok'), d.findings.length ? `${d.findings.length} potential weakness(es) found by rule-based scanning` : 'No known weakness patterns found (this does not prove the code is safe)'));
        d.findings.forEach(f => { const c = el('div', 'hit'); c.append(badge(f.severity, sevCls(f.severity)), badge('static', 'st'), el('b', '', ` ${f.name} `), el('small', '', `${f.cwe} · ${f.path}:${f.line}`), el('pre', '', f.snippet), note('Why it matters: ' + f.why), note('Remediation: ' + f.fix, 'good'));
          const row = el('div', 'pact'); if (f.demo) row.appendChild(btn('Harmless demonstration', 'ghostbtn', async bt2 => busy(bt2, 'Running…', async () => runDemo(f.demo))));
          row.appendChild(btn('Fix with AI', 'ghostbtn', () => CP.runTurn(where.value === 'editor' ? { mode: 'security', lang: els.lang.value, target: '', level: '', prompt: `Focus on this finding and give the fixed code: ${f.name} at line ${f.line}: ${f.snippet}`, code: code() } : { mode: 'ask', lang: els.lang.value, target: '', level: '', prompt: `In ${f.path} line ${f.line} a scanner found "${f.name}": ${f.snippet}\nExplain the risk and show the safer code.`, code: '' }))); c.appendChild(row); out.appendChild(c); }); } catch (e) { fail(out, e); } }));
    async function runDemo(k) { demo.innerHTML = ''; try { const d = await api('/api/labs/attack/demo', { demo: k }); CP.evidence.add(d.evidence); demo.append(sec('Demonstration: ' + d.title, note(d.explanation), el('pre', 'ex', d.output || d.stderr), note(d.note, 'warnc'))); demo.scrollIntoView && demo.scrollIntoView({ block: 'nearest' }); } catch (e) { fail(demo, e); } }
    const url = el('input'); url.id = 'atUrl'; url.value = 'http://127.0.0.1:5000/'; url.placeholder = 'http://127.0.0.1:PORT/path?param=value'; const param = el('input'); param.id = 'atParam'; param.placeholder = 'Parameter names to test, comma separated (optional)';
    const auth = el('input'); auth.type = 'checkbox'; auth.id = 'atAuth'; const al = el('label', 'chk'); al.append(auth, document.createTextNode('I own this application and I am allowed to test it.')); const pout = el('div'); pout.id = 'atProbe';
    const probe = btn('Run local probe', 'go', bt => busy(bt, 'Probing…', async () => {
      pout.innerHTML = ''; try { const d = await api('/api/labs/attack/probe', { url: url.value, param: param.value, authorized: auth.checked }); CP.evidence.add(d.evidence); usage.bump('probe');
        pout.appendChild(el('div', 'summary ' + (d.findings.length ? 'bad' : 'ok'), `${d.findings.length} issue(s) observed · ${d.requests.length} harmless GET request(s) to ${d.target}`));
        d.findings.forEach(f => { const c = el('div', 'hit'); c.append(badge(f.severity, sevCls(f.severity)), badge('verified', 'good'), el('b', '', ' ' + f.title), note(f.detail), note('Remediation: ' + f.remediation, 'good')); f.evidence.forEach(x => c.appendChild(el('div', 'tinfo', `${x.ref} → ${x.text}`))); pout.appendChild(c); });
        const rl = el('div', 'kv'); d.requests.forEach(r => { const row = el('div'); row.append(el('b', '', r.label), el('span', '', `${r.path} → ${r.status} (${r.ms} ms)`)); rl.appendChild(row); }); pout.appendChild(sec('Requests sent', rl)); pout.appendChild(note(d.note)); } catch (e) { fail(pout, e); } }));
    const demoNames = el('div', 'pact'); [['sqli', 'SQL injection'], ['xss', 'XSS'], ['cmdi', 'Command injection'], ['path', 'Path traversal'], ['deser', 'Deserialization'], ['hash', 'Weak hash'], ['random', 'Predictable tokens'], ['eval', 'eval()']].forEach(([k, l]) => demoNames.appendChild(btn(l, 'ghostbtn', async bt => busy(bt, 'Running…', async () => runDemo(k)))));
    b.append(sec('1 · Static scan — find weaknesses in code', note('Rule-based scanning with the exact line, why it matters and how to fix it.'), (() => { const w = el('div', 'pact'); w.append(where, run); return w; })(), out),
      sec('2 · Harmless demonstrations', note('Generic, safe demonstrations on built-in sample data run in the sandbox — they never touch your code or any other system.'), demoNames, demo),
      sec('3 · Local probe — test your own app on localhost', el('div', 'lerr', 'Only localhost / 127.0.0.1 targets are allowed, only harmless GET requests are sent, and only applications you own may be tested. Never test systems you do not own or have written permission to test. This tool is disabled on production servers.'), url, param, al, probe, pout));
  }

  [['proof', 'Proof Mode: show me that it works'], ['tribunal', 'Review Tribunal'], ['spec', 'Spec → Test generator'], ['merge', 'Merge Conflict Resolver'], ['attack', 'Security Attack Simulator']]
    .forEach(([k, label]) => CP.extraCommands.push({ label: 'Labs → ' + label, run: () => { CP.showTab('labs'); openSub(k); } }));
  CP.openLabSub = openSub;
})();

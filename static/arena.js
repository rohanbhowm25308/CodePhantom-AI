/* CODEPHANTOM AI — core workspace (v4): streaming chat, 17 modes, Monaco editor, sandboxed runner, tests,
   voice, history, autosave. Extra features (practice, trace, palette, snippets…) live in features.js via window.CP. */
(() => {
  const $ = s => document.querySelector(s);
  const LANGS = ['Python','JavaScript','TypeScript','Java','C','C++','C#','Go','Rust','PHP','Ruby','Swift','Kotlin','SQL','R','Bash'];
  const EXT = { python:'py', py:'py', javascript:'js', js:'js', typescript:'ts', ts:'ts', java:'java', c:'c', 'c++':'cpp', cpp:'cpp', 'c#':'cs', csharp:'cs',
    go:'go', rust:'rs', php:'php', ruby:'rb', swift:'swift', kotlin:'kt', sql:'sql', r:'r', bash:'sh', sh:'sh', html:'html', css:'css', json:'json' };
  const FENCE = { py:'Python', python:'Python', js:'JavaScript', javascript:'JavaScript', ts:'TypeScript', typescript:'TypeScript', java:'Java', c:'C',
    'c++':'C++', cpp:'C++', 'c#':'C#', csharp:'C#', go:'Go', golang:'Go', rust:'Rust', rs:'Rust', php:'PHP', ruby:'Ruby', rb:'Ruby', swift:'Swift',
    kotlin:'Kotlin', sql:'SQL', r:'R', bash:'Bash', sh:'Bash', shell:'Bash' };
  const MONACO = { Python:'python', JavaScript:'javascript', TypeScript:'typescript', Java:'java', C:'c', 'C++':'cpp', 'C#':'csharp', Go:'go', Rust:'rust',
    PHP:'php', Ruby:'ruby', Swift:'swift', Kotlin:'kotlin', SQL:'sql', R:'r', Bash:'shell' };
  const STARTER = {
    Python: 'import sys\n\nname = sys.stdin.readline().strip() or "world"\nprint(f"Hello, {name}!")\n',
    JavaScript: 'const lines = require("fs").readFileSync(0, "utf8").split("\\n");\nconsole.log(`Hello, ${lines[0].trim() || "world"}!`);\n',
    Java: 'import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n        Scanner sc = new Scanner(System.in);\n        String name = sc.hasNextLine() ? sc.nextLine().trim() : "";\n        System.out.println("Hello, " + (name.isEmpty() ? "world" : name) + "!");\n    }\n}\n',
    C: '#include <stdio.h>\n\nint main(void) {\n    char name[100] = "world";\n    if (scanf("%99s", name) != 1) { }\n    printf("Hello, %s!\\n", name);\n    return 0;\n}\n',
    'C++': '#include <iostream>\n#include <string>\nusing namespace std;\n\nint main() {\n    string name;\n    if (!(cin >> name)) name = "world";\n    cout << "Hello, " << name << "!" << endl;\n    return 0;\n}\n',
    Go: 'package main\n\nimport "fmt"\n\nfunc main() {\n\tname := "world"\n\tfmt.Scan(&name)\n\tfmt.Printf("Hello, %s!\\n", name)\n}\n',
    Rust: 'use std::io;\n\nfn main() {\n    let mut name = String::new();\n    io::stdin().read_line(&mut name).unwrap();\n    let name = name.trim();\n    println!("Hello, {}!", if name.is_empty() { "world" } else { name });\n}\n',
    PHP: '<?php\n$name = trim(fgets(STDIN)) ?: "world";\necho "Hello, $name!\\n";\n',
    Ruby: 'name = (gets || "").strip\nname = "world" if name.empty?\nputs "Hello, #{name}!"\n',
    Bash: 'read -r name\necho "Hello, ${name:-world}!"\n',
  };

  const MODES = {
    solve:      { ic:'✦', name:'Solve a problem',  hint:'Describe the problem in plain English, e.g. "find two numbers in an array that add up to a target". You can include sample inputs and outputs.' },
    debug:      { ic:'✖', name:'Fix my code',      code:true, hint:'Optional: paste the error message or describe what goes wrong.' },
    explain:    { ic:'?', name:'Explain code',     code:true, level:true, hint:'Optional: ask about a specific part.' },
    dryrun:     { ic:'▤', name:'Dry run',          code:true, level:true, hint:'Optional: an example input to walk through, e.g. "n = 5".' },
    complexity: { ic:'O', name:'Complexity',       code:true, hint:'Optional: ask about a specific function.' },
    optimize:   { ic:'⚡', name:'Optimize code',    code:true, hint:'Optional: what matters most — speed, memory or readability?' },
    refactor:   { ic:'♻', name:'Refactor',         code:true, hint:'Optional: style rules or what to clean up.' },
    convert:    { ic:'⇄', name:'Convert code',     code:true, convert:true, hint:'Optional: notes about libraries or style.' },
    security:   { ic:'◈', name:'Security scan',    code:true, hint:'Optional: where does this code run (web server, CLI…)?' },
    tests:      { ic:'✓', name:'Generate tests',   code:true, hint:'Optional: preferred test framework.' },
    docs:       { ic:'✎', name:'Comments & docs',  code:true, hint:'Optional: docstring style (Google, NumPy, JSDoc…).' },
    review:     { ic:'⌥', name:'Review & commit',  code:true, hint:'Paste a diff or code. Optional: what changed.' },
    starter:    { ic:'▣', name:'Project starter',  hint:'Describe the project, e.g. "a Flask REST API for a todo list with SQLite".' },
    repo:       { ic:'❏', name:'Project review',   files:true, hint:'Attach several files above. Optional: what to focus on (architecture, bugs, README…).' },
    learn:      { ic:'✧', name:'Learn a concept',  level:true, hint:'What do you want to learn? e.g. "recursion" or "pointers in C".' },
    ask:        { ic:'…', name:'Ask anything',     hint:'Ask any coding question or continue the conversation.' },
    interview:  { ic:'☎', name:'Mock interview',   hidden:true, hint:'Answer the interviewer here.' },
  };
  const VERB = { debug:'Find and fix the bugs in', explain:'Explain', dryrun:'Dry-run step by step', complexity:'Analyze the time and space complexity of',
    optimize:'Optimize', refactor:'Refactor for readability and maintainability', security:'Scan for security problems in', tests:'Write unit tests for',
    docs:'Add helpful comments and write documentation for', review:'Review as a pull request and write a commit message for' };
  const SEND_LABEL = { solve:'Generate solution', debug:'Run Phantom Debug', ask:'Send', interview:'Send answer' };

  const KEY = 'codephantom.history.v1', DRAFT = 'codephantom.draft.v1', USAGE = 'codephantom.usage.v1', MEM = 'codephantom.memory.v1';
  let mode = 'solve', turns = [], sessionId = newId(), busy = false, running = false, current = 'assist', attached = [];
  let run = { enabled:false, langs:[], timeout:5, backend:null }, lastRun = null, prevLang = 'Python';
  const tests = [{ input:'', expected:'' }];
  const hooks = {};            // tab-show callbacks, filled by features.js

  const IDS = ['modes','lang','targetLang','targetWrap','level','levelWrap','prompt','log','send','newChat','historyList','clearHistory','histSearch','exportBtn','status','statusTxt',
    'modeName','mic','voiceBar','codeInfo','attachRow','runBtn','testBtn','runInfo','editor','editorWrap','codeFallback','stdin','runOut','errChips','addTest','testList','testResults',
    'main','viewAssist','viewCode','viewPractice','viewProjects','viewLabs','viewDash'];
  const els = Object.fromEntries(IDS.map(id => [id, document.getElementById(id)]));
  if (!els.log) return;

  function newId() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[c]));
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const jget = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch { return d; } };
  const jset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} };
  const evAdd = list => { try { window.CP && window.CP.evidence && window.CP.evidence.add(list); } catch (_) {} };
  const usage = { all: () => jget(USAGE, {}), bump(k, n = 1) { const u = jget(USAGE, {}); u[k] = (u[k] || 0) + n; jset(USAGE, u); } };
  const memory = () => { const m = jget(MEM, {}); return m.on && m.text ? m.text : ''; };
  const isLight = () => document.documentElement.dataset.theme === 'light';

  /* ─────────── editor (Monaco, with textarea fallback) ─────────── */
  let buf = '';
  let impl = { get: () => buf, set: v => { buf = v; }, lang() {}, goto() {}, mark() {}, clear() {}, theme() {} };
  const ed = { get: () => impl.get(), set: v => impl.set(v), lang: l => impl.lang(l), goto: (n, m) => impl.goto(n, m), mark: (a, m) => impl.mark(a, m), clear: () => impl.clear(), theme: l => impl.theme(l) };

  function useFallback() {
    if (impl.fallback) return;
    els.editor.hidden = true; els.codeFallback.hidden = false; els.codeFallback.value = impl.get();
    els.codeFallback.addEventListener('keydown', e => {
      if (e.key === 'Tab') { e.preventDefault(); els.codeFallback.setRangeText('    ', els.codeFallback.selectionStart, els.codeFallback.selectionEnd, 'end'); }
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runCode(); }
    });
    const ta = els.codeFallback;
    impl = { fallback: true, get: () => ta.value, set: v => { ta.value = v; }, lang() {}, clear() {}, mark() {}, theme() {},
      goto(n) { const ls = ta.value.split('\n'); let s = 0; for (let i = 0; i < n - 1 && i < ls.length; i++) s += ls[i].length + 1; ta.focus(); ta.setSelectionRange(s, s + (ls[n - 1] || '').length); } };
  }

  function initEditor() {
    const BASE = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min';
    let settled = false;
    const bail = () => { if (!settled) { settled = true; useFallback(); } };
    const timer = setTimeout(bail, 9000);
    const s = document.createElement('script');
    s.src = BASE + '/vs/loader.js'; s.onerror = bail;
    s.onload = () => {
      window.MonacoEnvironment = { getWorkerUrl: () => URL.createObjectURL(new Blob(
        [`self.MonacoEnvironment={baseUrl:'${BASE}/'};importScripts('${BASE}/vs/base/worker/workerMain.js');`], { type:'text/javascript' })) };
      window.require.config({ paths: { vs: BASE + '/vs' } });
      window.require(['vs/editor/editor.main'], () => {
        if (settled) return; settled = true; clearTimeout(timer);
        monaco.editor.defineTheme('phantom', { base:'vs-dark', inherit:true, rules:[], colors:{
          'editor.background':'#050506', 'editor.lineHighlightBackground':'#14090b', 'editorLineNumber.foreground':'#5a5550',
          'editorLineNumber.activeForeground':'#ff2b2b', 'editorCursor.foreground':'#ff2b2b', 'editor.selectionBackground':'#c0121f66' } });
        const e = monaco.editor.create(els.editor, { value: buf, language: MONACO[els.lang.value] || 'plaintext', theme: isLight() ? 'vs' : 'phantom', automaticLayout:true,
          minimap:{ enabled:false }, fontSize:13, fontFamily:"'JetBrains Mono', Consolas, monospace", tabSize:4, scrollBeyondLastLine:false,
          padding:{ top:10 }, renderLineHighlight:'line', smoothScrolling:true });
        e.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, runCode);
        impl = { get: () => e.getValue(), set: v => e.setValue(v), lang: l => monaco.editor.setModelLanguage(e.getModel(), MONACO[l] || 'plaintext'),
          goto: n => { try { e.revealLineInCenter(n); e.setPosition({ lineNumber:n, column:1 }); e.focus(); } catch (_) {} },
          mark: (lines, msg) => { try { monaco.editor.setModelMarkers(e.getModel(), 'cp', lines.map(n => ({ startLineNumber:n, endLineNumber:n, startColumn:1, endColumn:400, message: msg || 'Error', severity: monaco.MarkerSeverity.Error }))); } catch (_) {} },
          clear: () => { try { monaco.editor.setModelMarkers(e.getModel(), 'cp', []); } catch (_) {} },
          theme: light => { try { monaco.editor.setTheme(light ? 'vs' : 'phantom'); } catch (_) {} } };
      }, bail);
    };
    document.head.appendChild(s);
  }

  /* ─────────── setup ─────────── */
  LANGS.forEach(l => { els.lang.add(new Option(l, l)); els.targetLang.add(new Option(l, l)); });
  els.targetLang.value = 'Java';
  Object.entries(MODES).forEach(([k, m]) => {
    if (m.hidden) return;
    const b = el('button'); b.type = 'button'; b.dataset.mode = k; b.innerHTML = `<i>${esc(m.ic)}</i><span>${esc(m.name)}</span>`;
    b.addEventListener('click', () => setMode(k)); els.modes.appendChild(b);
  });

  function setMode(k) {
    mode = k; const m = MODES[k];
    [...els.modes.children].forEach(b => b.classList.toggle('on', b.dataset.mode === k));
    els.targetWrap.hidden = !m.convert; els.levelWrap.hidden = !m.level; els.attachRow.hidden = !m.files;
    els.prompt.placeholder = m.hint; els.modeName.textContent = m.name;
    els.send.textContent = SEND_LABEL[k] || m.name;
    updateCodeInfo(); showTab('assist');
  }
  function showTab(name) {
    current = name;
    const split = els.main.classList.contains('split') && ['assist', 'code'].includes(name);
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t.dataset.tab === name));
    [['assist', 'viewAssist'], ['code', 'viewCode'], ['practice', 'viewPractice'], ['projects', 'viewProjects'], ['labs', 'viewLabs'], ['dash', 'viewDash']].forEach(([t, v]) => {
      els[v].hidden = !(t === name || (split && (t === 'assist' || t === 'code')));
    });
    if (name === 'assist') updateCodeInfo();
    if (hooks[name]) hooks[name]();
  }
  document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => showTab(t.dataset.tab)));
  function updateCodeInfo() {
    const m = MODES[mode], code = ed.get().trim();
    els.codeInfo.hidden = !(m.code || m.files);
    if (m.files) els.codeInfo.innerHTML = attached.length ? `<b>${attached.length}</b> file${attached.length > 1 ? 's' : ''} attached` : 'Attach the project files you want reviewed.';
    else els.codeInfo.innerHTML = code ? `Using the code in <b>Code &amp; Run</b> · ${esc(els.lang.value)} · ${code.split('\n').length} lines` : 'This mode needs code. Open <b>Code &amp; Run</b> and paste it there first.';
  }

  els.lang.addEventListener('change', () => {
    const l = els.lang.value, cur = ed.get();
    if (!cur.trim() || cur === STARTER[prevLang]) ed.set(STARTER[l] || '');
    prevLang = l; ed.lang(l); updateRunAvailability(); updateCodeInfo();
  });
  function setLanguage(l) { if (LANGS.includes(l) && els.lang.value !== l) { els.lang.value = l; prevLang = l; ed.lang(l); updateRunAvailability(); } }
  const canRun = l => run.enabled && run.langs.includes(String(l).toLowerCase());

  /* ─────────── sandboxed runner ─────────── */
  async function post(url, body) {
    const res = await fetch(url, { method:'POST', headers:{ 'Content-Type':'application/json', 'X-Requested-With':'codephantom' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
    return data;
  }
  function updateRunAvailability() {
    const l = els.lang.value, can = canRun(l);
    els.runBtn.disabled = els.testBtn.disabled = !can || running;
    els.runInfo.textContent = !run.enabled ? 'Running code is off on this server — see README (Docker or Judge0).'
      : !can ? `${l} cannot be run on this server. AI features still work.`
      : run.backend === 'local' ? `Local runner · development only, NOT isolated · ${run.timeout} s limit · don't run code you don't trust`
      : `Sandbox: ${run.backend} · ${run.timeout} s limit · no network access`;
    hooks.runState && hooks.runState();
  }
  async function loadRunStatus() {
    try { const s = await (await fetch('/api/run/status')).json(); run = { enabled: s.enabled, langs: s.languages || [], timeout: s.timeout, backend: s.backend }; } catch (_) {}
    updateRunAvailability();
  }
  function busyRun(on) { running = on; els.runBtn.textContent = on ? 'Running…' : 'Run'; updateRunAvailability(); }

  const STATUS = { ok:['Finished', 'ok'], compile_error:['Compile error', 'bad'], runtime_error:['Runtime error', 'bad'], timeout:['Time limit exceeded', 'warn'], killed:['Killed (memory)', 'warn'] };
  function errLines(text) {
    const seen = new Set(), max = ed.get().split('\n').length;
    for (const rx of [/File "[^"]*", line (\d+)/g, /(?:main\.(?:c|cpp|go|rs|js|php|rb|sh|py)|Main\.java):(\d+)/g, /\bline (\d+)\b/gi])
      for (const m of text.matchAll(rx)) { const n = +m[1]; if (n > 0 && n <= max) seen.add(n); }
    return [...seen].slice(0, 6);
  }
  function renderRun(r) {
    const o = els.runOut; o.className = 'runout'; o.innerHTML = ''; els.errChips.innerHTML = ''; ed.clear();
    const [label, cls] = STATUS[r.status] || [r.status, 'warn'];
    o.appendChild(el('span', 'badge ' + cls, `${label}${r.exit_code != null ? ' · exit ' + r.exit_code : ''}${r.time_ms != null ? ' · ' + r.time_ms + ' ms' : ''}${r.memory_kb ? ' · ' + Math.round(r.memory_kb / 1024) + ' MB' : ''}`));
    if (r.stdout) { o.appendChild(el('span', 'lbl', r.status === 'compile_error' ? '' : 'STDOUT')); o.appendChild(el('div', 'stdout', r.stdout)); }
    if (r.stderr) { o.appendChild(el('span', 'lbl', r.status === 'compile_error' ? 'COMPILER OUTPUT' : 'STDERR')); o.appendChild(el('div', 'stderr', r.stderr)); }
    if (!r.stdout && !r.stderr) o.appendChild(el('div', 'muted', '(the program printed nothing)'));
    if (r.status !== 'ok' && r.stderr) {
      const lines = errLines(r.stderr), msg = r.stderr.trim().split('\n').pop().slice(0, 200);
      if (lines.length) { ed.mark(lines, msg); lines.forEach(n => { const b = el('button', '', 'Line ' + n); b.type = 'button'; b.addEventListener('click', () => ed.goto(n, msg)); els.errChips.appendChild(b); }); }
    }
  }
  function showOutMsg(msg) { els.runOut.className = 'runout'; els.runOut.innerHTML = ''; els.runOut.appendChild(el('span', 'badge warn', msg)); }

  async function runCode() {
    if (running || els.runBtn.disabled) return;
    const code = ed.get();
    if (!code.trim()) return showOutMsg('Write some code first.');
    busyRun(true); els.runOut.className = 'runout muted'; els.runOut.textContent = 'Running in sandbox…';
    try { lastRun = await post('/api/run', { language: els.lang.value, code, stdin: els.stdin.value }); renderRun(lastRun); usage.bump('runs');
      evAdd([{ claim: `${els.lang.value} program: ${(STATUS[lastRun.status] || [lastRun.status])[0]}`, status: 'verified', source: 'Code & Run', items: [{ type: 'execution', ref: `exit ${lastRun.exit_code ?? '—'}, ${lastRun.time_ms ?? '?'} ms`, text: (lastRun.stdout || lastRun.stderr || '(no output)').slice(0, 160) }] }]); }
    catch (e) { lastRun = null; showOutMsg(e.message); }
    finally { busyRun(false); }
  }
  els.runBtn.addEventListener('click', runCode);

  function renderTestRows() {
    els.testList.innerHTML = '';
    tests.forEach((t, i) => {
      const row = el('div', 'trow'); row.appendChild(el('span', '', '#' + (i + 1)));
      const a = el('textarea'); a.placeholder = 'Input'; a.value = t.input; a.spellcheck = false; a.addEventListener('input', () => { t.input = a.value; });
      const b = el('textarea'); b.placeholder = t.ai ? 'Expected output (AI-predicted — verify)' : 'Expected output'; b.value = t.expected; b.spellcheck = false;
      b.addEventListener('input', () => { t.expected = b.value; t.ai = false; });
      if (t.note) b.title = t.note;
      const x = el('button', '', '×'); x.type = 'button'; x.title = 'Remove test'; x.addEventListener('click', () => { if (tests.length > 1) { tests.splice(i, 1); renderTestRows(); } });
      row.append(a, b, x); els.testList.appendChild(row);
    });
  }
  els.addTest.addEventListener('click', () => { if (tests.length >= 8) return; tests.push({ input:'', expected:'' }); renderTestRows(); });
  const usableTests = () => tests.filter(t => t.input.trim() || t.expected.trim()).map(t => ({ input: t.input, expected: t.expected }));

  function renderTestResults(d, box, showExpected = () => true) {
    box.innerHTML = '';
    if (d.compile_error) { renderRun(d.compile_error); box.appendChild(el('div', 'summary bad', 'Compile error — no tests were run.')); return; }
    box.appendChild(el('div', 'summary ' + (d.passed === d.total ? 'ok' : 'bad'), `${d.passed} / ${d.total} tests passed`));
    d.results.forEach(r => {
      const row = el('div', 'rrow'), h = el('header');
      h.append(el('b', r.passed ? 'ok' : 'bad', r.passed ? 'PASS' : 'FAIL'), el('span', '', 'Test #' + r.index), el('span', '', (STATUS[r.status] || [r.status])[0] + (r.time_ms != null ? ' · ' + r.time_ms + ' ms' : '')));
      row.appendChild(h);
      if (!r.passed && showExpected(r.index)) {
        const p = el('pre'); p.append(el('i', '', 'EXPECTED'), document.createTextNode(r.expected || '(empty)'), el('i', '', 'ACTUAL OUTPUT'), document.createTextNode(r.stdout || '(empty)'));
        if (r.stderr) p.append(el('i', '', 'STDERR'), document.createTextNode(r.stderr));
        row.appendChild(p);
      }
      box.appendChild(row);
    });
  }
  els.testBtn.addEventListener('click', async () => {
    if (running || els.testBtn.disabled) return;
    const code = ed.get(); els.testResults.innerHTML = '';
    if (!code.trim()) return els.testResults.appendChild(el('div', 'summary bad', 'Write some code first.'));
    const usable = usableTests();
    if (!usable.length) return els.testResults.appendChild(el('div', 'summary bad', 'Fill in at least one test case (input and expected output).'));
    busyRun(true); els.testBtn.textContent = 'Testing…';
    try {
      const d = await post('/api/test', { language: els.lang.value, code, tests: usable });
      renderTestResults(d, els.testResults); usage.bump('tests');
      if (!d.compile_error) { evAdd([{ claim: `${d.passed}/${d.total} test cases pass`, status: 'verified', source: 'Code & Run', items: d.results.slice(0, 6).map(r => ({ type: 'execution', ref: '#' + r.index, text: `${r.passed ? 'PASS' : 'FAIL'} — got ${JSON.stringify((r.stdout || '').slice(0, 60))}` })) }]);
        if (tests.some(t => t.ai)) evAdd([{ claim: 'Some expected outputs were predicted by AI', status: 'ai', source: 'Code & Run', items: [{ type: 'note', ref: 'expectation', text: 'A FAIL can mean the expectation is wrong.' }] }]); }
      if (tests.some(t => t.ai)) els.testResults.appendChild(el('div', 'tinfo', 'Some expected outputs were predicted by AI. A FAIL can mean the expectation is wrong — check it.'));
    } catch (e) { els.testResults.appendChild(el('div', 'summary bad', e.message)); }
    finally { els.testBtn.textContent = 'Run tests'; busyRun(false); }
  });

  document.querySelectorAll('[data-ask]').forEach(b => b.addEventListener('click', () => {
    if (busy) return;
    setMode(b.dataset.ask);
    els.prompt.value = lastRun && lastRun.status !== 'ok' ? `Real run result — ${lastRun.status}:\n${(lastRun.stderr || lastRun.stdout || '').slice(0, 1500)}` : '';
    send();
  }));

  /* ─────────── composing / rendering ─────────── */
  const fence = (l, c) => '```' + l.toLowerCase() + '\n' + c + '\n```';
  function compose(t) {
    const lvl = t.level && MODES[t.mode] && MODES[t.mode].level ? `\n\nLevel: ${t.level}.` : '';
    switch (t.mode) {
      case 'solve': return `Write a solution in ${t.lang} for this problem:\n${t.prompt}`;
      case 'starter': return `Create a starter project in ${t.lang} for: ${t.prompt}`;
      case 'ask': return t.prompt;
      case 'learn': return `Teach me: ${t.prompt}${lvl}`;
      case 'interview': return t.prompt + (t.code ? `\n\nMy solution (${t.lang}):\n${fence(t.lang, t.code)}` : '');
      case 'repo': return `Review this project (${t.files.length} file${t.files.length > 1 ? 's' : ''}):\n\n` + t.files.map(f => `### ${f.name}\n\`\`\`\n${f.text}\n\`\`\``).join('\n\n') + (t.prompt ? `\n\nFocus: ${t.prompt}` : '');
      default: {
        const head = t.mode === 'convert' ? `Convert this ${t.lang} code to ${t.target}:` : `${VERB[t.mode]} this ${t.lang} code:`;
        return [head, fence(t.lang, t.code), t.prompt && 'Extra context: ' + t.prompt].filter(Boolean).join('\n\n') + lvl;
      }
    }
  }
  const toMessages = () => turns.flatMap(t => [{ role:'user', content: compose(t) }, ...(t.answer ? [{ role:'assistant', content: t.answer }] : [])]);

  function htmlOf(text) { return window.marked && window.DOMPurify ? DOMPurify.sanitize(marked.parse(text, { gfm:true })) : null; }
  function md(text, turn) {
    const box = el('div', 'md'), html = htmlOf(text);
    if (!html) { box.style.whiteSpace = 'pre-wrap'; box.textContent = text; return box; }
    box.innerHTML = html;
    box.querySelectorAll('pre > code').forEach(c => enhanceCode(c, turn));
    box.querySelectorAll('table').forEach(t => { const w = el('div', 'tbl'); t.replaceWith(w); w.appendChild(t); });
    return box;
  }
  function enhanceCode(code, turn) {
    const pre = code.parentElement;
    const lang = ((code.className.match(/language-([\w+#-]+)/) || [])[1] || 'text').toLowerCase();
    if (window.hljs) { try { hljs.highlightElement(code); } catch (_) {} }
    const wrap = el('div', 'cb'), bar = el('div', 'cb__bar'), tools = el('div'); bar.append(el('span', '', lang), tools);
    const raw = code.textContent, uiLang = FENCE[lang], A = () => window.CP && window.CP.actions;
    const btn = (label, fn) => { const b = el('button', '', label); b.type = 'button'; b.addEventListener('click', () => fn(b)); tools.appendChild(b); };
    btn('Copy', async b => { try { await navigator.clipboard.writeText(raw); b.textContent = 'Copied'; } catch { b.textContent = 'Failed'; } setTimeout(() => { b.textContent = 'Copy'; }, 1400); });
    btn('Download', () => { const a = el('a'); a.href = URL.createObjectURL(new Blob([raw], { type:'text/plain' })); a.download = `codephantom.${EXT[lang] || 'txt'}`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 500); });
    if (uiLang) {
      btn('Open in editor', () => { setLanguage(uiLang); ed.set(raw); showTab('code'); });
      btn('Run in sandbox', () => { setLanguage(uiLang); ed.set(raw); showTab('code'); runCode(); });
      if (turn && turn.code && ['debug', 'optimize', 'refactor', 'docs'].includes(turn.mode)) {
        btn(turn.mode === 'debug' ? 'Apply fix' : 'Apply', () => { setLanguage(uiLang); ed.set(raw); showTab('code'); flash('Applied to the editor'); });
        btn('Apply + re-run tests', () => { setLanguage(uiLang); ed.set(raw); showTab('code'); if (usableTests().length) els.testBtn.click(); else flash('Add test cases first (Code & Run)'); });
      }
    }
    if (turn && turn.code && turn.mode !== 'convert') btn('Diff', () => A() && A().diff(turn.code, raw, wrap));
    if (turn && turn.code && ['optimize', 'refactor'].includes(turn.mode) && uiLang === turn.lang && canRun(uiLang)) btn('Benchmark', () => A() && A().benchmark(turn.code, raw, uiLang, wrap));
    if (turn && turn.mode === 'convert' && uiLang === turn.target && canRun(turn.lang) && canRun(uiLang)) btn('Validate conversion', () => A() && A().validate(turn, raw, wrap));
    pre.replaceWith(wrap); wrap.append(bar, pre);
  }

  function addUser(t) {
    const d = el('div', 'msg msg--user');
    d.appendChild(el('div', 'msg__tag', MODES[t.mode].name + (['ask', 'repo', 'learn', 'interview'].includes(t.mode) ? '' : ' · ' + t.lang) + (t.mode === 'convert' ? ' → ' + t.target : '')));
    if (t.prompt) d.appendChild(el('p', '', t.prompt));
    if (t.files && t.files.length) d.appendChild(el('p', '', 'Files: ' + t.files.map(f => f.name).join(', ')));
    if (t.code) d.appendChild(el('pre', 'msg__code', t.code));
    els.log.appendChild(d); return d;
  }
  function botShell() { const d = el('div', 'msg msg--bot'); d.appendChild(el('div', 'msg__tag', 'CODEPHANTOM')); els.log.appendChild(d); return d; }
  function addBot(text, isError, turn) {
    const d = botShell();
    if (isError) { d.classList.add('msg--err'); d.appendChild(el('p', '', text)); } else d.appendChild(md(text, turn));
    return d;
  }
  const nearBottom = () => els.log.scrollHeight - els.log.scrollTop - els.log.clientHeight < 90;
  const scrollLog = () => { els.log.scrollTop = els.log.scrollHeight; };
  function welcome() {
    const d = el('div', 'welcome');
    d.innerHTML = '<h3>Welcome to CODEPHANTOM AI</h3><p>Pick a mode, describe a problem or paste code in <b>Code &amp; Run</b>, and the phantom answers live. Run it in a real sandbox, trace it step by step, practise DSA, and press <b>Ctrl + K</b> for every command.</p>';
    return d;
  }
  function renderAll() {
    els.log.innerHTML = '';
    if (!turns.length) return els.log.appendChild(welcome());
    turns.forEach(t => { addUser(t); if (t.answer) addBot(t.answer, false, t); }); scrollLog();
  }

  /* ─────────── sending (streamed) ─────────── */
  let flashUntil = 0;
  function flash(msg) { flashUntil = Date.now() + 2600; els.statusTxt.textContent = msg; els.status.className = 'status warn'; setTimeout(checkHealth, 2650); }

  function buildTurn() {
    const m = MODES[mode];
    const t = { mode, lang: els.lang.value, target: els.targetLang.value, level: els.level.value, prompt: els.prompt.value.trim(), code: m.code ? ed.get().replace(/\s+$/, '') : '' };
    if (m.files) {
      t.files = attached.map(f => ({ ...f }));
      if (!t.files.length && ed.get().trim()) t.files = [{ name: 'editor.' + (EXT[t.lang.toLowerCase()] || 'txt'), text: ed.get() }];
      if (!t.files.length) { flash('Attach at least one file first.'); return null; }
    }
    if (m.code && !t.code) {
      if (!turns.length) { showTab('code'); flash('Paste some code in Code & Run first.'); return null; }
      t.mode = 'ask'; if (!t.prompt) { flash('Type a follow-up question.'); return null; }
    }
    if (['solve', 'ask', 'starter', 'learn', 'interview'].includes(mode) && !t.prompt) { flash('Type your request first.'); return null; }
    if (t.mode === 'convert' && t.lang === t.target) { flash('Pick a different target language.'); return null; }
    return t;
  }
  async function send() { if (busy) return; const t = buildTurn(); if (t) runTurn(t); }

  async function runTurn(t) {
    if (busy) return;
    if (recording && recorder && recorder.finish) recorder.finish();
    busy = true; els.send.disabled = true;
    if (!turns.length) els.log.innerHTML = '';
    turns.push(t); addUser(t); showTab('assist');
    const shell = botShell(), body = el('div', 'md thinking'); body.innerHTML = '<p>Reading the code<span>.</span><span>.</span><span>.</span></p>'; shell.appendChild(body);
    scrollLog(); els.prompt.value = '';

    const ctl = new AbortController(); let idle = setTimeout(() => ctl.abort(), 30000);
    let text = '', raf = 0;
    const paint = () => { raf = 0; const h = htmlOf(text), stick = nearBottom(); body.className = 'md'; if (h) body.innerHTML = h; else { body.style.whiteSpace = 'pre-wrap'; body.textContent = text; } if (stick) scrollLog(); };
    try {
      const res = await fetch('/api/chat', { method:'POST', headers:{ 'Content-Type':'application/json', 'X-Requested-With':'codephantom' }, signal: ctl.signal,
        body: JSON.stringify({ mode: t.mode, language: t.lang, target: t.target, memory: memory(), messages: toMessages() }) });
      if (!res.ok || !res.body) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `Request failed (${res.status}).`); }
      const reader = res.body.getReader(), dec = new TextDecoder(); let buffer = '';
      for (;;) {
        const { done, value } = await reader.read(); if (done) break;
        clearTimeout(idle); idle = setTimeout(() => ctl.abort(), 30000);
        buffer += dec.decode(value, { stream:true });
        let i; while ((i = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, i).trim(); buffer = buffer.slice(i + 1); if (!line) continue;
          let j; try { j = JSON.parse(line); } catch { continue; }
          if (j.e) throw new Error(j.e); text += j.d || '';
        }
        if (!raf) raf = requestAnimationFrame(paint);
      }
      if (!text.trim()) throw new Error('The AI returned an empty answer. Try again.');
      if (raf) cancelAnimationFrame(raf);
      shell.remove(); t.answer = text; addBot(text, false, t); saveSession(); usage.bump('ai');
      evAdd([{ claim: `${MODES[t.mode].name}: AI answer (not executed)`, status: 'ai', source: 'Assistant', items: [{ type: 'note', ref: 'verification', text: 'Run it in Code & Run, or use Proof Mode, to turn this into verified evidence.' }] }]);
    } catch (err) {
      if (raf) cancelAnimationFrame(raf);
      shell.remove(); turns.pop(); els.log.lastElementChild && els.log.lastElementChild.remove();
      addBot(err.name === 'AbortError' ? 'The AI stopped responding. Try again.' : err.message, true);
      els.prompt.value = t.prompt;
    } finally { clearTimeout(idle); busy = false; els.send.disabled = false; scrollLog(); }
  }
  els.send.addEventListener('click', send);
  /* Enter sends, Shift+Enter makes a new line */
  els.prompt.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });

  /* ─────────── mission history: favourites, search, export ─────────── */
  const load = () => jget(KEY, []);
  const store = list => jset(KEY, list.slice(0, 40));
  function saveSession() {
    const first = turns[0], prev = load().find(s => s.id === sessionId);
    const list = load().filter(s => s.id !== sessionId);
    list.unshift({ id: sessionId, ts: Date.now(), fav: !!(prev && prev.fav), title: (first.prompt || first.code || 'Untitled').split('\n')[0].slice(0, 46), mode: first.mode, turns: turns.filter(t => t.answer) });
    store(list); renderHistory();
  }
  const haystack = s => (s.title + ' ' + ((MODES[s.mode] || {}).name || '') + ' ' + s.turns.map(t => [t.prompt, t.code, t.answer].join(' ')).join(' ')).toLowerCase();
  function renderHistory() {
    const q = els.histSearch.value.trim().toLowerCase();
    const list = load().filter(s => !q || haystack(s).includes(q)).sort((a, b) => (b.fav ? 1 : 0) - (a.fav ? 1 : 0) || b.ts - a.ts);
    els.historyList.innerHTML = '';
    if (!list.length) { els.historyList.appendChild(el('li', 'empty', q ? 'No missions match your search.' : 'Nothing yet. Finished missions appear here.')); return; }
    list.forEach(s => {
      const li = el('li'), star = el('button', 'star' + (s.fav ? ' on' : ''), s.fav ? '★' : '☆'); star.type = 'button'; star.title = 'Favourite';
      star.addEventListener('click', () => { const all = load(), x = all.find(z => z.id === s.id); if (x) { x.fav = !x.fav; store(all); renderHistory(); } });
      const b = el('button', 'row' + (s.id === sessionId ? ' on' : '')); b.type = 'button';
      b.append(el('span', '', s.title), el('small', '', `${(MODES[s.mode] || {}).name || s.mode} · ${new Date(s.ts).toLocaleDateString()}`));
      b.addEventListener('click', () => { if (busy) return; sessionId = s.id; turns = s.turns; renderAll(); renderHistory(); showTab('assist'); });
      li.append(star, b); els.historyList.appendChild(li);
    });
  }
  els.histSearch.addEventListener('input', renderHistory);
  els.clearHistory.addEventListener('click', () => { if (confirm('Delete all saved missions?')) { store([]); renderHistory(); } });
  els.newChat.addEventListener('click', () => { if (busy) return; turns = []; sessionId = newId(); els.prompt.value = ''; renderAll(); renderHistory(); });
  function exportReport() {
    const src = turns.length ? [{ title: (turns[0].prompt || 'Mission'), ts: Date.now(), turns: turns.filter(t => t.answer) }] : load().map(s => ({ title: s.title, ts: s.ts, turns: s.turns }));
    if (!src.length || !src.some(s => s.turns.length)) return flash('Nothing to export yet.');
    let md = '# CODEPHANTOM AI report\n\n_Generated ' + new Date().toLocaleString() + '_\n';
    src.forEach(s => {
      md += `\n---\n\n# ${s.title}\n`;
      s.turns.forEach((t, i) => {
        md += `\n## ${i + 1}. ${MODES[t.mode].name}${t.lang ? ' (' + t.lang + ')' : ''}\n\n`;
        if (t.prompt) md += t.prompt + '\n\n';
        if (t.code) md += fence(t.lang, t.code) + '\n\n';
        md += '**Answer (AI — not executed):**\n\n' + t.answer + '\n';
      });
    });
    const a = el('a'); a.href = URL.createObjectURL(new Blob([md], { type:'text/markdown' })); a.download = 'codephantom-report.md'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 800);
    usage.bump('exports');
  }
  els.exportBtn.addEventListener('click', exportReport);

  async function checkHealth() {
    try {
      const h = await (await fetch('/api/health')).json();
      if (Date.now() < flashUntil) return;          // don't wipe a message the user is reading
      els.status.className = 'status ' + (h.key ? 'ok' : 'bad');
      els.statusTxt.textContent = h.key ? `Online · ${h.model}` : 'No GROQ_API_KEY in .env';
    } catch { els.status.className = 'status bad'; els.statusTxt.textContent = 'Backend offline — run python app.py'; }
  }

  /* ─────────── voice input: record → Groq Whisper → fills the prompt and sends ─────────── */
  let recorder = null, recording = false, transcribing = false, voiceTimer = 0;
  function voiceMsg(text, kind, ms) {
    clearTimeout(voiceTimer);
    els.voiceBar.hidden = !text; els.voiceBar.textContent = text || ''; els.voiceBar.className = 'voicebar ' + (kind || '');
    if (text && ms) voiceTimer = setTimeout(() => { els.voiceBar.hidden = true; }, ms);
  }
  function setMic(state) {
    els.mic.classList.toggle('on', state === 'rec'); els.mic.classList.toggle('wait', state === 'busy');
    els.mic.disabled = state === 'busy'; els.mic.title = state === 'rec' ? 'Click to stop' : 'Speak your request';
  }
  const MIC_FAIL = { NotAllowedError:'Microphone is blocked. Click the lock icon in the address bar, allow the microphone, then try again.',
    SecurityError:'Microphone is blocked for this page.', NotFoundError:'No microphone was found on this computer.', NotReadableError:'The microphone is being used by another app.' };
  async function startMic() {
    if (busy || recording || transcribing) return;
    if (!navigator.mediaDevices || !window.MediaRecorder) return voiceMsg('This browser cannot record audio. Use a recent Chrome or Edge.', 'bad', 6000);
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch (e) { return voiceMsg(MIC_FAIL[e.name] || 'Could not open the microphone.', 'bad', 7000); }
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'].find(t => MediaRecorder.isTypeSupported(t)) || '';
    const chunks = []; let spoke = false, stopped = false, timer = 0, ac = null;
    recorder = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 32000 } : undefined);
    recorder.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
    const finish = () => { if (stopped) return; stopped = true; clearInterval(timer); if (ac) ac.close().catch(() => {}); if (recorder.state !== 'inactive') recorder.stop(); };
    recorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop()); recording = false;
      if (!spoke || !chunks.length) { setMic('idle'); return voiceMsg("I didn't hear anything. Click the mic and speak clearly.", 'bad', 5000); }
      await transcribe(new Blob(chunks, { type: (recorder.mimeType || mime || 'audio/webm').split(';')[0] }));
    };
    recorder.start(250); recording = true; setMic('rec');
    voiceMsg('● Listening… speak now. It stops by itself when you pause (or click the mic).', 'live');
    try {
      ac = new (window.AudioContext || window.webkitAudioContext)();
      const an = ac.createAnalyser(); an.fftSize = 1024; ac.createMediaStreamSource(stream).connect(an);
      const b = new Uint8Array(an.fftSize); const t0 = Date.now(); let lastVoice = t0;
      timer = setInterval(() => {
        an.getByteTimeDomainData(b); let sum = 0; for (const v of b) sum += ((v - 128) / 128) ** 2;
        const rms = Math.sqrt(sum / b.length), now = Date.now();
        if (rms > 0.025) { spoke = true; lastVoice = now; }
        if ((spoke && now - lastVoice > 1500) || (!spoke && now - t0 > 8000) || now - t0 > 30000) finish();
      }, 100);
    } catch (_) { spoke = true; setTimeout(finish, 8000); }
    recorder.finish = finish;
  }
  async function transcribe(blob) {
    transcribing = true; setMic('busy'); voiceMsg('Transcribing…', 'live');
    try {
      const lang = (navigator.language || 'en').slice(0, 2) === 'en' ? '?lang=en' : '';
      const res = await fetch('/api/transcribe' + lang, { method:'POST', headers:{ 'Content-Type': blob.type }, body: blob });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d.error || `Speech recognition failed (${res.status}).`);
      if (!d.text) throw new Error("I couldn't understand that. Try again a little slower.");
      els.prompt.value = (els.prompt.value.trim() + ' ' + d.text).trim();
      voiceMsg('Heard: “' + d.text + '”', 'ok', 3500);
      transcribing = false; setMic('idle'); usage.bump('voice'); send();
    } catch (e) { voiceMsg(e.message, 'bad', 7000); }
    finally { transcribing = false; setMic('idle'); }
  }
  if (!navigator.mediaDevices || !window.MediaRecorder) { els.mic.disabled = true; els.mic.title = 'Voice input needs a recent Chrome or Edge'; }
  else els.mic.addEventListener('click', () => (recording ? recorder.finish && recorder.finish() : startMic()));

  /* ─────────── autosave + session recovery ─────────── */
  let lastDraft = '';
  function saveDraft() {
    const d = JSON.stringify({ code: ed.get(), lang: els.lang.value, stdin: els.stdin.value, tests, mode, target: els.targetLang.value });
    if (d !== lastDraft) { lastDraft = d; try { localStorage.setItem(DRAFT, d); } catch (_) {} }
  }
  function restoreDraft() {
    const d = jget(DRAFT, null);
    if (!d || typeof d.code !== 'string') return false;
    buf = d.code;
    if (LANGS.includes(d.lang)) { els.lang.value = d.lang; prevLang = d.lang; }
    if (LANGS.includes(d.target)) els.targetLang.value = d.target;
    els.stdin.value = d.stdin || '';
    if (Array.isArray(d.tests) && d.tests.length) tests.splice(0, tests.length, ...d.tests.slice(0, 8).map(t => ({ input: String(t.input || ''), expected: String(t.expected || ''), ai: !!t.ai })));
    if (MODES[d.mode] && !MODES[d.mode].hidden) mode = d.mode;
    return true;
  }
  setInterval(saveDraft, 2000);
  window.addEventListener('beforeunload', saveDraft);

  /* ─────────── public handle for features.js ─────────── */
  window.CP = { $, el, esc, els, ed, MODES, LANGS, STARTER, EXT, FENCE, hooks, tests, usage, jget, jset, post, md, flash, setMode, showTab, send, runTurn, runCode,
    extraCommands: [], renderTestRows, renderTestResults, usableTests, setLanguage, canRun, updateCodeInfo, exportReport, isLight, STATUS,
    get turns() { return turns; }, get lastRun() { return lastRun; }, get mode() { return mode; }, get current() { return current; }, get busy() { return busy; },
    get attached() { return attached; }, set attached(v) { attached = v; updateCodeInfo(); }, actions: {} };

  /* ─────────── boot ─────────── */
  const restored = restoreDraft();
  if (!restored) buf = STARTER.Python;
  initEditor(); renderTestRows(); setMode(mode); renderAll(); renderHistory(); checkHealth(); loadRunStatus();
  if (restored) flash('Restored your last session');
})();

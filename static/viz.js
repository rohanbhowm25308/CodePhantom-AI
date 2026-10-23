/* CODEPHANTOM AI — visualization helpers (no dependencies): data structures, recursion tree, dependency graph. */
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const S = (tag, attrs = {}, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
  const H = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const PTR = new Set(['i', 'j', 'k', 'l', 'r', 'lo', 'hi', 'low', 'high', 'mid', 'left', 'right', 'start', 'end', 'idx', 'index', 'p', 'q', 'ptr', 'pos', 'slow', 'fast', 'pivot', 'curr', 'cur']);
  const LABEL = ['val', 'value', 'data', 'key', 'name', 'id', 'x'];

  function fmt(v, d = 0) {
    if (v === null) return 'None'; if (v === true) return 'True'; if (v === false) return 'False';
    if (typeof v === 'number') return String(v); if (typeof v === 'string') return JSON.stringify(v);
    switch (v.t) {
      case 'list': { const inner = v.v.map(x => fmt(x, d + 1)).join(', ') + (v.n > v.v.length ? ', …' : ''); return v.k === 'tuple' ? `(${inner})` : v.k === 'list' ? `[${inner}]` : `{${inner}}`; }
      case 'dict': return '{' + v.v.map(([k, x]) => fmt(k, d + 1) + ': ' + fmt(x, d + 1)).join(', ') + (v.n > v.v.length ? ', …' : '') + '}';
      case 'obj': return d >= 1 ? `${v.c}#${v.id}` : `${v.c}(` + Object.entries(v.a).map(([k, x]) => `${k}=${fmt(x, d + 1)}`).join(', ') + ')';
      case 'ref': return `↺${v.c}#${v.id}`;
      default: return v.v;
    }
  }
  const labelOf = o => { const k = LABEL.find(x => x in o.a); return k ? fmt(o.a[k], 1) : o.c; };

  /* ───── arrays (with pointer arrows) and 2-D grids ───── */
  function array(name, v, ints) {
    const box = H('div', 'vz'); box.appendChild(H('div', 'vz__t', `${name} · ${v.k} of ${v.n}`));
    const rows = v.v.length && v.v.every(x => x && x.t === 'list');
    if (rows) {
      const g = H('div', 'grid2d'), get = n => (ints.find(([k]) => k === n) || [])[1];
      const rr = get('i') ?? get('r') ?? get('row'), cc = get('j') ?? get('c') ?? get('col');
      v.v.forEach((row, ri) => { const line = H('div', 'cells'); row.v.forEach((x, ci) => { const c = H('div', 'cell' + (ri === rr && ci === cc ? ' hl' : '')); c.textContent = fmt(x); line.appendChild(c); }); g.appendChild(line); });
      box.appendChild(g); return box;
    }
    const ptrAt = {};
    ints.forEach(([k, val]) => { if (PTR.has(k) && val >= 0 && val <= v.v.length) (ptrAt[val] = ptrAt[val] || []).push(k); });
    const row = H('div', 'cells');
    v.v.forEach((x, i) => { const c = H('div', 'cell' + (ptrAt[i] ? ' hl' : '')); c.append(H('i', '', String(i)), H('b', '', fmt(x)), H('u', '', (ptrAt[i] || []).join(','))); row.appendChild(c); });
    if (v.n > v.v.length) row.appendChild(H('div', 'cell more', '…'));
    else if (ptrAt[v.v.length]) { const c = H('div', 'cell end'); c.append(H('i', '', String(v.v.length)), H('b', '', '∅'), H('u', '', ptrAt[v.v.length].join(','))); row.appendChild(c); }
    box.appendChild(row); return box;
  }

  /* ───── dictionaries: table, or a graph when it looks like an adjacency list ───── */
  function dictView(name, v) {
    const box = H('div', 'vz'); box.appendChild(H('div', 'vz__t', `${name} · dict of ${v.n}`));
    const adj = v.v.length >= 2 && v.v.every(([k, x]) => x && x.t === 'list' && (typeof k === 'string' || typeof k === 'number'));
    if (adj) {
      const nodes = v.v.map(([k]) => String(k)), edges = [];
      v.v.forEach(([k, x]) => x.v.forEach(n => { if (typeof n !== 'object' || n === null) { const t = String(n); if (!nodes.includes(t)) nodes.push(t); edges.push([String(k), t]); } }));
      if (edges.length) { box.appendChild(circleGraph(nodes, edges)); return box; }
    }
    const t = H('div', 'kv'); v.v.forEach(([k, x]) => { const r = H('div'); r.append(H('b', '', fmt(k)), H('span', '', fmt(x))); t.appendChild(r); }); box.appendChild(t); return box;
  }
  function circleGraph(nodes, edges) {
    const W = 300, Hh = 220, cx = W / 2, cy = Hh / 2, R = Math.min(W, Hh) / 2 - 26, svg = S('svg', { viewBox: `0 0 ${W} ${Hh}`, width: W, height: Hh, class: 'svgviz' });
    const defs = S('defs', {}, svg); const m = S('marker', { id: 'ar', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' }, defs); S('path', { d: 'M0 0L10 5L0 10z', fill: '#c0121f' }, m);
    const pos = {}; nodes.slice(0, 14).forEach((n, i) => { const a = (2 * Math.PI * i) / Math.min(nodes.length, 14) - Math.PI / 2; pos[n] = [cx + R * Math.cos(a), cy + R * Math.sin(a)]; });
    edges.forEach(([a, b]) => { if (!pos[a] || !pos[b]) return; const [x1, y1] = pos[a], [x2, y2] = pos[b], dx = x2 - x1, dy = y2 - y1, L = Math.hypot(dx, dy) || 1;
      S('line', { x1: x1 + dx / L * 14, y1: y1 + dy / L * 14, x2: x2 - dx / L * 16, y2: y2 - dy / L * 16, stroke: '#c0121f', 'stroke-width': 1.4, 'marker-end': 'url(#ar)' }, svg); });
    Object.entries(pos).forEach(([n, [x, y]]) => { S('circle', { cx: x, cy: y, r: 14, class: 'gn' }, svg); const t = S('text', { x, y: y + 4, 'text-anchor': 'middle', class: 'gt' }, svg); t.textContent = n.slice(0, 4); });
    return svg;
  }

  /* ───── objects: linked lists, binary trees, n-ary trees ───── */
  function objView(name, v, covered) {
    if (v.t !== 'obj') return null;
    const box = H('div', 'vz'), a = v.a;
    const nextKey = ['next', 'nxt', 'next_node'].find(k => k in a);
    if (nextKey) {
      box.appendChild(H('div', 'vz__t', `${name} · linked list`));
      const row = H('div', 'chain'); let cur = v, n = 0, tail = 'None';
      while (cur && cur.t === 'obj' && n < 16) { covered.add(cur.id); const nx = cur.a[nextKey]; const node = H('div', 'node', labelOf(cur)); row.appendChild(node); n++;
        if (nx && nx.t === 'ref') { tail = '↺ cycle'; row.appendChild(H('span', 'arrow', '→')); break; }
        row.appendChild(H('span', 'arrow', '→')); cur = nx; if (!nx) break; }
      row.appendChild(H('div', 'node nil', tail)); box.appendChild(row); return box;
    }
    const L = ['left', 'l'].find(k => k in a), R = ['right', 'r'].find(k => k in a);
    if (L || R) { box.appendChild(H('div', 'vz__t', `${name} · binary tree`)); box.appendChild(treeSvg(v, n => [L && n.a[L], R && n.a[R]], false, covered)); return box; }
    if (a.children && a.children.t === 'list') { box.appendChild(H('div', 'vz__t', `${name} · tree`)); box.appendChild(treeSvg(v, n => (n.a.children && n.a.children.t === 'list' ? n.a.children.v : []), true, covered)); return box; }
    box.appendChild(H('div', 'vz__t', `${name} · ${v.c} object`)); const t = H('div', 'kv');
    Object.entries(a).forEach(([k, x]) => { const r = H('div'); r.append(H('b', '', k), H('span', '', fmt(x, 1))); t.appendChild(r); }); box.appendChild(t); return box;
  }
  function treeSvg(root, kids, nary, covered) {
    const nodes = [], edges = []; let leaf = 0;
    (function walk(n, d) {
      if (!n || n.t !== 'obj' || nodes.length >= 31 || d > 5) return null;
      const me = { n, d, x: 0 }; nodes.push(me); const ks = kids(n);
      if (nary) { const cs = ks.map(k => walk(k, d + 1)).filter(Boolean); me.x = cs.length ? cs.reduce((s, c) => s + c.x, 0) / cs.length : leaf++; cs.forEach(c => edges.push([me, c])); }
      else { const l = walk(ks[0], d + 1); me.x = leaf++; const r = walk(ks[1], d + 1); if (l) edges.push([me, l]); if (r) edges.push([me, r]); }
      return me;
    })(root, 0);
    const XS = 44, YS = 52, W = Math.max(leaf, 1) * XS + 20, Hh = (Math.max(...nodes.map(n => n.d)) + 1) * YS + 10, svg = S('svg', { viewBox: `0 0 ${W} ${Hh}`, width: W, height: Hh, class: 'svgviz' });
    edges.forEach(([a, b]) => S('line', { x1: a.x * XS + 22, y1: a.d * YS + 22, x2: b.x * XS + 22, y2: b.d * YS + 22, stroke: '#7b2330', 'stroke-width': 1.5 }, svg));
    nodes.forEach(m => { if (covered) covered.add(m.n.id); S('circle', { cx: m.x * XS + 22, cy: m.d * YS + 22, r: 16, class: 'gn' }, svg); const t = S('text', { x: m.x * XS + 22, y: m.d * YS + 26, 'text-anchor': 'middle', class: 'gt' }, svg); t.textContent = labelOf(m.n).slice(0, 4); });
    return svg;
  }

  function structures(vars) {
    const root = H('div', 'viz'), ints = Object.entries(vars).filter(([, x]) => typeof x === 'number' && Number.isInteger(x));
    let any = false; const covered = new Set(), aliases = [];
    for (const [name, v] of Object.entries(vars)) {
      if (!v || typeof v !== 'object') continue;
      let node = null;
      if (v.t === 'obj' && covered.has(v.id)) { aliases.push(name); continue; }          // already drawn as part of another variable
      if (v.t === 'list') node = array(name, v, ints); else if (v.t === 'dict') node = dictView(name, v); else if (v.t === 'obj') node = objView(name, v, covered);
      if (node) { any = true; root.appendChild(node); }
    }
    if (aliases.length) root.appendChild(H('div', 'tinfo', `${aliases.join(', ')} point into the structure(s) drawn above.`));
    if (!any) root.appendChild(H('div', 'tinfo', 'No lists, dicts, linked lists, trees or graphs in scope at this step.'));
    return root;
  }

  /* ───── recursion: call tree that grows (and fills in return values) as the trace advances ───── */
  function callTree(steps, upto) {
    const roots = [], stack = []; let created = null;
    for (let i = 0; i <= upto; i++) {
      const s = steps[i];
      if (s.e === 'call') { const node = { name: s.s[s.s.length - 1] || '?', args: Object.entries(s.v).map(([k, x]) => `${k}=${fmt(x, 1)}`).join(', '), kids: [], ret: undefined, d: stack.length, i };
        (stack.length ? stack[stack.length - 1].kids : roots).push(node); stack.push(node); if (i === upto) created = node; }
      else if (s.e === 'return' && stack.length) { const n = stack.pop(); n.ret = s.r; }
    }
    const cur = stack[stack.length - 1] || null;
    if (!roots.length) return H('div', 'tinfo', 'No function calls yet. Step into a function to see its call tree.');
    const all = []; let leaf = 0;
    const place = n => { all.push(n); if (!n.kids.length) n.x = leaf++; else { n.kids.forEach(place); n.x = n.kids.reduce((s, c) => s + c.x, 0) / n.kids.length; } };
    roots.forEach(place);
    const shown = all.slice(0, 60), XS = 92, YS = 54, W = Math.max(leaf, 1) * XS + 20, Hh = (Math.max(...shown.map(n => n.d)) + 1) * YS + 30;
    const svg = S('svg', { viewBox: `0 0 ${W} ${Hh}`, width: W, height: Hh, class: 'svgviz calltree' });
    const link = n => n.kids.forEach(k => { if (shown.includes(k)) { S('line', { x1: n.x * XS + 46, y1: n.d * YS + 34, x2: k.x * XS + 46, y2: k.d * YS + 6, stroke: '#7b2330', 'stroke-width': 1.4 }, svg); link(k); } });
    roots.forEach(link);
    shown.forEach(n => {
      const g = S('g', { class: 'cn' + (n === cur ? ' cur' : '') + (n.ret !== undefined ? ' done' : '') + (n === created ? ' new' : '') }, svg);
      S('rect', { x: n.x * XS + 4, y: n.d * YS + 6, width: 84, height: 28, rx: 6 }, g);
      const t = S('text', { x: n.x * XS + 46, y: n.d * YS + 18, 'text-anchor': 'middle', class: 'gt' }, g); t.textContent = `${n.name}(${n.args.replace(/[a-z_]+=/gi, '')})`.slice(0, 14);
      if (n.ret !== undefined) { const r = S('text', { x: n.x * XS + 46, y: n.d * YS + 30, 'text-anchor': 'middle', class: 'rt' }, g); r.textContent = '→ ' + String(n.ret).slice(0, 10); }
    });
    const wrap = H('div', 'ctwrap'); wrap.appendChild(svg); if (all.length > 60) wrap.appendChild(H('div', 'tinfo', `Showing the first 60 of ${all.length} calls.`)); return wrap;
  }

  /* ───── file dependency graph (layered left → right: importers → imported) ───── */
  function graph(container, data, opts = {}) {
    container.innerHTML = '';
    const hi = opts.highlight || new Set(), focus = opts.focus || null;
    let nodes = data.nodes.slice().sort((a, b) => (b.in + b.out) - (a.in + a.out)).slice(0, 70);
    const ids = new Set(nodes.map(n => n.id)), edges = data.edges.filter(e => ids.has(e.s) && ids.has(e.t));
    if (!nodes.length) { container.appendChild(H('div', 'tinfo', 'No source files to draw.')); return; }
    const layer = new Map(nodes.map(n => [n.id, 0]));
    for (let it = 0; it < nodes.length; it++) { let ch = false; for (const e of edges) if (layer.get(e.t) < layer.get(e.s) + 1 && layer.get(e.s) < nodes.length) { layer.set(e.t, layer.get(e.s) + 1); ch = true; } if (!ch) break; }
    const linked = new Set(edges.flatMap(e => [e.s, e.t])), maxL = Math.max(0, ...[...layer.entries()].filter(([k]) => linked.has(k)).map(([, v]) => v));
    nodes.forEach(n => { if (!linked.has(n.id)) layer.set(n.id, maxL + 1); });
    const cols = [], pos = new Map();
    nodes.forEach(n => { (cols[layer.get(n.id)] = cols[layer.get(n.id)] || []).push(n); });
    cols.forEach((c, ci) => c && c.sort((a, b) => a.id.localeCompare(b.id)).forEach((n, ri) => pos.set(n.id, [ci * 210 + 10, ri * 38 + 12])));
    const W = cols.length * 210 + 20, Hh = Math.max(...cols.map(c => (c ? c.length : 0))) * 38 + 30;
    const svg = S('svg', { viewBox: `0 0 ${W} ${Hh}`, width: W, height: Hh, class: 'svgviz depgraph' });
    const defs = S('defs', {}, svg); [['gar', '#5f5b55'], ['gar2', '#ff2b2b']].forEach(([id, col]) => { const m = S('marker', { id, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto' }, defs); S('path', { d: 'M0 0L10 5L0 10z', fill: col }, m); });
    const near = new Set(focus ? [focus] : []); if (focus) edges.forEach(e => { if (e.s === focus) near.add(e.t); if (e.t === focus) near.add(e.s); });
    edges.forEach(e => { const [x1, y1] = pos.get(e.s), [x2, y2] = pos.get(e.t), hot = hi.has(e.s) && hi.has(e.t), dim = focus && !(e.s === focus || e.t === focus);
      S('path', { d: `M${x1 + 186} ${y1 + 11} C${x1 + 210} ${y1 + 11},${x2 - 24} ${y2 + 11},${x2} ${y2 + 11}`, fill: 'none', stroke: hot ? '#ff2b2b' : '#5f5b55', 'stroke-width': hot ? 2 : 1, opacity: dim ? 0.12 : 0.8, 'marker-end': hot ? 'url(#gar2)' : 'url(#gar)' }, svg); });
    nodes.forEach(n => { const [x, y] = pos.get(n.id), g = S('g', { class: 'gf' + (hi.has(n.id) ? ' hot' : '') + (n.test ? ' test' : '') + (focus === n.id ? ' sel' : ''), opacity: focus && !near.has(n.id) ? 0.25 : 1 }, svg);
      S('rect', { x, y, width: 186, height: 24, rx: 5 }, g); const t = S('text', { x: x + 8, y: y + 16, class: 'gt' }, g); t.textContent = (n.id.length > 26 ? '…' + n.id.slice(-25) : n.id);
      const tt = S('title', {}, g); tt.textContent = `${n.id} — ${n.lang}, ${n.lines} lines, imports ${n.out}, imported by ${n.in}`;
      if (opts.onClick) g.addEventListener('click', () => opts.onClick(n)); });
    const wrap = H('div', 'graphwrap'); wrap.appendChild(svg); container.appendChild(wrap);
    if (data.nodes.length > nodes.length) container.appendChild(H('div', 'tinfo', `Showing the ${nodes.length} most connected of ${data.nodes.length} files.`));
  }

  window.CPViz = { fmt, structures, callTree, graph, H, S };
})();

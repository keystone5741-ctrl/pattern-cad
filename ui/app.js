/* pattern-cad 화면 — 1단계: 캔버스 + 치수 패널.
   엔진(patterncad/)이 계산한 점·선을 그대로 그린다. 좌표는 인치, 화면 배율 K px/inch.
   치수를 고치거나 점을 끌면 서버에 다시 계산을 시켜 그린다 (서버는 상태가 없다 — 수정값은 여기 있다). */
'use strict';

const K = 24;                       // 배율 1 = 1 inch 당 24px
const ROLES = ['outline', 'dart', 'construction', 'mark', 'notch', 'grain', 'fold', 'dimension'];
const ROLE_KO = {outline: '완성선', dart: '다트·턱', construction: '안내선', mark: '표시', notch: '노치', grain: '식서', fold: '골선', dimension: '치수선'};

const S = {
  kind: 'style', id: null, data: null,
  overrides: {}, pointOverrides: {},
  unit: 'in', sel: null,                // sel: {type:'point'|'line', block, name}
  layers: Object.fromEntries(ROLES.map(r => [r, r !== 'dimension'])), labels: false, helpers: false,
  k: 1, px: 40, py: 40,                 // 확대 배율, 이동
  seq: 0, busy: false,
};

const $ = id => document.getElementById(id);
const cv = $('cv'), world = $('world');

// ------------------------------------------------------------ 단위
function parseInch(t) {
  if (typeof t === 'number') return t;
  let s = String(t).trim().replace(/[",”″“]|in(ch(es)?)?$/gi, '').trim();
  if (!s) return null;
  let sign = 1;
  if (s[0] === '-' || s[0] === '+') { sign = s[0] === '-' ? -1 : 1; s = s.slice(1).trim(); }
  let m;
  if ((m = s.match(/^(\d+)\.(\d+)\/(\d+)$/))) return sign * (+m[1] + m[2] / m[3]);          // 3.1/2
  if ((m = s.match(/^(\d+(?:\.\d+)?)\s*[-+ ]\s*(\d+)\s*\/\s*(\d+)$/))) return sign * (+m[1] + m[2] / m[3]);
  if ((m = s.match(/^(\d+)\s*\/\s*(\d+)$/))) return sign * (m[1] / m[2]);
  if ((m = s.match(/^\d+(?:\.\d+)?$/))) return sign * +s;
  return null;
}
function toFraction(v, den = 16) {
  const sign = v < 0 ? '-' : '';
  let total = Math.round(Math.abs(v) * den);
  let whole = Math.floor(total / den), num = total % den;
  if (num === 0) return sign + whole;
  let g = gcd(num, den); num /= g; const d = den / g;
  return sign + (whole ? whole + '.' : '') + num + '/' + d;      // 포트폴리오식 3.1/2
}
function gcd(a, b) { return b ? gcd(b, a % b) : a; }
function fmt(v) {                              // 인치 실수 → 현재 단위 글자
  if (v == null || typeof v !== 'number') return v == null ? '' : String(v);
  if (S.unit === 'in') return toFraction(v);
  if (S.unit === 'cm') return (v * 2.54).toFixed(1);
  return Math.round(v * 25.4) + '';
}
function unitLabel() { return S.unit === 'in' ? '"' : S.unit; }
function parseUnit(t) {                        // 현재 단위 글자 → 인치 실수 (못 읽으면 null)
  if (S.unit === 'in') return parseInch(t);
  const n = parseFloat(String(t).replace(',', ''));
  if (isNaN(n)) return null;
  return S.unit === 'cm' ? n / 2.54 : n / 25.4;
}

// ------------------------------------------------------------ 서버
async function post(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (r.headers.get('content-type')?.includes('json')) {
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }
  if (!r.ok) throw new Error(await r.text());
  return r;
}
function payload() { return {kind: S.kind, id: S.id, overrides: S.overrides, point_overrides: S.pointOverrides}; }

let evalTimer = null;
function scheduleEval(delay = 60) { clearTimeout(evalTimer); evalTimer = setTimeout(evaluate, delay); }
async function evaluate() {
  const my = ++S.seq;
  S.busy = true; $('busy').textContent = '계산 중…';
  try {
    const d = await post('/api/eval', payload());
    if (my !== S.seq) return;                  // 더 새 요청이 나갔다
    S.data = d;
    $('err').hidden = true;
    draw(); renderTree(); renderMeas(); renderSel(); renderLineage();
    $('title').textContent = d.name;
    $('counts').textContent = `조각 ${d.pieces.length} · 점 ${d.blocks.reduce((n, b) => n + b.points.length, 0)} · 선 ${d.blocks.reduce((n, b) => n + b.lines.length, 0)}`;
  } catch (e) {
    $('err').hidden = false; $('err').textContent = e.message;
  } finally {
    if (my === S.seq) { S.busy = false; $('busy').textContent = ''; }
  }
}

// ------------------------------------------------------------ 좌표
const pieceOf = (block, piece) => S.data.pieces.find(p => p.block === block && p.piece === piece) || {dx: 0, dy: 0};
function toScreen(x, y, off) { return [(x + off.dx) * K, (y + off.dy) * K]; }          // world px (확대 전)
function clientToWorld(ev) {
  const r = cv.getBoundingClientRect();
  return [(ev.clientX - r.left - S.px) / S.k, (ev.clientY - r.top - S.py) / S.k];
}
function applyView() {
  world.setAttribute('transform', `translate(${S.px} ${S.py}) scale(${S.k})`);
  $('zoom').textContent = Math.round(S.k * 100) + '%';
  const rr = 4 / S.k;
  world.querySelectorAll('.pt').forEach(c => c.setAttribute('r', rr));
  world.querySelectorAll('.hdot').forEach(c => c.setAttribute('r', 3 / S.k));
  world.querySelectorAll('text').forEach(t => t.setAttribute('transform', `translate(${t.dataset.x} ${t.dataset.y}) scale(${1 / S.k})`));
}
function fitAll(pieceFilter) {
  if (!S.data) return;
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (const p of S.data.pieces) {
    if (pieceFilter && !pieceFilter(p)) continue;
    const [ax, ay] = toScreen(p.bbox[0], p.bbox[1], p), [bx, by] = toScreen(p.bbox[2], p.bbox[3], p);
    x0 = Math.min(x0, ax); y0 = Math.min(y0, ay); x1 = Math.max(x1, bx); y1 = Math.max(y1, by);
  }
  const r = cv.getBoundingClientRect(), m = 40;
  S.k = Math.min((r.width - 2 * m) / (x1 - x0 || 1), (r.height - 2 * m) / (y1 - y0 || 1));
  S.k = Math.max(0.05, Math.min(20, S.k));
  S.px = (r.width - (x1 - x0) * S.k) / 2 - x0 * S.k;
  S.py = (r.height - (y1 - y0) * S.k) / 2 - y0 * S.k;
  applyView();
}

// ------------------------------------------------------------ 그리기
const NS = 'http://www.w3.org/2000/svg';
function el(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function pathD(line, off) {
  const P = ([x, y]) => toScreen(x, y, off).map(v => v.toFixed(2)).join(' ');
  if (line.kind === 'curve') {
    let d = 'M' + P(line.beziers[0][0]);
    for (const b of line.beziers) d += `C${P(b[1])} ${P(b[2])} ${P(b[3])}`;
    return d;
  }
  return 'M' + line.pts.map(P).join('L');
}
function text(x, y, s, cls, parent, dx = 0, dy = 0) {
  // 자리(x, y)는 그림 좌표, 밀어 놓는 양(dx, dy)은 화면 픽셀 — 확대해도 글자 크기·간격이 그대로
  const t = el('text', {class: cls, x: dx, y: dy, 'data-x': x.toFixed(2), 'data-y': y.toFixed(2)}, parent);
  t.textContent = s;
  return t;
}
function draw() {
  world.innerHTML = '';
  const d = S.data;
  for (const b of d.blocks) {                   // 원형 이름은 한 번, 조각 이름은 조각마다
    const pcs = d.pieces.filter(p => p.block === b.key);
    if (!pcs.length) continue;
    let x0 = 1e9, y0 = 1e9;
    for (const p of pcs) { const [x, y] = toScreen(p.bbox[0], p.bbox[1], p); x0 = Math.min(x0, x); y0 = Math.min(y0, y); }
    text(x0, y0, `${b.key} · ${b.name}`, 'pcname', world, 0, -24);
    for (const p of pcs) {
      if (!p.piece) continue;
      const [x, y] = toScreen(p.bbox[0], p.bbox[1], p);
      text(x, y, p.piece, 'pcname', world, 0, -7);
    }
  }
  for (const b of d.blocks) {
    const g = el('g', {'data-block': b.key}, world);
    for (const l of b.lines) {
      if (!S.layers[l.role]) continue;
      const off = pieceOf(b.key, l.piece), dd = pathD(l, off);
      const lit = S.sel && ((S.sel.type === 'line' && S.sel.block === b.key && S.sel.name === l.name) ||
                            (S.sel.type === 'point' && S.sel.block === b.key && l.points.includes(S.sel.name)));
      el('path', {d: dd, class: `r-${l.role}${lit ? ' lit' : ''}`, 'data-name': l.name}, g);
      const hit = el('path', {d: dd, class: 'hit'}, g);
      hit.addEventListener('click', ev => { ev.stopPropagation(); select({type: 'line', block: b.key, name: l.name}); });
      hit.addEventListener('mouseenter', () => hint(`${l.name}${l.ko ? ' · ' + l.ko : ''} — ${ROLE_KO[l.role]} · 길이 ${fmt(l.length)}${unitLabel()}`));
      hit.addEventListener('mouseleave', () => hint(''));
    }
    // 곡선 핸들 (선택한 곡선만)
    if (S.sel && S.sel.type === 'line' && S.sel.block === b.key) {
      const l = b.lines.find(x => x.name === S.sel.name);
      if (l && l.kind === 'curve') {
        const off = pieceOf(b.key, l.piece);
        for (const bz of l.beziers) {
          const [p0, c1, c2, p3] = bz.map(([x, y]) => toScreen(x, y, off));
          el('line', {x1: p0[0], y1: p0[1], x2: c1[0], y2: c1[1], class: 'handle'}, g);
          el('line', {x1: p3[0], y1: p3[1], x2: c2[0], y2: c2[1], class: 'handle'}, g);
          el('circle', {cx: c1[0], cy: c1[1], r: 3, class: 'hdot'}, g);
          el('circle', {cx: c2[0], cy: c2[1], r: 3, class: 'hdot'}, g);
        }
      }
    }
    // 점: 그 점을 지나는 선이 하나라도 보일 때만. 어느 선에도 안 쓰이는 보조점은 안내선 층을 따른다
    const usedRoles = {};
    for (const l of b.lines) for (const n of l.points) (usedRoles[n] ||= new Set()).add(l.role);
    for (const p of b.points) {
      const roles = usedRoles[p.name];
      const visible = roles ? [...roles].some(r => S.layers[r]) : S.helpers;   // 어느 선에도 안 쓰이는 보조점은 따로 켠다
      const on = S.sel && S.sel.type === 'point' && S.sel.block === b.key && S.sel.name === p.name;
      if (!visible && !on) continue;
      const off = pieceOf(b.key, p.piece), [x, y] = toScreen(p.x, p.y, off);
      const c = el('circle', {cx: x, cy: y, r: 4, class: `pt${on ? ' on' : ''}${p.override ? ' ov' : ''}`}, g);
      c.addEventListener('pointerdown', ev => startDrag(ev, b.key, p, off, c));
      c.addEventListener('mouseenter', () => hint(`${p.name}${p.ko ? ' · ' + p.ko : ''} — ${p.rule}`));
      c.addEventListener('mouseleave', () => hint(''));
      if (S.labels || on) text(x, y, p.name, 'lbl' + (on ? ' acc' : ''), g, 7, -7);
    }
  }
  applyView();
}
function hint(s) { $('hint').textContent = s; }

// ------------------------------------------------------------ 선택
function select(sel) { S.sel = sel; draw(); renderSel(); }
cv.addEventListener('click', ev => { if (ev.target === $('bg')) select(null); });

function renderSel() {
  const box = $('sel');
  if (!S.sel || !S.data) { box.className = 'small muted'; box.textContent = '점이나 선을 누르세요. 점은 끌어 옮길 수 있습니다.'; return; }
  box.className = '';
  const b = S.data.blocks.find(x => x.key === S.sel.block);
  if (S.sel.type === 'point') {
    const p = b.points.find(x => x.name === S.sel.name);
    if (!p) { S.sel = null; return renderSel(); }
    const key = `${b.key}.${p.name}`;
    const users = b.lines.filter(l => l.points.includes(p.name)).map(l => l.name).join(' · ') || '—';
    box.innerHTML = `<div class="kv">
      <b>점</b><div><strong>${p.name}</strong> ${p.ko ? '· ' + p.ko : ''} <span class="muted">(${b.key}${p.piece ? ' · ' + p.piece : ''})</span></div>
      <b>규칙</b><div>${esc(p.rule)}</div>
      ${p.note ? `<b>메모</b><div class="note">${esc(p.note)}</div>` : ''}
      <b>좌표</b><div>x ${fmt(p.x)}  y ${fmt(p.y)} ${unitLabel()} <span class="muted small">(원형 기준)</span></div>
      <b>수정값</b><div>${p.override ? `<span class="tag ov">끌어 옮김</span> 규칙대로면 x ${fmt(p.computed[0])} y ${fmt(p.computed[1])} <button class="linkbtn" id="resetPt">되돌리기</button>` : '없음'}</div>
      <b>지나는 선</b><div>${esc(users)}</div></div>`;
    $('resetPt')?.addEventListener('click', () => { delete S.pointOverrides[key]; scheduleEval(0); });
  } else {
    const l = b.lines.find(x => x.name === S.sel.name);
    if (!l) { S.sel = null; return renderSel(); }
    box.innerHTML = `<div class="kv">
      <b>선</b><div><strong>${l.name}</strong> ${l.ko ? '· ' + l.ko : ''} <span class="muted">(${b.key}${l.piece ? ' · ' + l.piece : ''})</span></div>
      <b>역할</b><div>${ROLE_KO[l.role]} · ${l.kind === 'curve' ? '곡선 (베지어 ' + l.beziers.length + '구간)' : '직선'}</div>
      <b>길이</b><div>${fmt(l.length)} ${unitLabel()}</div>
      <b>점</b><div>${l.points.map(n => `<button class="linkbtn" data-pt="${n}">${n}</button>`).join(' → ')}</div>
      ${l.kind === 'curve' ? '<b></b><div class="note">핸들은 보기만 — 끌어 고치는 건 다음 단계</div>' : ''}</div>`;
    box.querySelectorAll('[data-pt]').forEach(btn => btn.addEventListener('click', () => select({type: 'point', block: b.key, name: btn.dataset.pt})));
  }
}
const esc = s => String(s).replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));

// ------------------------------------------------------------ 점 끌기 → 수정값
function startDrag(ev, block, p, off, circle) {
  ev.preventDefault(); ev.stopPropagation();
  const key = `${block}.${p.name}`;
  const start = clientToWorld(ev), orig = [p.x, p.y];
  let moved = false;
  cv.classList.add('dragging');
  const move = e => {
    const w = clientToWorld(e);
    const nx = orig[0] + (w[0] - start[0]) / K, ny = orig[1] + (w[1] - start[1]) / K;
    if (!moved && Math.hypot(w[0] - start[0], w[1] - start[1]) < 2) return;
    moved = true;
    const [sx, sy] = toScreen(nx, ny, off);
    circle.setAttribute('cx', sx); circle.setAttribute('cy', sy);
    S.pointOverrides[key] = [nx, ny];
    scheduleEval(80);                           // 끄는 동안에도 뒷 점들이 따라오게
  };
  const up = () => {
    window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
    cv.classList.remove('dragging');
    if (!moved) select({type: 'point', block, name: p.name});
    else { S.sel = {type: 'point', block, name: p.name}; scheduleEval(0); }
  };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
}

// ------------------------------------------------------------ 이동·확대
let pan = null;
cv.addEventListener('pointerdown', ev => {
  if (ev.target.classList.contains('pt')) return;
  pan = {x: ev.clientX, y: ev.clientY, px: S.px, py: S.py};
  cv.classList.add('panning'); cv.setPointerCapture(ev.pointerId);
});
cv.addEventListener('pointermove', ev => {
  const [wx, wy] = clientToWorld(ev);
  $('cursor').textContent = `x ${fmt(wx / K)}  y ${fmt(wy / K)} ${unitLabel()}`;
  if (pan) { S.px = pan.px + ev.clientX - pan.x; S.py = pan.py + ev.clientY - pan.y; applyView(); }
});
cv.addEventListener('pointerup', () => { pan = null; cv.classList.remove('panning'); });
cv.addEventListener('wheel', ev => {
  ev.preventDefault();
  const r = cv.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
  const f = Math.exp(-ev.deltaY * 0.0015), nk = Math.max(0.05, Math.min(20, S.k * f));
  S.px = mx - (mx - S.px) * (nk / S.k); S.py = my - (my - S.py) * (nk / S.k); S.k = nk;
  applyView();
}, {passive: false});
$('fit').addEventListener('click', () => fitAll());

// ------------------------------------------------------------ 왼쪽: 조각 나무 · 층 · 원형 이력
function renderTree() {
  const t = $('tree'); t.innerHTML = '';
  for (const b of S.data.blocks) {
    const h = document.createElement('div'); h.className = 'blk'; h.textContent = `${b.key} · ${b.name}`;
    h.addEventListener('click', () => fitAll(p => p.block === b.key)); t.appendChild(h);
    for (const pc of b.pieces) {
      if (!pc) continue;
      const e = document.createElement('div'); e.className = 'pc'; e.textContent = pc;
      e.addEventListener('click', () => fitAll(p => p.block === b.key && p.piece === pc)); t.appendChild(e);
    }
  }
}
function renderLayers() {
  const box = $('layers'); box.innerHTML = '';
  for (const r of ROLES) {
    const l = document.createElement('label'); l.className = 'chk';
    l.innerHTML = `<input type="checkbox" ${S.layers[r] ? 'checked' : ''}> ${ROLE_KO[r]}`;
    l.querySelector('input').addEventListener('change', e => { S.layers[r] = e.target.checked; draw(); });
    box.appendChild(l);
  }
  for (const [key, label] of [['labels', '점 이름'], ['helpers', '보조점 (선에 안 쓰이는 점)']]) {
    const l = document.createElement('label'); l.className = 'chk';
    l.innerHTML = `<input type="checkbox" ${S[key] ? 'checked' : ''}> ${label}`;
    l.querySelector('input').addEventListener('change', e => { S[key] = e.target.checked; draw(); });
    box.appendChild(l);
  }
}
function renderLineage() {
  const box = $('lineage'); box.innerHTML = '';
  for (const b of S.data.blocks) {
    const e = document.createElement('div');
    e.innerHTML = `<b>${b.key}</b> ${b.name}${b.extends ? ` <span class="muted">← ${b.extends}</span>` : ''}<br><span class="muted">${b.source || ''}</span>`;
    box.appendChild(e);
  }
}

// ------------------------------------------------------------ 오른쪽: 치수
let measTab = null;
function renderMeas() {
  const tabs = $('blockTabs'); tabs.innerHTML = '';
  if (!S.data.blocks.some(b => b.key === measTab)) measTab = S.data.blocks[0].key;
  if (S.data.blocks.length > 1) for (const b of S.data.blocks) {
    const btn = document.createElement('button'); btn.textContent = b.key; if (b.key === measTab) btn.className = 'on';
    btn.addEventListener('click', () => { measTab = b.key; renderMeas(); }); tabs.appendChild(btn);
  }
  const b = S.data.blocks.find(x => x.key === measTab);
  const box = $('meas');
  const rows = b.measurements.map(m => {
    const key = `${b.key}.${m.name}`;
    let cell;
    if (m.kind === 'choice' && m.editable) {
      cell = `<select data-key="${key}" class="${m.modified ? 'mod' : ''}">${m.options.map(o => `<option ${o === m.value ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
    } else if (m.editable) {
      cell = `<input data-key="${key}" value="${typeof m.value === 'number' ? fmt(m.value) : esc(m.value)}" class="${m.modified ? 'mod' : ''}">`;
    } else if (m.linked) {
      cell = `<input value="${typeof m.value === 'number' ? fmt(m.value) : esc(m.value)}" class="linked" readonly title="${esc(m.linked)}">`;
    } else {
      cell = `<span class="num">${typeof m.value === 'number' ? fmt(m.value) : esc(m.value)}</span>`;
    }
    const sub = m.linked ? `<div class="formula">← ${esc(m.linked)}</div>` : m.formula ? `<div class="formula">= ${esc(m.formula)}</div>` : '';
    const reset = m.modified ? ` <button class="linkbtn" data-reset="${key}" title="스타일 값으로">↺</button>` : '';
    return `<tr><td><div>${esc(m.name)}${m.ko ? ` <span class="muted">${esc(m.ko)}</span>` : ''}</div>${sub}${m.note ? `<div class="note">${esc(m.note)}</div>` : ''}</td><td class="num">${cell}${reset}</td></tr>`;
  });
  box.innerHTML = `<table><tr><th>치수</th><th>값 (${unitLabel()})</th></tr>${rows.join('')}</table>`;
  box.querySelectorAll('input[data-key]').forEach(inp => {
    const commit = () => {
      const v = parseUnit(inp.value);
      if (v == null) { inp.style.borderColor = '#c33'; return; }
      S.overrides[inp.dataset.key] = v; scheduleEval(0);
    };
    inp.addEventListener('change', commit);
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') { commit(); inp.blur(); } });
  });
  box.querySelectorAll('select[data-key]').forEach(sel => sel.addEventListener('change', () => { S.overrides[sel.dataset.key] = sel.value; scheduleEval(0); }));
  box.querySelectorAll('[data-reset]').forEach(btn => btn.addEventListener('click', () => { delete S.overrides[btn.dataset.reset]; scheduleEval(0); }));
}

// ------------------------------------------------------------ 위쪽
$('units').querySelectorAll('button').forEach(btn => btn.addEventListener('click', () => {
  S.unit = btn.dataset.u;
  $('units').querySelectorAll('button').forEach(b => b.classList.toggle('on', b === btn));
  renderMeas(); renderSel();
}));
$('resetAll').addEventListener('click', () => { S.overrides = {}; S.pointOverrides = {}; scheduleEval(0); });
$('saveSvg').addEventListener('click', async () => {
  try {
    const r = await post('/api/svg', payload());
    const blob = await r.blob(), a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `${S.id}.svg`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) { $('err').hidden = false; $('err').textContent = e.message; }
});
$('picker').addEventListener('change', () => {
  const [kind, id] = $('picker').value.split(':');
  S.kind = kind; S.id = id; S.overrides = {}; S.pointOverrides = {}; S.sel = null; measTab = null;
  location.hash = `${kind}/${id}`;
  evaluate().then(() => fitAll());
});

async function init() {
  const cat = await (await fetch('/api/catalog')).json();
  const pk = $('picker');
  const grp = (label, kind, items) => {
    const g = document.createElement('optgroup'); g.label = label;
    for (const it of items) { const o = document.createElement('option'); o.value = `${kind}:${it.id}`; o.textContent = `${it.name}  (${it.id})`; g.appendChild(o); }
    pk.appendChild(g);
  };
  grp('스타일', 'style', cat.styles); grp('원형', 'block', cat.blocks);
  const [hpath, hsel] = location.hash.slice(1).split('@');
  const h = hpath.split('/');
  if (h.length === 2 && [...pk.options].some(o => o.value === `${h[0]}:${h[1]}`)) { S.kind = h[0]; S.id = h[1]; }
  else { S.kind = 'style'; S.id = cat.styles.find(s => s.id === 'shirt_collar_blouse') ? 'shirt_collar_blouse' : cat.styles[0].id; }
  pk.value = `${S.kind}:${S.id}`;
  renderLayers();
  await evaluate();
  fitAll();
  if (hsel && hsel.includes('.')) { const i = hsel.indexOf('.'); select({type: 'point', block: hsel.slice(0, i), name: hsel.slice(i + 1)}); }
}
window.addEventListener('resize', () => applyView());
init();

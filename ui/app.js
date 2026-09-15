/* pattern-cad 화면 — 캔버스 + 치수 패널 + 곡선 핸들 + 프로젝트 파일 + 원본 도면 겹쳐 보기 + 새 패턴 마법사.
   엔진(patterncad/)이 계산한 점·선을 그대로 그린다. 좌표는 인치, 화면 배율 K px/inch.
   치수를 고치거나 점·핸들을 끌면 서버에 다시 계산을 시켜 그린다 (서버는 상태가 없다 — 수정값은 여기 있다). */
'use strict';

const K = 24;                       // 배율 1 = 1 inch 당 24px
const ROLES = ['outline', 'dart', 'construction', 'mark', 'notch', 'grain', 'fold', 'dimension'];
const ROLE_KO = {outline: '완성선', dart: '다트·턱', construction: '안내선', mark: '표시', notch: '노치', grain: '식서', fold: '골선', dimension: '치수선'};

const S = {
  kind: 'style', id: null, data: null, projName: '',
  overrides: {}, pointOverrides: {}, lineOverrides: {}, pieceSettings: {},
  mode: 'draft', piecesData: null,          // 'draft' | 'pieces'
  grading: {system: null, base: null, sizes: []}, gradeData: null, sizeSystems: null,
  marker: {width: 58, gap: 0.25, items: []},   // items: [{key, size, rot, flip, x, y}] 원단 좌표(inch)
  compose: {}, optionsData: null,               // 디테일 옵션 {collar: 'stand', …}
  unit: 'in', sel: null, measure: null,        // sel: {type:'point'|'line', block, name}
  layers: Object.fromEntries(ROLES.map(r => [r, r !== 'dimension'])), labels: false, helpers: false,
  overlays: {}, pageCache: {},                 // overlays: 'block|piece' → {on, fit, loading}
  k: 1, px: 40, py: 40,                        // 확대 배율, 이동
  seq: 0,
};

const $ = id => document.getElementById(id);
const cv = $('cv'), world = $('world');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

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
function gcd(a, b) { return b ? gcd(b, a % b) : a; }
function toFraction(v, den = 16) {
  const sign = v < 0 ? '-' : '';
  let total = Math.round(Math.abs(v) * den);
  let whole = Math.floor(total / den), num = total % den;
  if (num === 0) return sign + whole;
  let g = gcd(num, den); num /= g; const d = den / g;
  return sign + (whole ? whole + '.' : '') + num + '/' + d;      // 포트폴리오식 3.1/2
}
function fmt(v) {                              // 인치 실수 → 현재 단위 글자
  if (v == null) return '';
  if (typeof v !== 'number') return String(v);
  if (S.unit === 'in') return toFraction(v);
  if (S.unit === 'cm') return (v * 2.54).toFixed(1);
  return Math.round(v * 25.4) + '';
}
const unitLabel = () => S.unit === 'in' ? '"' : S.unit;
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
async function getJson(url) {
  const r = await fetch(url), j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const payload = () => ({kind: S.kind, id: S.id, overrides: S.overrides, point_overrides: S.pointOverrides, line_overrides: S.lineOverrides, piece_settings: S.pieceSettings,
                        grading: S.grading.sizes.length ? S.grading : null, marker: S.marker,
                        compose: Object.keys(S.compose).length ? S.compose : null});
function showErr(e) { $('err').hidden = false; $('err').textContent = e.message || String(e); }

let evalTimer = null;
function scheduleEval(delay = 60) { clearTimeout(evalTimer); evalTimer = setTimeout(evaluate, delay); }
async function evaluate() {
  const my = ++S.seq;
  $('busy').textContent = '계산 중…';
  try {
    const d = await post('/api/eval', payload());
    if (my !== S.seq) return;                  // 더 새 요청이 나갔다
    S.data = d;
    $('err').hidden = true;
    if (S.mode === 'pieces' || S.mode === 'marker') S.piecesData = await post('/api/pieces', payload());
    S.gradeData = S.grading.sizes.length ? await post('/api/grade', payload()) : null;
    if (my !== S.seq) return;
    draw(); renderTree(); renderMeas(); renderSel(); renderLineage(); renderOverlayList(); renderGrading(); renderDetails();
    $('title').textContent = d.name;
    $('counts').textContent = `조각 ${d.pieces.length} · 점 ${d.blocks.reduce((n, b) => n + b.points.length, 0)} · 선 ${d.blocks.reduce((n, b) => n + b.lines.length, 0)}`;
  } catch (e) {
    showErr(e);
  } finally {
    if (my === S.seq) $('busy').textContent = '';
  }
}

// ------------------------------------------------------------ 좌표
const pieceOf = (block, piece) => S.data.pieces.find(p => p.block === block && p.piece === piece) || {dx: 0, dy: 0};
const toScreen = (x, y, off) => [(x + off.dx) * K, (y + off.dy) * K];          // world px (확대 전)
function clientToWorld(ev) {
  const r = cv.getBoundingClientRect();
  return [(ev.clientX - r.left - S.px) / S.k, (ev.clientY - r.top - S.py) / S.k];
}
const worldToBlock = (wx, wy, off) => [wx / K - off.dx, wy / K - off.dy];
function applyView() {
  world.setAttribute('transform', `translate(${S.px} ${S.py}) scale(${S.k})`);
  $('zoom').textContent = Math.round(S.k * 100) + '%';
  world.querySelectorAll('.pt').forEach(c => c.setAttribute('r', 4 / S.k));
  world.querySelectorAll('.hdot').forEach(c => c.setAttribute('r', 3.5 / S.k));
  world.querySelectorAll('text').forEach(t => t.setAttribute('transform', `translate(${t.dataset.x} ${t.dataset.y}) scale(${1 / S.k})`));
}
function fitAll(pieceFilter) {
  if (!S.data) return;
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  if (S.mode === 'marker') {
    const L = Math.max(markerLength(), 20);
    x0 = 0; y0 = 0; x1 = L * K; y1 = S.marker.width * K;
  }
  const boxes = S.mode === 'pieces' && S.piecesData ? S.piecesData.pieces : S.mode === 'marker' ? [] : S.data.pieces;
  for (const p of boxes) {
    if (pieceFilter && !pieceFilter(p)) continue;
    const [ax, ay] = toScreen(p.bbox[0], p.bbox[1], p), [bx, by] = toScreen(p.bbox[2], p.bbox[3], p);
    x0 = Math.min(x0, ax); y0 = Math.min(y0, ay); x1 = Math.max(x1, bx); y1 = Math.max(y1, by);
  }
  const r = cv.getBoundingClientRect(), m = 40;
  S.k = Math.max(0.05, Math.min(20, Math.min((r.width - 2 * m) / (x1 - x0 || 1), (r.height - 2 * m) / (y1 - y0 || 1))));
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
const isSel = (type, block, name) => S.sel && S.sel.type === type && S.sel.block === block && S.sel.name === name;

function draw() {
  world.innerHTML = '';
  if (S.mode === 'pieces') return drawPieces();
  if (S.mode === 'marker') return drawMarker();
  const d = S.data;
  drawOverlays();
  drawGrades();
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
      const lit = isSel('line', b.key, l.name) || (S.sel && S.sel.type === 'point' && S.sel.block === b.key && l.points.includes(S.sel.name));
      el('path', {d: dd, class: `r-${l.role}${lit ? ' lit' : ''}`, 'data-name': l.name}, g);
      const hit = el('path', {d: dd, class: 'hit'}, g);
      hit.addEventListener('click', ev => { ev.stopPropagation(); select({type: 'line', block: b.key, name: l.name}); });
      hit.addEventListener('mouseenter', () => hint(`${l.name}${l.ko ? ' · ' + l.ko : ''} — ${ROLE_KO[l.role]} · 길이 ${fmt(l.length)}${unitLabel()}`));
      hit.addEventListener('mouseleave', () => hint(''));
    }
    // 곡선 핸들 (선택한 곡선만) — 끌 수 있다
    if (S.sel && S.sel.type === 'line' && S.sel.block === b.key) {
      const l = b.lines.find(x => x.name === S.sel.name);
      if (l && l.kind === 'curve') {
        const off = pieceOf(b.key, l.piece);
        l.beziers.forEach((bz, i) => {
          const [p0, c1, c2, p3] = bz.map(([x, y]) => toScreen(x, y, off));
          const ov = l.overridden.includes(i) ? ' ov' : '';
          el('line', {x1: p0[0], y1: p0[1], x2: c1[0], y2: c1[1], class: 'handle'}, g);
          el('line', {x1: p3[0], y1: p3[1], x2: c2[0], y2: c2[1], class: 'handle'}, g);
          const h1 = el('circle', {cx: c1[0], cy: c1[1], r: 3.5, class: 'hdot' + ov}, g);
          const h2 = el('circle', {cx: c2[0], cy: c2[1], r: 3.5, class: 'hdot' + ov}, g);
          h1.addEventListener('pointerdown', ev => startHandleDrag(ev, b.key, l, i, 'c1', off));
          h2.addEventListener('pointerdown', ev => startHandleDrag(ev, b.key, l, i, 'c2', off));
        });
      }
    }
    // 점: 그 점을 지나는 선이 하나라도 보일 때만. 어느 선에도 안 쓰이는 보조점은 따로 켠다
    const usedRoles = {};
    for (const l of b.lines) for (const n of l.points) (usedRoles[n] ||= new Set()).add(l.role);
    for (const p of b.points) {
      const roles = usedRoles[p.name];
      const visible = roles ? [...roles].some(r => S.layers[r]) : S.helpers;
      const on = isSel('point', b.key, p.name);
      if (!visible && !on) continue;
      const off = pieceOf(b.key, p.piece), [x, y] = toScreen(p.x, p.y, off);
      const c = el('circle', {cx: x, cy: y, r: 4, class: `pt${on ? ' on' : ''}${p.override ? ' ov' : ''}`}, g);
      c.addEventListener('pointerdown', ev => startDrag(ev, b.key, p, off, c));
      c.addEventListener('mouseenter', () => hint(`${p.name}${p.ko ? ' · ' + p.ko : ''} — ${p.rule}`));
      c.addEventListener('mouseleave', () => hint(''));
      if (S.labels || on) text(x, y, p.name, 'lbl' + (on ? ' acc' : ''), g, 7, -7);
    }
  }
  drawMeasure();
  applyView();
}
function hint(s) { $('hint').textContent = s; }

// ------------------------------------------------------------ 그레이딩 겹쳐 보기
const GCOLORS = ['#c9302c', '#2a8a4a', '#8a4fbf', '#d98b00', '#0f8a9a', '#b5306f'];
const sizeColor = size => GCOLORS[(S.sizeSystems?.[S.grading.system]?.sizes.indexOf(size) ?? 0) % GCOLORS.length];
function drawGrades() {
  if (!S.gradeData) return;
  const bpath = (l, off) => pathD(l, off);
  for (const sz of S.gradeData.sizes) {
    const g = el('g', {class: 'grade', 'data-size': sz.size}, world);
    for (const b of sz.blocks) for (const l of b.lines) {
      if (!S.layers[l.role]) continue;
      const off = pieceOf(b.key, l.piece), sh = (b.shift || {})[l.piece] || [0, 0];
      el('path', {d: bpath(l, {dx: off.dx + sh[0], dy: off.dy + sh[1]}), class: 'gr', stroke: sizeColor(sz.size)}, g);
    }
  }
}
async function renderGrading() {
  if (!S.sizeSystems) { try { S.sizeSystems = await getJson('/api/sizes'); } catch (e) { showErr(e); return; } }
  const names = Object.keys(S.sizeSystems);
  if (!S.grading.system || !S.sizeSystems[S.grading.system]) S.grading.system = names[0];
  const sy = S.sizeSystems[S.grading.system];
  if (!sy.sizes.includes(S.grading.base)) S.grading.base = sy.sizes.includes('55') ? '55' : sy.sizes[Math.floor(sy.sizes.length / 2)];
  const sysSel = $('gSystem'); sysSel.innerHTML = names.map(n => `<option ${n === S.grading.system ? 'selected' : ''}>${esc(n)}</option>`).join('');
  const baseSel = $('gBase'); baseSel.innerHTML = sy.sizes.map(n => `<option ${n === S.grading.base ? 'selected' : ''}>${esc(n)} 기준</option>`).join('');
  const box = $('gSizes'); box.innerHTML = '';
  for (const sz of sy.sizes) {
    const l = document.createElement('label');
    if (sz === S.grading.base) { l.className = 'base'; l.textContent = sz; box.appendChild(l); continue; }
    l.innerHTML = `<input type="checkbox" ${S.grading.sizes.includes(sz) ? 'checked' : ''}><i style="background:${sizeColor(sz)}"></i>${esc(sz)}`;
    l.querySelector('input').addEventListener('change', e => {
      S.grading.sizes = e.target.checked ? [...S.grading.sizes, sz] : S.grading.sizes.filter(x => x !== sz);
      scheduleEval(0);
    });
    box.appendChild(l);
  }
  const al = Object.entries(sy.aliases || {}).map(([a, b]) => `${a}=${b}`).join(' ');
  $('gInfo').textContent = (S.gradeData ? S.gradeData.sizes.map(z => `${z.size}: ` + Object.entries(z.blocks[0].changed).slice(0, 4).map(([k, v]) => `${k} ${fmt(v)}`).join(' · ')).join('  |  ') + '  ' : '')
    + `표: data/sizes.yaml (초안)${al ? ' · ' + al : ''}. 켠 사이즈는 DXF 에 함께 나간다`;
}
$('gSystem').addEventListener('change', e => { S.grading.system = e.target.value; S.grading.base = null; S.grading.sizes = []; renderGrading(); scheduleEval(0); });
$('gBase').addEventListener('change', e => { S.grading.base = e.target.value.replace(/ 기준$/, ''); S.grading.sizes = S.grading.sizes.filter(x => x !== S.grading.base); renderGrading(); scheduleEval(0); });

// ------------------------------------------------------------ 디테일 옵션 (부속 갈아끼우기)
function optionRows(slots, current, onPick) {
  const box = document.createElement('div'); box.className = 'col';
  for (const sl of slots) {
    const row = document.createElement('div'); row.className = 'optrow';
    row.innerHTML = `<b>${esc(sl.label)}</b>`;
    for (const ch of sl.choices) {
      const t = document.createElement('span');
      const on = (current[sl.id] ?? sl.current) === ch.id;
      t.className = 'tag' + (on ? ' on' : '') + (ch.available ? '' : ' off');
      t.textContent = ch.label;
      if (!ch.available) t.title = '몸판에 없음: ' + ch.missing.join(', ');
      else t.addEventListener('click', () => onPick(sl.id, ch.id));
      row.appendChild(t);
    }
    box.appendChild(row);
  }
  return box;
}
async function renderDetails() {
  const sec = $('detailSec'), box = $('details');
  if (S.kind !== 'style') { sec.hidden = true; return; }
  sec.hidden = false;
  try { S.optionsData = await post('/api/options', {id: S.id, compose: Object.keys(S.compose).length ? S.compose : null}); }
  catch (e) { box.textContent = e.message; return; }
  box.innerHTML = '';
  box.appendChild(optionRows(S.optionsData.slots, S.compose, (slot, id) => {
    S.compose = {...S.compose, [slot]: id};
    S.sel = null; S.marker.items = []; scheduleEval(0);
  }));
  if (S.optionsData.slots.some(sl => sl.needs_slot && (S.compose[sl.needs_slot] ?? S.optionsData.slots.find(x => x.id === sl.needs_slot)?.current) === 'none' && (S.compose[sl.id] ?? sl.current) !== 'none')) {
    const n = document.createElement('div'); n.className = 'note'; n.textContent = '소매가 없으면 소매 밑단 부속은 뜬다'; box.appendChild(n);
  }
}

// ------------------------------------------------------------ 마카 (손으로 놓기 + 요척)
const rotPt = ([x, y], rot, flip) => {           // 조각 좌표 → 원단 좌표 (patterncad.pieces.transform_piece 와 같은 순서)
  let px = flip ? -x : x, py = y;
  const a = rot * Math.PI / 180, c = Math.cos(a), sn = Math.sin(a);
  return [px * c - py * sn, px * sn + py * c];
};
const piecesFor = size => (size && S.piecesData && S.piecesData.sizes && S.piecesData.sizes[size]) || (S.piecesData ? S.piecesData.pieces : []);
function itemPoly(it) {
  const pc = piecesFor(it.size).find(p => p.key === it.key);
  if (!pc) return null;
  return pc.cut.map(p => { const [x, y] = rotPt(p, it.rot, it.flip); return [x + it.x, y + it.y]; });
}
const polyBox = pts => pts.reduce((b, [x, y]) => [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)], [1e9, 1e9, -1e9, -1e9]);
const polyArea = pts => Math.abs(pts.reduce((a, p, i) => { const q = pts[(i + 1) % pts.length]; return a + p[0] * q[1] - q[0] * p[1]; }, 0) / 2);
function markerLength() {
  let L = 0;
  for (const it of S.marker.items) { const pl = itemPoly(it); if (pl) L = Math.max(L, polyBox(pl)[2]); }
  return L;
}
function markerItemsFromPieces() {              // 매수만큼 (둘째 장은 뒤집어) — 놓인 게 없을 때 처음 만든다. 켠 사이즈도 함께
  const items = [];
  const sizes = [null, ...Object.keys(S.piecesData.sizes || {})];
  for (const size of sizes) for (const pc of piecesFor(size)) for (let i = 0; i < pc.quantity; i++)
    items.push({key: pc.key, size: size || undefined, rot: 0, flip: i % 2 === 1, x: 0, y: 0});
  return items;
}
function autoLayout() {                         // 선반(shelf) 채우기 — 큰 것부터, 세로 폭 안에서 왼쪽부터
  const W = S.marker.width, gap = S.marker.gap;
  const items = S.marker.items.map(it => ({...it, x: 0, y: 0}));
  const sized = items.map(it => { const pl = itemPoly(it); const b = pl ? polyBox(pl) : [0, 0, 1, 1]; return {it, w: b[2] - b[0], h: b[3] - b[1], b}; })
    .sort((a, b) => b.h * b.w - a.h * a.w);
  const shelves = [];                             // {x, y, h}
  for (const s of sized) {
    let placed = false;
    for (const sh of shelves) {
      if (sh.y + s.h <= W + 1e-9 && s.h <= sh.h + 1e-9 || (sh.y + s.h <= W + 1e-9 && sh.items === 0)) {
        s.it.x = sh.x - s.b[0]; s.it.y = sh.y - s.b[1]; sh.x += s.w + gap; sh.h = Math.max(sh.h, s.h); sh.items++; placed = true; break;
      }
    }
    if (!placed) {
      // 새 선반: 세로로 쌓다가 폭이 넘치면 오른쪽에 새 열
      const last = shelves[shelves.length - 1];
      const y = last ? last.y + last.h + gap : 0;
      const colX = shelves.length ? Math.max(...shelves.map(z => z.x0)) : 0;
      if (y + s.h <= W + 1e-9) shelves.push({x0: colX, x: colX + s.w + gap, y, h: s.h, items: 1});
      else { const nx = Math.max(...shelves.map(z => z.x), 0); shelves.push({x0: nx, x: nx + s.w + gap, y: 0, h: s.h, items: 1}); }
      const sh = shelves[shelves.length - 1];
      s.it.x = sh.x0 - s.b[0]; s.it.y = sh.y - s.b[1];
    }
  }
  S.marker.items = items;
}
// 자동 네스팅 — 1/4" 격자 비트맵에 큰 조각부터 왼쪽 아래(bottom-left) 빈자리를 찾아 놓는다.
// 결 방향을 지키려고 0°·180°(+뒤집기)만 쓰고, 90° 허용을 켜면 네 방향 다 본다.
const CELL = 0.25;
function rasterize(poly, gapCells) {           // 폴리곤(inch) → {cells:[dy,dx…], w, h, ox, oy} 격자 마스크 (gap 만큼 부풀림)
  const b = polyBox(poly);
  const ox = b[0] - gapCells * CELL, oy = b[1] - gapCells * CELL;
  const w = Math.ceil((b[2] - ox) / CELL) + gapCells, h = Math.ceil((b[3] - oy) / CELL) + gapCells;
  const grid = new Uint8Array(w * h);
  for (let r = 0; r < h; r++) {                // 주사선: 셀 가운데 y 에서 다각형과 만나는 x 구간을 채운다
    const y = oy + (r + 0.5) * CELL, xs = [];
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [x1, y1] = poly[i], [x2, y2] = poly[j];
      if ((y1 > y) !== (y2 > y)) xs.push(x1 + (y - y1) * (x2 - x1) / (y2 - y1));
    }
    xs.sort((a, c) => a - c);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const c0 = Math.max(0, Math.floor((xs[k] - ox) / CELL)), c1 = Math.min(w - 1, Math.floor((xs[k + 1] - ox) / CELL));
      for (let c = c0; c <= c1; c++) grid[r * w + c] = 1;
    }
  }
  if (gapCells) {                                // 부풀리기: 이웃 셀까지
    const g2 = new Uint8Array(grid);
    for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) if (grid[r * w + c]) {
      for (let dr = -gapCells; dr <= gapCells; dr++) for (let dc = -gapCells; dc <= gapCells; dc++) {
        const rr = r + dr, cc = c + dc; if (rr >= 0 && rr < h && cc >= 0 && cc < w) g2[rr * w + cc] = 1;
      }
    }
    grid.set(g2);
  }
  const cells = [];
  for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) if (grid[r * w + c]) cells.push(r, c);
  return {cells, w, h, ox, oy};
}
function autoNest(allow90) {
  const W = S.marker.width, rows = Math.floor(W / CELL);
  const gapCells = Math.max(0, Math.round(S.marker.gap / CELL / 2));
  let cols = 200;                                // 50" 부터, 모자라면 늘린다
  let occ = new Uint8Array(rows * cols);
  const grow = () => { const n = new Uint8Array(rows * cols * 2); for (let r = 0; r < rows; r++) n.set(occ.subarray(r * cols, (r + 1) * cols), r * cols * 2); cols *= 2; occ = n; };
  const items = S.marker.items.map(it => ({...it}));
  const order = items.map((it, i) => { const pl = itemPoly({...it, x: 0, y: 0}); return {i, area: pl ? polyArea(pl) : 0}; }).sort((a, b) => b.area - a.area);
  const rots = allow90 ? [0, 90, 180, 270] : [0, 180];
  for (const {i} of order) {
    const it = items[i]; let best = null;
    for (const rot of rots) for (const flip of [it.flip, !it.flip]) {
      const pl = itemPoly({...it, rot, flip, x: 0, y: 0}); if (!pl) continue;
      const m = rasterize(pl, gapCells);
      if (m.h > rows) continue;
      let found = null;
      for (let c = 0; c + m.w <= cols + 1 && !found; c++) {
        if (c + m.w > cols) grow();
        for (let r = 0; r + m.h <= rows; r++) {
          let ok = true;
          for (let k = 0; k < m.cells.length; k += 2) { if (occ[(r + m.cells[k]) * cols + c + m.cells[k + 1]]) { ok = false; break; } }
          if (ok) { found = {r, c}; break; }
        }
      }
      if (found && (!best || found.c < best.c || (found.c === best.c && found.r < best.r))) best = {...found, rot, flip, m};
    }
    if (!best) continue;
    for (let k = 0; k < best.m.cells.length; k += 2) occ[(best.r + best.m.cells[k]) * cols + best.c + best.m.cells[k + 1]] = 1;
    it.rot = best.rot; it.flip = best.flip;
    it.x = best.c * CELL - best.m.ox + gapCells * CELL; it.y = best.r * CELL - best.m.oy + gapCells * CELL;
  }
  S.marker.items = items;
}
function drawMarker() {
  const pd = S.piecesData; if (!pd) return;
  if (!S.marker.items.length) { S.marker.items = markerItemsFromPieces(); autoNest(false); }
  const W = S.marker.width, L = Math.max(markerLength() + 2, 20);
  const g = el('g', {}, world);
  el('rect', {x: 0, y: 0, width: L * K, height: W * K, class: 'fabric'}, g);
  for (let x = 0; x <= L; x += 1) el('line', {x1: x * K, y1: 0, x2: x * K, y2: W * K, class: 'fabgrid'}, g);
  for (let y = 0; y <= W; y += 1) el('line', {x1: 0, y1: y * K, x2: L * K, y2: y * K, class: 'fabgrid'}, g);
  text(0, 0, `원단 폭 ${fmt(W)}${unitLabel()} · 결 →`, 'pcname', g, 0, -8);
  const boxes = S.marker.items.map(it => { const pl = itemPoly(it); return pl ? polyBox(pl) : null; });
  S.marker.items.forEach((it, i) => {
    const pl = itemPoly(it); if (!pl) return;
    const pc = piecesFor(it.size).find(p => p.key === it.key);
    const b = boxes[i];
    const out = b[0] < -1e-6 || b[1] < -1e-6 || b[3] > W + 1e-6;
    const bad = boxes.some((o, j) => j !== i && o && b[0] < o[2] && o[0] < b[2] && b[1] < o[3] && o[1] < b[3] && polyOverlap(pl, itemPoly(S.marker.items[j])));
    const on = S.sel && S.sel.type === 'mk' && S.sel.i === i;
    const path = el('path', {d: 'M' + pl.map(([x, y]) => `${(x * K).toFixed(2)} ${(y * K).toFixed(2)}`).join('L') + 'Z', class: 'mk' + (on ? ' on' : '') + (bad ? ' bad' : '') + (out ? ' out' : '')}, g);
    path.addEventListener('pointerdown', ev => startMarkerDrag(ev, i));
    if (pc.grain) {
      const [a, c] = pc.grain.map(p => { const [x, y] = rotPt(p, it.rot, it.flip); return [(x + it.x) * K, (y + it.y) * K]; });
      el('line', {x1: a[0], y1: a[1], x2: c[0], y2: c[1], class: 'mkg', 'marker-end': 'url(#arw)'}, g);
    }
    const cx = (b[0] + b[2]) / 2 * K, cy = (b[1] + b[3]) / 2 * K;
    text(cx, cy, `${pc.name}${it.size ? ' ' + it.size : ' ' + (S.grading.base && Object.keys(S.piecesData.sizes || {}).length ? S.grading.base : '')}${it.flip ? ' ↔' : ''}`, 'lbl mkl', g, -14, 4);
  });
  applyView();
}
function polyOverlap(a, b) {                     // 꼭짓점이 상대 안에 있는지로 (대략)
  const inside = (p, poly) => { let c = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > p[1]) !== (yj > p[1]) && p[0] < (xj - xi) * (p[1] - yi) / (yj - yi) + xi) c = !c; } return c; };
  return a.some(p => inside(p, b)) || b.some(p => inside(p, a));
}
function startMarkerDrag(ev, i) {
  ev.preventDefault(); ev.stopPropagation();
  const it = S.marker.items[i], start = clientToWorld(ev), ox = it.x, oy = it.y;
  let moved = false;
  S.sel = {type: 'mk', i}; draw(); renderSel();
  const move = e => {
    const w = clientToWorld(e);
    moved = true;
    it.x = Math.round((ox + (w[0] - start[0]) / K) * 8) / 8;          // 1/8" 눈금
    it.y = Math.round((oy + (w[1] - start[1]) / K) * 8) / 8;
    draw(); renderSel();
  };
  const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); if (moved) renderSel(); };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
}
function markerStats() {
  const W = S.marker.width, L = markerLength();
  let area = 0;
  for (const it of S.marker.items) { const pl = itemPoly(it); if (pl) area += polyArea(pl); }
  return {L, W, area, eff: L > 0 ? area / (L * W) : 0};
}
function renderMarkerPanel() {
  const box = $('sel'); box.className = '';
  const st = markerStats();
  const it = S.sel && S.sel.type === 'mk' ? S.marker.items[S.sel.i] : null;
  const pc = it && piecesFor(it.size).find(p => p.key === it.key);
  box.innerHTML = `<div class="stat">
    <span>원단 폭</span><span><input id="mkW" value="${fmt(S.marker.width)}" style="width:64px;text-align:right"> ${unitLabel()}</span>
    <span>조각 사이</span><span><input id="mkGap" value="${fmt(S.marker.gap)}" style="width:64px;text-align:right"> ${unitLabel()}</span>
    <span>요척 (길이)</span><b>${fmt(st.L)}${unitLabel()} = ${(st.L / 36).toFixed(2)} yd · ${(st.L * 2.54 / 100).toFixed(2)} m</b>
    <span>효율</span><b>${(st.eff * 100).toFixed(1)}%</b>
    <span>놓은 장</span><span>${S.marker.items.length}${Object.keys(S.piecesData.sizes || {}).length ? ' (사이즈 ' + [S.grading.base, ...Object.keys(S.piecesData.sizes)].join('·') + ')' : ''}</span></div>
    <div class="row" style="margin-top:8px;gap:6px"><button class="btn pri" id="mkNest" type="button">자동 네스팅</button><label class="chk" style="display:inline-flex"><input id="mk90" type="checkbox" ${S.marker.allow90 ? 'checked' : ''}> 90° 허용</label><button class="btn" id="mkAuto" type="button">선반</button><button class="btn" id="mkReset" type="button">매수대로 다시</button></div>
    <div class="row" style="margin-top:6px;gap:6px"><button class="btn" id="mkDxf" type="button">마카 DXF</button><button class="btn" id="mkSvg" type="button">마카 SVG</button></div>
    ${it ? `<div class="kv" style="margin-top:10px"><b>조각</b><div><strong>${esc(pc.name)}</strong> ${it.size ? esc(it.size) : ''} <span class="muted">(${esc(pc.block)})</span></div>
      <b>돌리기</b><div><button class="btn" type="button" data-rot="90">90°</button> <button class="btn" type="button" data-rot="180">180°</button> <button class="btn" type="button" data-rot="-90">−90°</button> <span class="muted">지금 ${it.rot}°</span></div>
      <b>뒤집기</b><div><button class="btn" type="button" id="mkFlip">${it.flip ? '↔ 뒤집힘' : '↔ 뒤집기'}</button></div>
      <b>자리</b><div>x ${fmt(it.x)} y ${fmt(it.y)} ${unitLabel()} · 1/8" 눈금 <button class="linkbtn" id="mkDel">빼기</button></div></div>` : '<div class="note" style="margin-top:8px">조각을 끌어 놓는다. 고른 조각은 R 로 90° 돌리고 M 으로 뒤집는다. 빨강은 겹침, 점선은 원단 밖</div>'}
    <div class="note" style="margin-top:6px">골선 조각은 골 펼치기를 켜야 한 장으로 놓인다 (조각·시접에서). 네스팅은 1/4" 격자에 큰 조각부터 왼쪽 아래 빈자리를 찾는 방식 — 결(0°·180°)을 지키고, 90° 허용을 켜면 더 촘촘해질 수 있다</div>`;
  const rebind = () => { draw(); renderSel(); };
  $('mkW').addEventListener('change', e => { const v = parseUnit(e.target.value); if (v) { S.marker.width = v; rebind(); } });
  $('mkGap').addEventListener('change', e => { const v = parseUnit(e.target.value); if (v != null) { S.marker.gap = v; rebind(); } });
  $('mkAuto').addEventListener('click', () => { autoLayout(); rebind(); fitAll(); });
  $('mk90').addEventListener('change', e => { S.marker.allow90 = e.target.checked; });
  $('mkNest').addEventListener('click', () => { $('busy').textContent = '네스팅 중…'; setTimeout(() => { autoNest(S.marker.allow90); $('busy').textContent = ''; rebind(); fitAll(); }, 10); });
  $('mkReset').addEventListener('click', () => { S.marker.items = markerItemsFromPieces(); autoLayout(); S.sel = null; rebind(); fitAll(); });
  $('mkDxf').addEventListener('click', () => download('/api/marker_dxf', {...payload(), placements: S.marker.items, width: S.marker.width}, `${S.projName || S.id}_marker.dxf`));
  $('mkSvg').addEventListener('click', () => {
    const W = S.marker.width, L = Math.max(markerLength(), 1), S_ = 25.4;
    const paths = S.marker.items.map(it => { const pl = itemPoly(it); return pl ? `<path d="M${pl.map(([x, y]) => `${(x * S_).toFixed(2)} ${(y * S_).toFixed(2)}`).join('L')}Z"/>` : ''; }).join('');
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${(L * S_).toFixed(1)}mm" height="${(W * S_).toFixed(1)}mm" viewBox="0 0 ${(L * S_).toFixed(1)} ${(W * S_).toFixed(1)}"><rect width="100%" height="100%" fill="white" stroke="#111" stroke-width="0.5"/><g fill="none" stroke="#111" stroke-width="0.35">${paths}</g></svg>`;
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([svg], {type: 'image/svg+xml'})); a.download = `${S.projName || S.id}_marker.svg`; a.click();
  });
  if (it) {
    box.querySelectorAll('[data-rot]').forEach(b => b.addEventListener('click', () => { it.rot = ((it.rot + +b.dataset.rot) % 360 + 360) % 360; rebind(); }));
    $('mkFlip').addEventListener('click', () => { it.flip = !it.flip; rebind(); });
    $('mkDel').addEventListener('click', () => { S.marker.items.splice(S.sel.i, 1); S.sel = null; rebind(); });
  }
}
async function download(url, body, filename) {
  try {
    const r = await post(url, body);
    const blob = await r.blob(), a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = filename; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) { showErr(e); }
}

// ------------------------------------------------------------ 조각·시접 보기
const pieceOff = pc => ({dx: pc.dx, dy: pc.dy});
function drawPieces() {
  const pd = S.piecesData;
  if (!pd) return;
  const poly = (pts, off) => pts.map(([x, y]) => toScreen(x, y, off).map(v => v.toFixed(2)).join(' ')).join('L');
  for (const pc of pd.pieces) {
    const off = pieceOff(pc), g = el('g', {}, world);
    const on = S.sel && S.sel.type === 'piece' && S.sel.key === pc.key;
    const cut = el('path', {d: 'M' + poly(pc.cut, off) + 'Z', class: 'cut' + (on ? ' on' : '') + (pc.warnings.length ? ' warn' : '')}, g);
    cut.addEventListener('click', ev => { ev.stopPropagation(); select({type: 'piece', key: pc.key}); });
    cut.addEventListener('mouseenter', () => hint(`${pc.name} ×${pc.quantity} ${pc.fabric} — 시접: ` + pc.edges.filter(e => !e.synthetic).map(e => `${e.name} ${fmt(e.allowance)}`).join(' · ')));
    cut.addEventListener('mouseleave', () => hint(''));
    el('path', {d: 'M' + poly(pc.loop, off) + 'Z', class: 'sew'}, g);
    for (const l of pc.internal) {
      if (!S.layers[l.role === 'fold' ? 'fold' : l.role]) continue;
      el('path', {d: 'M' + poly(l.pts, off), class: `r-${l.role}`}, g);
    }
    for (const [x, y, nx, ny, a] of pc.notches) {       // 재단선에서 안쪽으로 1/4" (a = 그 자리 시접)
      const [ax, ay] = toScreen(x + nx * a, y + ny * a, off), [bx, by] = toScreen(x + nx * (a - 0.25), y + ny * (a - 0.25), off);
      el('line', {x1: ax, y1: ay, x2: bx, y2: by, class: 'notch'}, g);
    }
    if (pc.grain) {
      const [a, b] = pc.grain, A = toScreen(a[0], a[1], off), B = toScreen(b[0], b[1], off);
      el('line', {x1: A[0], y1: A[1], x2: B[0], y2: B[1], class: 'grainl', 'marker-start': 'url(#arw)', 'marker-end': 'url(#arw)'}, g);
    }
    const [bx, by] = toScreen(pc.bbox[0], pc.bbox[1], off), [, by1] = toScreen(pc.bbox[0], pc.bbox[3], off);
    text(bx, by, `${pc.block} · ${pc.name}`, 'pcname', g, 0, -7);
    text(bx, by1, `×${pc.quantity} ${pc.fabric}${pc.unfolded ? ' · 골 펼침' : pc.fold ? ' · 골' : ''}`, 'lbl', g, 0, 14);
  }
  applyView();
}
function renderPiecePanel() {
  const box = $('sel');
  const pd = S.piecesData; if (!pd) return;
  const pc = pd.pieces.find(p => p.key === S.sel.key);
  if (!pc) { S.sel = null; return renderSel(); }
  const st = S.pieceSettings[pc.key] || {};
  const rows = pc.edges.filter(e => !e.synthetic).map(e => `<tr><td>${esc(e.name)}${e.role === 'fold' ? ' <span class="tag">골</span>' : ''}</td><td class="num">${e.role === 'fold' ? '—' : `<input data-edge="${esc(e.name)}" value="${fmt(e.allowance)}" class="${(st.allowance || {})[e.name] != null ? 'mod' : ''}">`}</td></tr>`).join('');
  box.className = '';
  box.innerHTML = `<div class="kv">
    <b>조각</b><div><strong>${esc(pc.name)}</strong> <span class="muted">(${esc(pc.block)})</span></div>
    <b>매수</b><div><input id="pcQty" type="number" min="1" value="${pc.quantity}" style="width:56px"> <select id="pcFab"><option ${pc.fabric === '겉감' ? 'selected' : ''}>겉감</option><option ${pc.fabric === '안감' ? 'selected' : ''}>안감</option><option ${pc.fabric === '심지' ? 'selected' : ''}>심지</option></select></div>
    ${pc.fold ? `<b>골선</b><div>${esc(pc.fold)} <label class="chk" style="display:inline-flex;margin-left:8px"><input id="pcUnfold" type="checkbox" ${pc.unfolded ? 'checked' : ''}> 펼쳐서 한 장으로</label></div>` : ''}
    <b>시접</b><div>기본 <input id="pcDef" value="${st.default_allowance != null ? fmt(st.default_allowance) : ''}" placeholder="변마다" style="width:56px"> ${unitLabel()}</div>
    ${pc.edges.some(e => e.synthetic) ? `<b>다트 캡</b><div><label class="chk" style="display:inline-flex"><input id="pcCap" type="checkbox" ${st.dart_cap === false ? '' : 'checked'}> 접어 자른 모양</label> <select id="pcFold" style="margin-left:6px"><option value="down" ${st.dart_fold !== 'up' ? 'selected' : ''}>아래로 접음</option><option value="up" ${st.dart_fold === 'up' ? 'selected' : ''}>위로 접음</option></select></div>` : ''}</div>
    <table style="margin-top:6px"><tr><th>변</th><th>시접 (${unitLabel()})</th></tr>${rows}</table>
    ${pc.warnings.length ? `<div class="warnbox">${pc.warnings.map(esc).join('<br>')}</div>` : ''}
    <div class="note" style="margin-top:6px">노치: 다트 다리 · 노치 표시 · 끊긴 자리 양 끝. 식서: 식서선이 없으면 세로. 다트 캡은 접는 쪽 이웃 재단선을 다트 중심선까지 연장한 꼭짓점</div>`;
  const setSt = (patch) => { S.pieceSettings[pc.key] = {...(S.pieceSettings[pc.key] || {}), ...patch}; scheduleEval(0); };
  $('pcQty').addEventListener('change', e => setSt({quantity: +e.target.value || 1}));
  $('pcFab').addEventListener('change', e => setSt({fabric: e.target.value}));
  $('pcUnfold')?.addEventListener('change', e => setSt({unfold: e.target.checked}));
  $('pcCap')?.addEventListener('change', e => setSt({dart_cap: e.target.checked}));
  $('pcFold')?.addEventListener('change', e => setSt({dart_fold: e.target.value}));
  $('pcDef').addEventListener('change', e => { const v = parseUnit(e.target.value); if (v != null) setSt({default_allowance: v}); else { const st2 = {...(S.pieceSettings[pc.key] || {})}; delete st2.default_allowance; S.pieceSettings[pc.key] = st2; scheduleEval(0); } });
  box.querySelectorAll('input[data-edge]').forEach(inp => inp.addEventListener('change', () => {
    const v = parseUnit(inp.value); if (v == null) { inp.style.borderColor = '#c33'; return; }
    setSt({allowance: {...((S.pieceSettings[pc.key] || {}).allowance || {}), [inp.dataset.edge]: v}});
  }));
}

// ------------------------------------------------------------ 선택 · 자
function select(sel) { S.sel = sel; draw(); renderSel(); }
cv.addEventListener('click', ev => { if (ev.target === $('bg')) { S.measure = null; select(null); } });

function pointOf(block, name) {
  const b = S.data.blocks.find(x => x.key === block);
  return b && b.points.find(p => p.name === name);
}
function drawMeasure() {
  $('measure').textContent = '';
  if (!S.measure) return;
  const {a, b} = S.measure, pa = pointOf(a.block, a.name), pb = pointOf(b.block, b.name);
  if (!pa || !pb) { S.measure = null; return; }
  const A = toScreen(pa.x, pa.y, pieceOf(a.block, pa.piece)), B = toScreen(pb.x, pb.y, pieceOf(b.block, pb.piece));
  el('line', {x1: A[0], y1: A[1], x2: B[0], y2: B[1], class: 'tape'}, world);
  const dist = Math.hypot(pa.x - pb.x, pa.y - pb.y), dx = Math.abs(pa.x - pb.x), dy = Math.abs(pa.y - pb.y);
  $('measure').textContent = `자  ${a.name} ↔ ${b.name} = ${fmt(dist)}${unitLabel()} (가로 ${fmt(dx)} · 세로 ${fmt(dy)})`;
}

function renderSel() {
  const box = $('sel');
  if (S.mode === 'marker' && S.data && S.piecesData) return renderMarkerPanel();
  if (!S.sel || !S.data) { box.className = 'small muted'; box.textContent = '점이나 선을 누르세요. 점은 끌어 옮기고, 점을 고른 뒤 다른 점을 Shift+클릭하면 거리를 잰다.'; return; }
  if (S.mode === 'marker') return renderMarkerPanel();
  if (S.sel.type === 'piece') return renderPiecePanel();
  box.className = '';
  const b = S.data.blocks.find(x => x.key === S.sel.block);
  if (S.sel.type === 'point') {
    const p = b.points.find(x => x.name === S.sel.name);
    if (!p) { S.sel = null; return renderSel(); }
    const key = `${b.key}.${p.name}`;
    const users = b.lines.filter(l => l.points.includes(p.name)).map(l => `<button class="linkbtn" data-ln="${esc(l.name)}">${esc(l.name)}</button>`).join(' · ') || '—';
    box.innerHTML = `<div class="kv">
      <b>점</b><div><strong>${esc(p.name)}</strong> ${p.ko ? '· ' + esc(p.ko) : ''} <span class="muted">(${esc(b.key)}${p.piece ? ' · ' + esc(p.piece) : ''})</span></div>
      <b>규칙</b><div>${esc(p.rule)}</div>
      ${p.note ? `<b>메모</b><div class="note">${esc(p.note)}</div>` : ''}
      <b>좌표</b><div>x ${fmt(p.x)}  y ${fmt(p.y)} ${unitLabel()} <span class="muted small">(원형 기준)</span></div>
      <b>수정값</b><div>${p.override ? `<span class="tag ov">끌어 옮김</span> 규칙대로면 x ${fmt(p.computed[0])} y ${fmt(p.computed[1])} <button class="linkbtn" id="resetPt">되돌리기</button>` : '없음'}</div>
      <b>지나는 선</b><div>${users}</div></div>` + (S.grading.sizes.length ? `<div style="margin-top:8px"><b class="muted small">사이즈별 편차 (${unitLabel()}) — 치수 재대입 위에 더한다</b>
      <table style="margin-top:3px"><tr><th>사이즈</th><th>가로 +</th><th>세로 +</th></tr>${S.grading.sizes.map(sz => { const r = ((S.grading.rules || {})[sz] || {})[key] || [0, 0]; return `<tr><td><i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${sizeColor(sz)};margin-right:4px"></i>${esc(sz)}</td><td><input data-rule="${esc(sz)}" data-i="0" value="${fmt(r[0])}" style="width:60px"></td><td><input data-rule="${esc(sz)}" data-i="1" value="${fmt(r[1])}" style="width:60px"></td></tr>`; }).join('')}</table></div>` : '');
    $('resetPt')?.addEventListener('click', () => { delete S.pointOverrides[key]; scheduleEval(0); });
    box.querySelectorAll('input[data-rule]').forEach(inp => inp.addEventListener('change', () => {
      const v = parseUnit(inp.value); if (v == null) { inp.style.borderColor = '#c33'; return; }
      const sz = inp.dataset.rule, rules = (S.grading.rules ||= {}), r = ((rules[sz] ||= {})[key] ||= [0, 0]);
      r[+inp.dataset.i] = v;
      if (!r[0] && !r[1]) delete rules[sz][key];
      scheduleEval(0);
    }));
    box.querySelectorAll('[data-ln]').forEach(btn => btn.addEventListener('click', () => select({type: 'line', block: b.key, name: btn.dataset.ln})));
  } else {
    const l = b.lines.find(x => x.name === S.sel.name);
    if (!l) { S.sel = null; return renderSel(); }
    const key = `${b.key}.${l.name}`;
    const ovn = l.overridden ? l.overridden.length : 0;
    box.innerHTML = `<div class="kv">
      <b>선</b><div><strong>${esc(l.name)}</strong> ${l.ko ? '· ' + esc(l.ko) : ''} <span class="muted">(${esc(b.key)}${l.piece ? ' · ' + esc(l.piece) : ''})</span></div>
      <b>역할</b><div>${ROLE_KO[l.role]} · ${l.kind === 'curve' ? '곡선 (베지어 ' + l.beziers.length + '구간)' : '직선'}</div>
      <b>길이</b><div>${fmt(l.length)} ${unitLabel()}</div>
      <b>점</b><div>${l.points.map(n => `<button class="linkbtn" data-pt="${esc(n)}">${esc(n)}</button>`).join(' → ')}</div>
      ${l.kind === 'curve' ? `<b>핸들</b><div>${ovn ? `<span class="tag ov">${ovn}구간 손으로 고침</span> <button class="linkbtn" id="resetLn">되돌리기</button>` : '규칙대로 (파란 점을 끌어 고친다)'}</div>` : ''}</div>`;
    box.querySelectorAll('[data-pt]').forEach(btn => btn.addEventListener('click', () => select({type: 'point', block: b.key, name: btn.dataset.pt})));
    $('resetLn')?.addEventListener('click', () => { delete S.lineOverrides[key]; scheduleEval(0); });
  }
}

// ------------------------------------------------------------ 점 끌기 → 수정값
function startDrag(ev, block, p, off, circle) {
  ev.preventDefault(); ev.stopPropagation();
  if (ev.shiftKey && S.sel && S.sel.type === 'point' && !(S.sel.block === block && S.sel.name === p.name)) {
    S.measure = {a: {block: S.sel.block, name: S.sel.name}, b: {block, name: p.name}};
    draw(); return;
  }
  const key = `${block}.${p.name}`;
  const start = clientToWorld(ev), orig = [p.x, p.y];
  let moved = false;
  cv.classList.add('dragging');
  const move = e => {
    const w = clientToWorld(e);
    if (!moved && Math.hypot(w[0] - start[0], w[1] - start[1]) < 2) return;
    moved = true;
    const nx = orig[0] + (w[0] - start[0]) / K, ny = orig[1] + (w[1] - start[1]) / K;
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

// ------------------------------------------------------------ 곡선 핸들 끌기 → 현 기준 [비율, 각]
function handleRel(p0, p3, c, end) {             // patterncad.block.handle_to_relative 와 같다
  const chord = [p3[0] - p0[0], p3[1] - p0[1]], L = Math.hypot(chord[0], chord[1]) || 1e-9;
  const base = end === 'c1' ? p0 : p3, s = end === 'c1' ? 1 : -1;
  const u = [chord[0] / L * s, chord[1] / L * s], v = [c[0] - base[0], c[1] - base[1]];
  const ang = Math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]) * 180 / Math.PI;
  return [Math.hypot(v[0], v[1]) / L, ang];
}
function startHandleDrag(ev, block, line, seg, end, off) {
  ev.preventDefault(); ev.stopPropagation();
  const key = `${block}.${line.name}`;
  cv.classList.add('dragging');
  let moved = false;
  const move = e => {
    moved = true;
    const w = clientToWorld(e), c = worldToBlock(w[0], w[1], off);
    const bz = line.beziers[seg], p0 = bz[0], p3 = bz[3];
    const lo = (S.lineOverrides[key] ||= {});
    (lo[seg] ||= {})[end] = handleRel(p0, p3, c, end);
    // 이어진 구간의 반대쪽 핸들은 같은 방향을 보게 (접점이 꺾이지 않도록) — 길이는 그대로
    if (end === 'c1' && seg > 0) {
      const prev = line.beziers[seg - 1], joint = p0, oc2 = prev[2];
      const len = Math.hypot(oc2[0] - joint[0], oc2[1] - joint[1]), dv = [c[0] - joint[0], c[1] - joint[1]], dl = Math.hypot(dv[0], dv[1]) || 1e-9;
      const nc2 = [joint[0] - dv[0] / dl * len, joint[1] - dv[1] / dl * len];
      (lo[seg - 1] ||= {}).c2 = handleRel(prev[0], prev[3], nc2, 'c2');
    } else if (end === 'c2' && seg < line.beziers.length - 1) {
      const next = line.beziers[seg + 1], joint = p3, oc1 = next[1];
      const len = Math.hypot(oc1[0] - joint[0], oc1[1] - joint[1]), dv = [c[0] - joint[0], c[1] - joint[1]], dl = Math.hypot(dv[0], dv[1]) || 1e-9;
      const nc1 = [joint[0] - dv[0] / dl * len, joint[1] - dv[1] / dl * len];
      (lo[seg + 1] ||= {}).c1 = handleRel(next[0], next[3], nc1, 'c1');
    }
    scheduleEval(80);
  };
  const up = () => {
    window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
    cv.classList.remove('dragging');
    if (moved) scheduleEval(0);
  };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
}

// ------------------------------------------------------------ 이동·확대·키
let pan = null;
cv.addEventListener('pointerdown', ev => {
  if (ev.target.classList.contains('pt') || ev.target.classList.contains('hdot')) return;
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
window.addEventListener('keydown', ev => {
  if (ev.target.matches('input, select, textarea') || $('wiz').open) return;
  if (ev.key === 'Escape') { S.measure = null; select(null); }
  else if (ev.key === 'f' || ev.key === 'F') fitAll();
  else if (S.mode === 'marker' && S.sel && S.sel.type === 'mk' && (ev.key === 'r' || ev.key === 'R')) { S.marker.items[S.sel.i].rot = (S.marker.items[S.sel.i].rot + 90) % 360; draw(); renderSel(); }
  else if (S.mode === 'marker' && S.sel && S.sel.type === 'mk' && (ev.key === 'm' || ev.key === 'M')) { S.marker.items[S.sel.i].flip = !S.marker.items[S.sel.i].flip; draw(); renderSel(); }
  else if ((ev.key === 'Delete' || ev.key === 'Backspace') && S.sel && S.sel.type === 'mk') { S.marker.items.splice(S.sel.i, 1); S.sel = null; draw(); renderSel(); }
  else if ((ev.key === 'Delete' || ev.key === 'Backspace') && S.sel && S.sel.type !== 'piece') {
    const key = `${S.sel.block}.${S.sel.name}`;
    if (S.sel.type === 'point') delete S.pointOverrides[key]; else delete S.lineOverrides[key];
    scheduleEval(0);
  }
});

// ------------------------------------------------------------ 왼쪽: 조각 나무 · 층 · 원형 이력 · 원본 도면
function renderTree() {
  const t = $('tree'); t.innerHTML = '';
  if (S.mode === 'marker' && S.piecesData) {
    S.marker.items.forEach((it, i) => {
      const pc = piecesFor(it.size).find(p => p.key === it.key); if (!pc) return;
      const e = document.createElement('div'); e.className = (S.sel && S.sel.type === 'mk' && S.sel.i === i ? 'on' : '');
      e.textContent = `${pc.name}${it.size ? ' ' + it.size : ''} ${it.rot ? it.rot + '°' : ''}${it.flip ? ' ↔' : ''}`;
      e.addEventListener('click', () => { S.sel = {type: 'mk', i}; draw(); renderSel(); });
      t.appendChild(e);
    });
    return;
  }
  if (S.mode === 'pieces' && S.piecesData) {
    for (const pc of S.piecesData.pieces) {
      const e = document.createElement('div'); e.className = (S.sel && S.sel.key === pc.key ? 'on' : '');
      e.innerHTML = `${esc(pc.name)} <span class="muted">×${pc.quantity} ${esc(pc.fabric)}${pc.fold ? (pc.unfolded ? ' 펼침' : ' 골') : ''}</span>${pc.warnings.length ? ' <span class="tag" style="color:#c33;border-color:#c33">!</span>' : ''}`;
      e.addEventListener('click', () => { select({type: 'piece', key: pc.key}); fitAll(p => p.key === pc.key); });
      t.appendChild(e);
    }
    return;
  }
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
    e.innerHTML = `<b>${esc(b.key)}</b> ${esc(b.name)}${b.extends ? ` <span class="muted">← ${esc(b.extends)}</span>` : ''}<br><span class="muted">${esc(b.source || '')}</span>`;
    box.appendChild(e);
  }
}

// 원본 도면 겹쳐 보기: 조각마다 켜고 끈다. 켜면 서버가 원형을 도면에 맞춘 변환을 주고, 도면 층 그림을 그 변환으로 놓는다
function renderOverlayList() {
  const box = $('overlays'); box.innerHTML = '';
  for (const b of S.data.blocks) {
    if (!/p\.\d+/.test(b.source || '')) continue;
    for (const pc of b.pieces) {
      const key = `${b.id}|${pc}`, st = S.overlays[key] || {};
      const l = document.createElement('label'); l.className = 'chk';
      const fitInfo = st.fit ? ` <span class="muted">p.${st.fit.page} · 편차 ${fmt(st.fit.err)}${unitLabel()}</span>` : st.loading ? ' <span class="muted">맞추는 중…</span>' : '';
      l.innerHTML = `<input type="checkbox" ${st.on ? 'checked' : ''}> ${esc(b.key)}${pc ? ' · ' + esc(pc) : ''}${fitInfo}`;
      l.querySelector('input').addEventListener('change', e => toggleOverlay(b, pc, e.target.checked));
      box.appendChild(l);
    }
  }
}
async function toggleOverlay(b, pc, on) {
  const key = `${b.id}|${pc}`;
  const st = (S.overlays[key] ||= {});
  st.on = on; st.block = b.key;
  if (on && !st.fit) {
    st.loading = true; renderOverlayList();
    try {
      st.fit = await post('/api/overlay', {block: b.id, piece: pc});
      if (!S.pageCache[st.fit.page + '|' + st.fit.layers])
        S.pageCache[st.fit.page + '|' + st.fit.layers] = await (await fetch(`/api/page?page=${st.fit.page}&layers=${encodeURIComponent(st.fit.layers)}`)).text();
    } catch (e) { showErr(e); st.on = false; }
    st.loading = false;
  }
  renderOverlayList(); draw();
}
function drawOverlays() {
  for (const [key, st] of Object.entries(S.overlays)) {
    if (!st.on || !st.fit) continue;
    const [bid, pc] = key.split('|');
    const b = S.data.blocks.find(x => x.id === bid && x.key === st.block) || S.data.blocks.find(x => x.id === bid);
    if (!b) continue;
    const off = pieceOf(b.key, pc), f = st.fit;
    const piece = S.data.pieces.find(p => p.block === b.key && p.piece === pc);
    const g = el('g', {class: 'ovl'}, world);
    if (piece) {                                   // 조각 둘레 1.5" 만 보이게 잘라 낸다 — 한 장에 다른 조각도 같이 그려져 있다
      const cid = 'clip-' + Object.keys(S.overlays).indexOf(key);   // 이름에 한글이 있어 글자로는 id 를 못 만든다
      const cp = el('clipPath', {id: cid}, g);
      const [x0, y0] = toScreen(piece.bbox[0] - 1.5, piece.bbox[1] - 1.5, off), [x1, y1] = toScreen(piece.bbox[2] + 1.5, piece.bbox[3] + 1.5, off);
      el('rect', {x: x0, y: y0, width: x1 - x0, height: y1 - y0}, cp);
      g.setAttribute('clip-path', `url(#${cid})`);
    }
    // 도면 pt → 원형 inch: 원점 빼고 배율 나누고, 회전 되돌리고, 뒤집었으면 다시 뒤집는다. 그다음 조각 자리로
    const t = [`scale(${K})`, `translate(${off.dx} ${off.dy})`, f.mirror ? 'scale(-1 1)' : '',
               `translate(${f.cx} ${f.cy}) rotate(${-f.rot}) translate(${-f.cx} ${-f.cy})`,
               `scale(${1 / f.sx} ${1 / f.sy})`, `translate(${-f.ox} ${-f.oy})`].join(' ');
    const inner = el('g', {transform: t}, g);
    inner.innerHTML = S.pageCache[f.page + '|' + f.layers] || '';
  }
}

// ------------------------------------------------------------ 오른쪽: 치수
let measTab = null;
function renderMeas() {
  const tabs = $('blockTabs'); tabs.innerHTML = '';
  if (!S.data.blocks.some(b => b.key === measTab)) measTab = S.data.blocks[0].key;
  if (S.data.blocks.length > 1) for (const b of S.data.blocks) {
    const btn = document.createElement('button'); btn.type = 'button'; btn.textContent = b.key; if (b.key === measTab) btn.className = 'on';
    btn.addEventListener('click', () => { measTab = b.key; renderMeas(); }); tabs.appendChild(btn);
  }
  const b = S.data.blocks.find(x => x.key === measTab);
  const box = $('meas');
  const rows = b.measurements.map(m => {
    const key = `${b.key}.${m.name}`;
    const val = typeof m.value === 'number' ? fmt(m.value) : esc(m.value);
    let cell;
    if (m.kind === 'choice' && m.editable) {
      cell = `<select data-key="${esc(key)}" class="${m.modified ? 'mod' : ''}">${m.options.map(o => `<option ${o === m.value ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
    } else if (m.editable) {
      cell = `<input data-key="${esc(key)}" value="${val}" class="${m.modified ? 'mod' : ''}">`;
    } else if (m.linked) {
      cell = `<input value="${val}" class="linked" readonly title="${esc(m.linked)}">`;
    } else {
      cell = `<span class="num">${val}</span>`;
    }
    const sub = m.linked ? `<div class="formula">← ${esc(m.linked)}</div>` : m.formula ? `<div class="formula">= ${esc(m.formula)}</div>` : '';
    const reset = m.modified ? ` <button class="linkbtn" data-reset="${esc(key)}" title="원래 값으로">↺</button>` : '';
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

// ------------------------------------------------------------ 위쪽: 단위 · 초기화 · SVG · 프로젝트 · 고르기
$('units').querySelectorAll('button').forEach(btn => btn.addEventListener('click', () => {
  S.unit = btn.dataset.u;
  $('units').querySelectorAll('button').forEach(b => b.classList.toggle('on', b === btn));
  renderMeas(); renderSel(); renderOverlayList(); drawMeasure();
}));
$('modes').querySelectorAll('button').forEach(btn => btn.addEventListener('click', async () => {
  S.mode = btn.dataset.m; S.sel = null;
  $('modes').querySelectorAll('button').forEach(b => b.classList.toggle('on', b === btn));
  await evaluate(); fitAll();
}));
$('resetAll').addEventListener('click', () => { S.overrides = {}; S.pointOverrides = {}; S.lineOverrides = {}; S.pieceSettings = {}; S.grading.sizes = []; S.marker.items = []; S.compose = {}; scheduleEval(0); });
$('saveHpgl').addEventListener('click', () => download(S.mode === 'marker' ? '/api/marker_dxf' : '/api/hpgl', S.mode === 'marker' ? {...payload(), placements: S.marker.items, width: S.marker.width, format: 'hpgl'} : payload(), `${S.projName || S.id}${S.mode === 'marker' ? '_marker' : ''}.plt`));
$('saveDxf').addEventListener('click', async () => {
  try {
    const r = await post('/api/dxf', payload());
    const blob = await r.blob(), a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `${S.projName || S.id}.dxf`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) { showErr(e); }
});
$('saveSvg').addEventListener('click', async () => {
  try {
    const r = await post('/api/svg', payload());
    const blob = await r.blob(), a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `${S.projName || S.id}.svg`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) { showErr(e); }
});
function open_(kind, id, {overrides = {}, pointOverrides = {}, lineOverrides = {}, pieceSettings = {}, grading = null, marker = null, compose = {}, projName = '', view = null} = {}) {
  S.compose = compose || {};
  S.marker = marker && marker.items ? marker : {width: S.marker.width, gap: S.marker.gap, items: []};
  S.kind = kind; S.id = id; S.overrides = overrides; S.pointOverrides = pointOverrides; S.lineOverrides = lineOverrides; S.pieceSettings = pieceSettings;
  S.grading = grading && grading.system ? grading : {system: S.grading.system, base: S.grading.base, sizes: []};
  S.sel = null; S.measure = null; S.overlays = {}; measTab = null; S.projName = projName;
  $('picker').value = `${kind}:${id}`;
  $('proj').textContent = projName ? `프로젝트 ${projName}` : '';
  location.hash = `${kind}/${id}`;
  return evaluate().then(() => { if (view && view.k) { S.k = view.k; S.px = view.px; S.py = view.py; applyView(); } else fitAll(); });
}
$('picker').addEventListener('change', () => { const [kind, id] = $('picker').value.split(':'); open_(kind, id); });

async function loadProjects() {
  const sel = $('projects'); const cur = sel.value;
  sel.innerHTML = '<option value="">열기…</option>';
  for (const p of await getJson('/api/projects')) {
    const o = document.createElement('option'); o.value = p.name; o.textContent = `${p.name}  (${p.id}, ${p.saved.slice(0, 16).replace('T', ' ')})`; sel.appendChild(o);
  }
  sel.value = cur;
}
$('projects').addEventListener('change', async () => {
  const name = $('projects').value; if (!name) return;
  try {
    const p = await getJson(`/api/project?name=${encodeURIComponent(name)}`);
    await open_(p.kind, p.id, {overrides: p.overrides || {}, pointOverrides: p.point_overrides || {}, lineOverrides: p.line_overrides || {}, pieceSettings: p.piece_settings || {}, grading: p.grading, marker: p.marker, compose: p.compose || {}, projName: p.name, view: p.view});
  } catch (e) { showErr(e); }
});
$('saveProj').addEventListener('click', async () => {
  const name = prompt('프로젝트 이름', S.projName || S.id);
  if (!name) return;
  try {
    const p = await post('/api/project', {name, ...payload(), view: {k: S.k, px: S.px, py: S.py}});
    S.projName = p.name; $('proj').textContent = `프로젝트 ${p.name} · 저장 ${p.saved.slice(11, 16)}`;
    await loadProjects(); $('projects').value = p.name;
  } catch (e) { showErr(e); }
});

// ------------------------------------------------------------ 새 패턴 마법사
const W = {cat: null, item: null, fit: '레귤러', data: null, opts: null, optStyle: null, compose: {}};
$('newPat').addEventListener('click', async () => {
  try {
    if (!W.data) W.data = await getJson('/api/wizard');
    W.cat = W.cat || W.data.categories[0].id; W.item = null;
    renderWizard(); $('wiz').showModal();
  } catch (e) { showErr(e); }
});
function renderWizard() {
  const cats = $('wizCats'); cats.innerHTML = '';
  for (const c of W.data.categories) {
    const d = document.createElement('div'); d.className = 'opt' + (c.id === W.cat ? ' on' : ''); d.textContent = `${c.ko} (${c.items.length})`;
    d.addEventListener('click', () => { W.cat = c.id; W.item = null; renderWizard(); }); cats.appendChild(d);
  }
  const cat = W.data.categories.find(c => c.id === W.cat);
  const items = $('wizItems'); items.innerHTML = '';
  for (const it of cat.items) {
    const d = document.createElement('div'); d.className = 'opt' + (W.item === it.style ? ' on' : '');
    d.innerHTML = `${esc(it.name)}<br><span class="muted">${it.item !== it.name ? esc(it.item) + ' · ' : ''}p.${it.pages.join(', ')}</span>`;
    d.addEventListener('click', () => { W.item = it.style; renderWizard(); }); items.appendChild(d);
  }
  const it = cat.items.find(x => x.style === W.item);
  const sz = $('wizSize');
  if (!it) { sz.innerHTML = '<div class="muted small">아이템을 고르면 그 아이템의 포트폴리오 사이즈표가 나온다</div>'; }
  else {
    sz.innerHTML = `<table><tr><th>치수</th><th>신체 (${unitLabel()})</th><th>포트폴리오 패턴</th></tr>` +
      it.size.map(m => `<tr><td>${esc(m.name)}</td><td class="num"><input data-name="${esc(m.name)}" value="${m.body == null ? '' : fmt(m.body)}" placeholder="—"></td><td class="num muted">${m.pattern == null ? '' : fmt(m.pattern)}</td></tr>`).join('') + '</table>';
  }
  const fit = $('wizFit'); fit.innerHTML = '';
  for (const lv of Object.keys(W.data.fit_levels)) {
    const l = document.createElement('label');
    l.innerHTML = `<input type="radio" name="fit" value="${esc(lv)}" ${lv === W.fit ? 'checked' : ''}> ${esc(lv)}`;
    l.querySelector('input').addEventListener('change', () => { W.fit = lv; });
    fit.appendChild(l);
  }
  $('wizMsg').textContent = it ? `${it.name} — 신체 치수는 원형의 같은 이름 치수(가슴둘레·허리둘레·엉덩이둘레…)에 들어간다` : '';
  const ob = $('wizOpts'); ob.innerHTML = '';
  if (it) {
    if (W.optStyle !== it.style) { W.optStyle = it.style; W.compose = {}; W.opts = null; }
    if (!W.opts) post('/api/options', {id: it.style}).then(o => { W.opts = o; renderWizard(); }).catch(showErr);
    else ob.appendChild(optionRows(W.opts.slots, W.compose, (slot, id) => { W.compose = {...W.compose, [slot]: id}; renderWizard(); }));
  }
}
$('wiz').addEventListener('close', async () => {
  const v = $('wiz').returnValue;
  if (v !== 'draw' && v !== 'blank') return;
  if (!W.item) return;
  try {
    let overrides = {};
    if (v === 'draw') {
      const body = {};
      $('wizSize').querySelectorAll('input[data-name]').forEach(inp => { const n = parseUnit(inp.value); if (n != null) body[inp.dataset.name] = n; });
      overrides = (await post('/api/wizard', {style: W.item, body, fit: W.fit})).overrides;
    }
    await open_('style', W.item, {overrides, compose: W.compose});
  } catch (e) { showErr(e); }
});

// ------------------------------------------------------------ 시작
async function init() {
  const cat = await getJson('/api/catalog');
  const pk = $('picker');
  const grp = (label, kind, items) => {
    const g = document.createElement('optgroup'); g.label = label;
    for (const it of items) { const o = document.createElement('option'); o.value = `${kind}:${it.id}`; o.textContent = `${it.name}  (${it.id})`; g.appendChild(o); }
    pk.appendChild(g);
  };
  grp('스타일', 'style', cat.styles); grp('원형', 'block', cat.blocks);
  // 주소: #style/아이디@블록.점이름  뒤에 ?ovl=블록id|조각,… 를 붙이면 그 도면을 겹쳐 놓고 연다 (공유·확인용)
  const [hmain, hquery] = decodeURIComponent(location.hash.slice(1)).split('?');   // 크롬은 한글을 %EC… 로 적는다
  const [hpath, hsel] = hmain.split('@');
  const h = hpath.split('/');
  const hq = Object.fromEntries((hquery || '').split('&').filter(Boolean).map(kv => kv.split('=').map(decodeURIComponent)));
  if (h.length === 2 && [...pk.options].some(o => o.value === `${h[0]}:${h[1]}`)) { S.kind = h[0]; S.id = h[1]; }
  else { S.kind = 'style'; S.id = cat.styles.find(s => s.id === 'shirt_collar_blouse') ? 'shirt_collar_blouse' : cat.styles[0].id; }
  pk.value = `${S.kind}:${S.id}`;
  renderLayers();
  loadProjects().catch(showErr);
  await evaluate();
  fitAll();
  if (hsel && hsel.includes('.')) {
    const i = hsel.indexOf('.'), blk = hsel.slice(0, i), name = hsel.slice(i + 1);
    const b = S.data.blocks.find(x => x.key === blk);
    if (b) select({type: b.lines.some(l => l.name === name) ? 'line' : 'point', block: blk, name});
  }
  if (hq.compose) { S.compose = Object.fromEntries(hq.compose.split(',').map(kv => kv.split(':'))); await evaluate(); fitAll(); }   // ?compose=collar:stand,sleeve:two_piece
  if (hq.mode === 'pieces' || hq.mode === 'marker') $('modes').querySelector(`[data-m=${hq.mode}]`).click();
  if (hq.sizes) { await renderGrading(); S.grading.sizes = hq.sizes.split(','); await evaluate(); }
  if (hq.wiz) $('newPat').click();
  for (const key of (hq.ovl || '').split(',').filter(Boolean)) {
    const [bid, pc] = key.split('|'), b = S.data.blocks.find(x => x.id === bid);
    if (b) await toggleOverlay(b, pc || '', true);
  }
}
window.addEventListener('resize', () => applyView());
init();

import os
import json
import math
import re
import time
import threading

import requests
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
GDBROWSER_BASE = os.environ.get("GDBROWSER_BASE", "https://gdbrowser.com/api")
GDDL_BASE = os.environ.get("GDDL_BASE", "https://gdladder.com/api")
GDDL_API_KEY = os.environ.get("GDDL_API_KEY", "")
AREDL_LIST_URL = os.environ.get("AREDL_LIST_URL", "https://api.aredl.net/v2/api/aredl/levels")

HEADERS = {"User-Agent": "Mozilla/5.0 (GD Difficulty Calculator)", "Accept": "application/json"}
TIMEOUT = 8
MAX_RESULTS = 25

http = requests.Session()
http.headers.update(HEADERS)


# ----------------------------------------------------------------------------
# Formula
# ----------------------------------------------------------------------------
def calculate_score(s, g, a):
    base = 1.3 ** (g + s - 1)
    list_bonus = (10 / 9) ** (149 - (a - 1) / 7) if a > 0 else 1.0
    return round(base + list_bonus - 2, 2)


def score_parts(s, g, a):
    base = 1.3 ** (g + s - 1)
    list_bonus = (10 / 9) ** (149 - (a - 1) / 7) if a > 0 else 1.0
    return base, list_bonus


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def get_json(url, **kwargs):
    try:
        r = http.get(url, timeout=TIMEOUT, **kwargs)
        if r.status_code != 200:
            return None
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalize_gdbrowser_level(item):
    if not isinstance(item, dict):
        return None
    lvl_id = to_int(item.get("id"))
    if not lvl_id:
        return None
    return {
        "id": lvl_id,
        "name": str(item.get("name") or "Unknown"),
        "author": str(item.get("author") or "Unknown"),
        "stars": to_int(item.get("stars")),
        "downloads": to_int(item.get("downloads")),
    }


def fetch_level(level_id):
    data = get_json(f"{GDBROWSER_BASE}/level/{level_id}")
    return normalize_gdbrowser_level(data)


# --- GDDL tier -------------------------------------------------------------
GDDL_SITE = os.environ.get("GDDL_SITE", "https://gdladder.com")


def _tier_from_json(data):
    if not isinstance(data, dict):
        return None
    for key in ("level", "data"):
        if isinstance(data.get(key), dict):
            data = data[key]
            break
    for key in ("Rating", "rating", "Tier", "tier"):
        if data.get(key) is not None:
            return to_float(data.get(key), None)
    return None


def _tier_from_html(html):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"Tier\s*(\d+)\s*\(\s*(\d+(?:\.\d+)?)\s*\)", text)
    if m:
        return float(m.group(2))
    m = re.search(r'content="Tier\s*(\d+(?:\.\d+)?)\s*[\u00b7|]', html)
    if m:
        return float(m.group(1))
    return None


def gddl_candidates(level_id):
    return [
        ("json", f"{GDDL_BASE}/level/{level_id}"),
        ("json", f"{GDDL_SITE}/api/level/{level_id}"),
        ("json", f"{GDDL_SITE}/api/levels/{level_id}"),
        ("html", f"{GDDL_SITE}/level/{level_id}"),
    ]


def fetch_gddl_tier(level_id, debug=None):
    headers = {}
    if GDDL_API_KEY:
        headers["Authorization"] = f"Bearer {GDDL_API_KEY}"
    for attempt in range(2):
        loaded = False
        for kind, url in gddl_candidates(level_id):
            try:
                r = http.get(url, headers=headers, timeout=TIMEOUT)
            except requests.RequestException as e:
                if debug is not None:
                    debug.append({"url": url, "error": str(e)})
                continue
            tier = None
            if r.status_code == 200:
                if kind == "json":
                    try:
                        data = r.json()
                        loaded = loaded or isinstance(data, dict)
                        tier = _tier_from_json(data)
                    except ValueError:
                        tier = None
                else:
                    loaded = True
                    tier = _tier_from_html(r.text)
            if debug is not None:
                debug.append({"url": url, "status": r.status_code,
                              "content_type": r.headers.get("content-type"),
                              "tier": tier, "preview": r.text[:200]})
            if tier is not None:
                return tier
        if loaded:
            return 0.0
        time.sleep(0.7)
    return None


# --- AREDL position --------------------------------------------------------
_aredl_lock = threading.Lock()
_aredl_cache = {"ts": 0.0, "by_id": {}}
AREDL_TTL = 3600


def get_aredl_index():
    with _aredl_lock:
        if _aredl_cache["by_id"] and time.time() - _aredl_cache["ts"] < AREDL_TTL:
            return _aredl_cache["by_id"]

    data = get_json(AREDL_LIST_URL)
    if isinstance(data, dict):
        data = data.get("data") or data.get("levels") or []
    by_id = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            gd_id = to_int(item.get("level_id"))
            pos = to_int(item.get("position"))
            if gd_id and pos:
                by_id[gd_id] = pos

    with _aredl_lock:
        if by_id:
            _aredl_cache["by_id"] = by_id
            _aredl_cache["ts"] = time.time()
        return _aredl_cache["by_id"]


def fetch_aredl_rank(level_id):
    return get_aredl_index().get(level_id, 0)


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@app.route("/api/search")
def search_levels():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"exact": [], "contains": []})

    levels = []
    seen = set()

    if re.fullmatch(r"\d{1,10}", query):
        lvl = fetch_level(int(query))
        if lvl:
            seen.add(lvl["id"])
            levels.append(lvl)

    data = get_json(f"{GDBROWSER_BASE}/search/{requests.utils.quote(query, safe='')}",
                    params={"count": MAX_RESULTS})
    if isinstance(data, list):
        for item in data:
            lvl = normalize_gdbrowser_level(item)
            if lvl and lvl["id"] not in seen:
                seen.add(lvl["id"])
                levels.append(lvl)

    q = query.lower()
    exact, contains = [], []
    for lvl in levels:
        if lvl["stars"] <= 0:
            continue
        n = lvl["name"].lower()
        if n == q or str(lvl["id"]) == q:
            exact.append(lvl)
        elif q in n:
            contains.append(lvl)

    return jsonify({"exact": exact[:MAX_RESULTS], "contains": contains[:MAX_RESULTS]})


@app.route("/api/score/<int:level_id>")
def get_level_score(level_id):
    lvl = fetch_level(level_id)
    if not lvl:
        return jsonify({"error": "Level not found (or the GD data source is unreachable)."}), 404
    if lvl["stars"] <= 0:
        return jsonify({"error": "Level is not rated."}), 400

    stars = lvl["stars"]
    gddl_tier = 0.0
    gddl_failed = False
    aredl_rank = 0

    if stars == 10:
        gddl_tier = fetch_gddl_tier(level_id)
        gddl_failed = gddl_tier is None
        if gddl_failed:
            gddl_tier = 0.0
        aredl_rank = fetch_aredl_rank(level_id)

    score = calculate_score(stars, gddl_tier, aredl_rank)
    base, list_bonus = score_parts(stars, gddl_tier, aredl_rank)

    return jsonify({
        "base": round(base, 2),
        "list_bonus": round(list_bonus, 2),
        "name": lvl["name"],
        "author": lvl["author"],
        "id": level_id,
        "stars": stars,
        "gddl_tier": round(gddl_tier, 2),
        "gddl_failed": gddl_failed,
        "aredl_rank": aredl_rank,
        "score": score,
    })


@app.route("/api/calc")
def calc():
    s = min(max(to_int(request.args.get("s"), 1), 1), 10)
    g = min(max(to_float(request.args.get("g"), 0.0), 0.0), 39.0)
    a = min(max(to_int(request.args.get("a"), 0), 0), 1000)
    base, list_bonus = score_parts(s, g, a)
    return jsonify({
        "score": calculate_score(s, g, a),
        "base": round(base, 2),
        "list_bonus": round(list_bonus, 2),
    })


@app.route("/api/curve")
def curve():
    s = min(max(to_int(request.args.get("s"), 10), 1), 10)
    g = min(max(to_float(request.args.get("g"), 0.0), 0.0), 39.0)
    a = min(max(to_int(request.args.get("a"), 0), 0), 1000)
    if request.args.get("mode") == "rank":
        xs = [1] + list(range(5, 501, 5))
        pts = [{"x": x, "y": calculate_score(s, g, x)} for x in xs]
    else:
        xs = [i / 2 for i in range(0, 79)]
        pts = [{"x": x, "y": calculate_score(s, x, a)} for x in xs]
    return jsonify({"points": pts})


@app.route("/")
@app.route("/api/index")
def index():
    return render_template_string(HTML_TEMPLATE)


HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GD Difficulty Score</title>
<style>
:root { --bg:#0f1115; --panel:#181b22; --panel2:#20242d; --line:#2b303b; --text:#eef0f4; --muted:#9aa3b2; --accent:#4cd07d; --accent2:#f5a524; --danger:#ff5d5d; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font-family:system-ui,-apple-system,Segoe UI,sans-serif; line-height:1.45; }
.wrap { max-width:760px; margin:0 auto; padding:28px 18px 60px; }
header h1 { margin:0; font-size:1.6rem; letter-spacing:-.3px; }
header p { margin:4px 0 18px; color:var(--muted); font-size:.95rem; }
nav { display:flex; gap:6px; border-bottom:1px solid var(--line); margin-bottom:20px; }
nav button { background:none; color:var(--muted); border:none; border-bottom:2px solid transparent; padding:10px 14px; font-size:.95rem; font-weight:600; cursor:pointer; border-radius:0; }
nav button:hover { color:var(--text); }
nav button.active { color:var(--accent); border-bottom-color:var(--accent); }
.card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:18px; margin-bottom:16px; }
input[type=text], input[type=number] { background:var(--panel2); border:1px solid var(--line); color:var(--text); border-radius:8px; padding:11px 12px; font-size:15px; outline:none; }
input:focus { border-color:var(--accent); }
button { background:var(--accent); color:#06150c; border:none; border-radius:8px; padding:11px 16px; font-weight:700; cursor:pointer; font-size:14px; }
button:hover { filter:brightness(1.08); }
button.ghost { background:var(--panel2); color:var(--text); border:1px solid var(--line); }
button.small { padding:6px 10px; font-size:12.5px; }
.row { display:flex; gap:10px; }
.row input { flex:1; min-width:0; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; align-items:center; font-size:.82rem; color:var(--muted); }
.chip { background:var(--panel2); border:1px solid var(--line); color:var(--text); border-radius:999px; padding:3px 10px; cursor:pointer; font-size:.82rem; }
.chip:hover { border-color:var(--accent); }
.label { font-size:.75rem; text-transform:uppercase; letter-spacing:1px; color:var(--muted); font-weight:700; margin:18px 0 8px; }
.level { background:var(--panel2); border:1px solid transparent; border-radius:10px; padding:11px 14px; display:flex; justify-content:space-between; align-items:center; cursor:pointer; margin-bottom:8px; gap:10px; }
.level:hover { border-color:var(--accent); }
.level.exact { border-left:3px solid var(--accent); }
.meta { color:var(--muted); font-size:.85rem; }
.badge { background:var(--line); border-radius:6px; padding:3px 8px; font-size:.82rem; font-weight:700; white-space:nowrap; }
.msg { color:var(--muted); padding:10px 0; } .msg.err { color:var(--danger); }
.score-title { font-size:1.15rem; font-weight:700; }
.score-num { font-size:2.6rem; font-weight:800; color:var(--accent); margin:6px 0 2px; word-break:break-all; letter-spacing:-1px; }
.stats { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin:14px 0; }
.stat { background:var(--panel2); border-radius:8px; padding:10px; }
.stat b { display:block; font-size:1.05rem; } .stat span { color:var(--muted); font-size:.75rem; text-transform:uppercase; letter-spacing:.5px; }
.bar { display:flex; height:12px; border-radius:6px; overflow:hidden; background:var(--line); margin:8px 0 6px; }
.bar i { display:block; height:100%; } .b1 { background:var(--accent); } .b2 { background:var(--accent2); }
.legend { display:flex; gap:16px; flex-wrap:wrap; font-size:.82rem; color:var(--muted); }
.dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:5px; }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }
table { width:100%; border-collapse:collapse; font-size:.9rem; }
th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-size:.74rem; text-transform:uppercase; letter-spacing:.8px; }
td.num { text-align:right; font-variant-numeric:tabular-nums; } th.num { text-align:right; }
.slider { margin:14px 0; }
.slider .top { display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; font-weight:600; }
.slider input[type=range] { width:100%; accent-color:var(--accent); }
.slider input[type=number] { width:90px; padding:6px 8px; }
.formula { background:var(--panel2); border-radius:10px; padding:14px; font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.92rem; overflow-x:auto; line-height:1.8; }
.formula .t1 { color:var(--accent); } .formula .t2 { color:var(--accent2); }
h3 { margin:22px 0 6px; font-size:1.05rem; } p { margin:6px 0; } li { margin:4px 0; }
.cmpgrid { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }
.cmpgrid .row input { flex:1; }
.cmpgrid .stat { margin-top:10px; } .cmpgrid .stat b { font-size:1rem; }
.verdict { font-size:1.2rem; font-weight:800; color:var(--accent); margin:6px 0 2px; }
.logbar { height:14px; border-radius:7px; background:var(--line); margin:4px 0 12px; overflow:hidden; } .logbar i { display:block; height:100%; }
.chartbox svg { width:100%; height:auto; display:block; }
.chart-cap { color:var(--muted); font-size:.82rem; margin:6px 0 0; }
.scale-toggle { display:flex; gap:4px; background:var(--panel2); padding:3px; border-radius:8px; border:1px solid var(--line); }
.scale-toggle button { background:none; color:var(--muted); padding:4px 10px; font-size:12px; border-radius:6px; }
.scale-toggle button.active { background:var(--accent); color:#06150c; font-weight:700; }
.hidden { display:none; }
@media (max-width:520px) { .cmpgrid { grid-template-columns:1fr; } .stats { grid-template-columns:1fr 1fr; } .score-num { font-size:2.1rem; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>GD Difficulty Score</h1>
    <p>One number for how hard a Geometry Dash level is, built from stars, GDDL tier and AREDL rank.</p>
  </header>
  <nav>
    <button class="active" data-tab="cmp">Compare</button>
    <button data-tab="calc">Calculator</button>
    <button data-tab="play">Playground</button>
    <button data-tab="how">How it works</button>
  </nav>

  <!-- COMPARE -->
  <section id="tab-cmp">
    <div class="card">
      <div class="score-title">Compare levels</div>
      <p class="meta">Add as many levels as you want. They're ranked by score and plotted on the graph below.</p>
      <div class="row" style="margin-top:12px"><input type="text" id="cq" placeholder="Name or ID…" autocomplete="off"><button id="cb">Search</button></div>
      <div id="cr"></div>
    </div>
    <div id="cmpResult"></div>
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
        <div class="score-title">Score vs GDDL tier</div>
        <div class="scale-toggle">
          <button id="btnLog" class="active" onclick="setScale('log')">Log scale</button>
          <button id="btnLin" onclick="setScale('linear')">Linear scale</button>
        </div>
      </div>
      <div class="chartbox" id="cmpChart" style="margin-top:12px"></div>
      <div class="legend" id="cmpLegend" style="margin-top:8px"></div>
      <p class="chart-cap">The curve follows the standard baseline up to tier 30, and your Desmos logistic curve model from tier 30 onwards.</p>
    </div>
  </section>

  <!-- CALCULATOR -->
  <section id="tab-calc" class="hidden">
    <div class="card">
      <div class="row">
        <input type="text" id="searchInput" placeholder="Search a level name or ID…" autocomplete="off">
        <button id="searchBtn">Search</button>
      </div>
      <div class="chips" id="recent"></div>
      <div id="resultsArea"></div>
    </div>
    <div id="scoreCard" class="card hidden"></div>
  </section>

  <!-- PLAYGROUND -->
  <section id="tab-play" class="hidden">
    <div class="card">
      <div class="score-title">Score playground</div>
      <p class="meta">Move the sliders to see how each input changes the score. Same formula as the calculator.</p>
      <div class="slider"><div class="top"><span>Stars</span><input type="number" id="pS" min="1" max="10" step="1" value="10"></div><input type="range" id="rS" min="1" max="10" step="1" value="10"></div>
      <div class="slider"><div class="top"><span>GDDL tier</span><input type="number" id="pG" min="0" max="39" step="0.01" value="10"></div><input type="range" id="rG" min="0" max="39" step="0.01" value="10"></div>
      <div class="slider"><div class="top"><span>AREDL rank <span class="meta">(0 = unranked)</span></span><input type="number" id="pA" min="0" max="500" step="1" value="0"></div><input type="range" id="rA" min="0" max="500" step="1" value="0"></div>
      <div class="score-num" id="pScore">–</div>
      <div id="pBreak"></div>
    </div>
  </section>

  <!-- HOW IT WORKS -->
  <section id="tab-how" class="hidden">
    <div class="card">
      <div class="score-title">The formula</div>
      <div class="formula">
        score = <span class="t1">1.3<sup>(tier + stars − 1)</sup></span> + <span class="t2">(10/9)<sup>(149 − (rank − 1) / 7)</sup></span> − 2
      </div>
      <h3><span class="dot b1" style="background:var(--accent)"></span>Base term</h3>
      <p>Stars and GDDL tier are added together inside one exponent, so each extra star <em>or</em> tier multiplies the base term by 1.3. A 10★ demon starts at 1.3<sup>9</sup> ≈ 10.6 before its tier is even counted, and every tier above 0 multiplies that again.</p>
      <h3><span class="dot b2" style="background:var(--accent2)"></span>List term</h3>
      <p>If the level is on the AREDL (the Extreme Demon List), its position feeds a second exponential. Rank #1 gives (10/9)<sup>149</sup> ≈ 6.5 million, and every 7 places down divides the term by 10/9. Unranked levels use a fixed 1.0.</p>
      <h3>The −2 offset</h3>
      <p>Subtracting 2 removes the two baseline "1" values, so a non-demon with no tier and no rank scores 1.3<sup>(stars−1)</sup> − 1.</p>
      <h3>Where the data comes from</h3>
      <ul>
        <li><b>Stars:</b> the Geometry Dash servers, via GDBrowser.</li>
        <li><b>Tier:</b> community-voted GDDL rating (0 if the level isn't on GDDL). Only 10★ demons look it up.</li>
        <li><b>Rank:</b> current AREDL position (0 if unranked). Only 10★ demons look it up.</li>
      </ul>
      <h3>What the list term looks like</h3>
      <table><thead><tr><th>AREDL rank</th><th class="num">List term</th></tr></thead><tbody id="rankTable"><tr><td colspan="2" class="meta">Loading…</td></tr></tbody></table>
      <div class="actions"><button class="ghost" id="toPlay">Try it in the playground</button></div>
    </div>
  </section>
</div>

<script>
function el(tag, props, kids) {
  const e = document.createElement(tag);
  for (const k in (props || {})) {
    if (k === 'class') e.className = props[k];
    else if (k === 'text') e.textContent = props[k];
    else e[k] = props[k];
  }
  (kids || []).forEach(c => e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
  return e;
}
const $ = id => document.getElementById(id);
const fmt = n => Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
function load(key) { try { return JSON.parse(localStorage.getItem(key)) || []; } catch (e) { return []; } }
function save(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) {} }

/* ---------- tabs ---------- */
function showTab(name) {
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  ['calc', 'cmp', 'play', 'how'].forEach(t => $('tab-' + t).classList.toggle('hidden', t !== name));
  if (name === 'how') loadRankTable();
  if (name === 'play') runPlay();
}
document.querySelectorAll('nav button').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
$('toPlay').addEventListener('click', () => showTab('play'));

/* ---------- breakdown widget ---------- */
function breakdown(base, listBonus) {
  const total = base + listBonus;
  const p1 = total > 0 ? (base / total) * 100 : 50;
  return el('div', null, [
    el('div', { class: 'bar' }, [
      el('i', { class: 'b1', style: 'width:' + p1 + '%' }),
      el('i', { class: 'b2', style: 'width:' + (100 - p1) + '%' })
    ]),
    el('div', { class: 'legend' }, [
      el('span', null, [el('span', { class: 'dot b1' }), 'Base ' + fmt(base) + ' (' + p1.toFixed(1) + '%)']),
      el('span', null, [el('span', { class: 'dot b2' }), 'List ' + fmt(listBonus) + ' (' + (100 - p1).toFixed(1) + '%)']),
      el('span', { text: 'Offset −2' })
    ])
  ]);
}

/* ---------- recent searches ---------- */
function renderRecent() {
  const box = $('recent'); box.replaceChildren();
  const r = load('gdRecent');
  if (!r.length) return;
  box.appendChild(document.createTextNode('Recent:'));
  r.forEach(q => {
    const c = el('span', { class: 'chip', text: q });
    c.addEventListener('click', () => { $('searchInput').value = q; searchLevels(); });
    box.appendChild(c);
  });
}
function pushRecent(q) {
  const r = load('gdRecent').filter(x => x.toLowerCase() !== q.toLowerCase());
  r.unshift(q); save('gdRecent', r.slice(0, 6)); renderRecent();
}

/* ---------- search ---------- */
function setArea(node) { $('resultsArea').replaceChildren(node); }
function msg(text, err) { return el('div', { class: 'msg' + (err ? ' err' : ''), text }); }
function levelRow(l, exact, onClick) {
  const row = el('div', { class: 'level' + (exact ? ' exact' : '') }, [
    el('div', null, [el('div', { text: l.name, style: 'font-weight:700' }), el('div', { class: 'meta', text: 'by ' + l.author + ' • ID ' + l.id })]),
    el('div', { class: 'badge', text: l.stars + '★' })
  ]);
  row.addEventListener('click', onClick || (() => selectLevel(l.id)));
  return row;
}
function group(title, list, exact) {
  const f = document.createDocumentFragment();
  f.appendChild(el('div', { class: 'label', text: title }));
  list.forEach(l => f.appendChild(levelRow(l, exact)));
  return f;
}
async function searchLevels() {
  const q = $('searchInput').value.trim();
  if (!q) return;
  $('scoreCard').classList.add('hidden');
  setArea(msg('Searching…'));
  try {
    const d = await (await fetch('/api/search?q=' + encodeURIComponent(q))).json();
    if (!d.exact.length && !d.contains.length) { setArea(msg('No rated levels found for "' + q + '".', true)); return; }
    pushRecent(q);
    const f = document.createDocumentFragment();
    if (d.exact.length) f.appendChild(group('Exact matches', d.exact, true));
    if (d.contains.length) f.appendChild(group('Containing "' + q + '"', d.contains, false));
    setArea(f);
  } catch (e) { setArea(msg('Could not reach the server.', true)); }
}

/* ---------- score card ---------- */
function showCard(nodes) { const c = $('scoreCard'); c.classList.remove('hidden'); c.replaceChildren(...nodes); }
async function selectLevel(id) {
  showCard([msg('Calculating…')]);
  try {
    const d = await (await fetch('/api/score/' + id)).json();
    if (d.error) { showCard([msg(d.error, true)]); return; }
    const stat = (label, val) => el('div', { class: 'stat' }, [el('b', { text: val }), el('span', { text: label })]);
    const add = el('button', { text: 'Compare…' });
    add.addEventListener('click', () => sendToCompare(d));
    const play = el('button', { class: 'ghost', text: 'Open in playground' });
    play.addEventListener('click', () => { setPlay(d.stars, d.gddl_tier, d.aredl_rank); showTab('play'); });
    const copy = el('button', { class: 'ghost', text: 'Copy score' });
    copy.addEventListener('click', () => { navigator.clipboard && navigator.clipboard.writeText(String(d.score)); copy.textContent = 'Copied!'; setTimeout(() => copy.textContent = 'Copy score', 1200); });
    showCard([
      el('div', { class: 'score-title' }, [d.name + ' ', el('span', { class: 'meta', text: 'by ' + d.author + ' • ID ' + d.id })]),
      el('div', { class: 'score-num', text: fmt(d.score) }),
      el('div', { class: 'stats' }, [stat('Stars', d.stars + '★'), stat('GDDL tier', d.gddl_failed ? 'Lookup failed' : (d.gddl_tier || 'N/A')), stat('AREDL rank', d.aredl_rank ? '#' + d.aredl_rank : 'Unranked')]),
      breakdown(d.base, d.list_bonus),
      el('div', { class: 'actions' }, [add, play, copy])
    ]);
  } catch (e) { showCard([msg('Failed to calculate score.', true)]); }
}

/* ---------- compare (any number of levels) ---------- */
const cmp = [];
const PALETTE = ['#ffffff', '#7cc7ff', '#ff8fd0', '#ffd166', '#9be564', '#ff9f6b', '#b8a1ff', '#5eead4', '#f87171', '#c4c4c4'];
function sendToCompare(d) { addToCompare(d); showTab('cmp'); }
function addToCompare(d) {
  if (cmp.some(x => x.id === d.id)) return;
  cmp.push(Object.assign({}, d));
  renderCompare();
}
async function cmpSearch() {
  const q = $('cq').value.trim(); if (!q) return;
  const box = $('cr'); box.replaceChildren(msg('Searching…'));
  try {
    const d = await (await fetch('/api/search?q=' + encodeURIComponent(q))).json();
    const all = d.exact.concat(d.contains).slice(0, 6);
    if (!all.length) { box.replaceChildren(msg('No rated levels found.', true)); return; }
    const f = document.createDocumentFragment();
    all.forEach(l => f.appendChild(levelRow(l, false, () => cmpAdd(l.id))));
    box.replaceChildren(f);
  } catch (e) { box.replaceChildren(msg('Could not reach the server.', true)); }
}
async function cmpAdd(id) {
  $('cr').replaceChildren(msg('Adding…'));
  try {
    const d = await (await fetch('/api/score/' + id)).json();
    if (d.error) { $('cr').replaceChildren(msg(d.error, true)); return; }
    addToCompare(d); $('cr').replaceChildren(); $('cq').value = ''; $('cq').focus();
  } catch (e) { $('cr').replaceChildren(msg('Failed to load level.', true)); }
}
function renderCompare() {
  cmp.forEach((x, i) => { x.color = PALETTE[i % PALETTE.length]; });
  drawCmpChart();
  const box = $('cmpResult'); box.replaceChildren();
  if (!cmp.length) return;
  const rows = cmp.slice().sort((a, b) => b.score - a.score), top = rows[0];
  let head, sub = '';
  if (rows.length === 1) { head = top.name; sub = fmt(top.score) + ' points'; }
  else {
    const second = rows[1], diff = top.score - second.score;
    head = diff === 0 ? 'The top two are tied' : top.name + ' is the hardest';
    sub = diff === 0 ? '' : 'ahead of ' + second.name + ' by ' + fmt(diff) + ' points' + (second.score > 0 ? ' (about ×' + fmt(top.score / second.score) + ')' : '');
  }
  const clear = el('button', { class: 'ghost small', text: 'Clear all' });
  clear.addEventListener('click', () => { cmp.length = 0; renderCompare(); });
  const body = el('tbody');
  rows.forEach((x, i) => {
    const rm = el('button', { class: 'ghost small', text: '✕' });
    rm.addEventListener('click', () => { cmp.splice(cmp.indexOf(x), 1); renderCompare(); });
    body.appendChild(el('tr', null, [
      el('td', { text: i + 1 }),
      el('td', null, [el('span', { class: 'dot', style: 'background:' + x.color }), x.name]),
      el('td', { class: 'num', text: x.gddl_failed ? 'failed' : (x.gddl_tier || '–') }),
      el('td', { class: 'num', text: x.aredl_rank ? '#' + x.aredl_rank : '–' }),
      el('td', { class: 'num', text: fmt(x.score) }),
      el('td', { class: 'num', text: i === 0 ? '–' : '−' + fmt(top.score - x.score) }),
      el('td', { class: 'num', text: i === 0 || x.score <= 0 ? '–' : fmt(top.score / x.score) + '× lower' }),
      el('td', null, [rm])
    ]));
  });
  const warnings = cmp.filter(d => d.gddl_failed || !d.gddl_tier).map(d => el('p', { class: 'msg err', text: d.name + ': ' + (d.gddl_failed ? 'the GDDL lookup failed, so its score is missing the tier term. Remove it and add it again in a moment.' : 'no GDDL tier found, so it is not plotted on the graph.') }));
  box.appendChild(el('div', { class: 'card' }, [
    el('div', { style: 'display:flex; justify-content:space-between; align-items:flex-start; gap:10px;' }, [
      el('div', null, [el('div', { class: 'verdict', text: head }), el('div', { class: 'meta', text: sub })]), clear
    ]),
    el('div', { style: 'overflow-x:auto; margin-top:10px;' }, [el('table', null, [
      el('thead', null, [el('tr', null, ['#', 'Level', 'Tier', 'AREDL', 'Score', 'Behind', 'Ratio', ''].map((h, i) => el('th', { class: i >= 2 && i <= 6 ? 'num' : '', text: h })))]),
      body
    ])])
  ].concat(warnings)));
}
$('cb').addEventListener('click', cmpSearch);
$('cq').addEventListener('keydown', e => { if (e.key === 'Enter') cmpSearch(); });

/* ---------- chart: log/linear toggle & Desmos logistic curve ---------- */
let currentScale = 'log';
function setScale(scale) {
  currentScale = scale;
  $('btnLog').classList.toggle('active', scale === 'log');
  $('btnLin').classList.toggle('active', scale === 'linear');
  drawCmpChart();
}

const BANDS = [
  { name: 'Easy', max: 5, color: '#4cd07d' },
  { name: 'Medium', max: 10, color: '#f5d524' },
  { name: 'Hard', max: 15, color: '#f5a524' },
  { name: 'Insane', max: 20, color: '#ff5d5d' },
  { name: 'Extreme', max: 39, color: '#b86bff' }
];
function svgEl(tag, attrs) { const e = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; }
function svgText(attrs, text) { const t = svgEl('text', attrs); t.textContent = text; return t; }

const Y_LABELS_LOG = ['1', '10', '100', '1000', '10K', '100K', '1M', '10M'];

function renderChart(box, pts, xLabel, marks, xTicks, scale) {
  const W = 640, H = 300, L = 58, R = 16, T = 16, B = 40;
  const x0 = pts[0].x, x1 = pts[pts.length - 1].x;
  const px = x => L + (x - x0) / ((x1 - x0) || 1) * (W - L - R);

  let py, yTicks = [];
  if (scale === 'log') {
    const LO = 0, HI = 7;
    py = y => T + (1 - (Math.log10(Math.min(Math.max(y, 1), 1e7)) - LO) / (HI - LO)) * (H - T - B);
    yTicks = Y_LABELS_LOG.map((lbl, k) => ({ yVal: Math.pow(10, k), label: lbl }));
  } else {
    const maxY = Math.max(5000000, ...pts.map(p => p.y), ...marks.map(m => m.y));
    py = y => T + (1 - Math.min(Math.max(y, 0), maxY) / maxY) * (H - T - B);
    yTicks = [0, 0.25, 0.5, 0.75, 1].map(f => ({ yVal: maxY * f, label: maxY * f >= 1e6 ? (maxY * f / 1e6).toFixed(1) + 'M' : Math.round(maxY * f).toLocaleString() }));
  }

  const s = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img' });
  const defs = svgEl('defs', {}), grad = svgEl('linearGradient', { id: 'tierGrad', gradientUnits: 'userSpaceOnUse', x1: px(x0), x2: px(x1), y1: 0, y2: 0 });
  const stop = (o, c) => grad.appendChild(svgEl('stop', { offset: Math.min(Math.max(o, 0), 1), 'stop-color': c }));
  stop(0, BANDS[0].color);
  let from = 1;
  BANDS.forEach(b => { stop(((from + b.max) / 2 - x0) / (x1 - x0), b.color); from = b.max + 1; });
  stop(1, BANDS[BANDS.length - 1].color);
  defs.appendChild(grad); s.appendChild(defs);

  yTicks.forEach(yt => {
    const y = py(yt.yVal);
    s.appendChild(svgEl('line', { x1: L, x2: W - R, y1: y, y2: y, stroke: 'var(--line)' }));
    s.appendChild(svgText({ x: L - 8, y: y + 4, 'text-anchor': 'end', fill: 'var(--muted)', 'font-size': 11 }, yt.label));
  });

  xTicks.forEach(xv => {
    const x = px(xv);
    s.appendChild(svgEl('line', { x1: x, x2: x, y1: H - B, y2: H - B + 4, stroke: 'var(--muted)' }));
    s.appendChild(svgText({ x: x, y: H - B + 17, 'text-anchor': 'middle', fill: 'var(--muted)', 'font-size': 11 }, xv));
  });

  s.appendChild(svgText({ x: (L + W - R) / 2, y: H - 6, 'text-anchor': 'middle', fill: 'var(--muted)', 'font-size': 12 }, xLabel));
  s.appendChild(svgEl('polyline', { points: pts.map(p => px(p.x).toFixed(1) + ',' + py(p.y).toFixed(1)).join(' '), fill: 'none', stroke: 'url(#tierGrad)', 'stroke-width': 1.75, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));

  const sortedMarks = marks.map(m => ({ ...m, cx: px(m.x), cy: py(m.y) })).sort((a, b) => a.cy - b.cy);
  for (let i = 0; i < sortedMarks.length; i++) {
    let curr = sortedMarks[i];
    let yOff = -9;
    if (i > 0 && Math.abs(curr.cy - sortedMarks[i - 1].cy) < 18) {
      yOff = sortedMarks[i - 1].yOff > 0 ? -22 : 18;
    }
    curr.yOff = yOff;
  }

  sortedMarks.forEach(m => {
    const right = m.cx > W - 140;
    s.appendChild(svgEl('circle', { cx: m.cx, cy: m.cy, r: 5, fill: m.color, stroke: '#0f1115', 'stroke-width': 2 }));
    s.appendChild(svgText({ x: m.cx + (right ? -10 : 10), y: m.cy + m.yOff, 'text-anchor': right ? 'end' : 'start', fill: m.color, 'font-size': 12, 'font-weight': 700 }, m.label + ': ' + fmt(m.y)));
  });
  box.replaceChildren(s);
}

// Function curve: Smooth transition from the original curve to the logistic curve
function functionCurve() {
  const pts = [];

  for (let i = 0; i <= 78; i++) {
    const g = i / 2;
    const linear = Math.pow(1.3, g + 9) - 1;
    const logistic = 7146482.46 / (1 + Math.exp(-(0.61511 * g - 23.25027)));

    let y;

    if (g <= 28) {
      y = linear;
    } else if (g >= 32) {
      y = logistic;
    } else {
      const t = (g - 28) / 4;
      const blend = t * t * (3 - 2 * t);
      y = linear * (1 - blend) + logistic * blend;
    }

    pts.push({ x: g, y: Math.max(y, 0) });
  }

  return pts;
}

function drawCmpChart() {
  const marks = cmp.filter(x => x.gddl_tier > 0).map(x => ({ x: x.gddl_tier, y: x.score, label: x.name.length > 14 ? x.name.slice(0, 13) + '…' : x.name, color: x.color }));
  renderChart($('cmpChart'), functionCurve(), 'GDDL tier', marks, [0, 5, 10, 15, 20, 25, 30, 35, 39], currentScale);
}

(function buildLegend() {
  let from = 1;
  BANDS.forEach(b => {
    $('cmpLegend').appendChild(el('span', null, [el('span', { class: 'dot', style: 'background:' + b.color }), b.name + ' (' + from + '–' + b.max + ')']));
    from = b.max + 1;
  });
})();

/* ---------- playground ---------- */
const pairs = [['pS', 'rS'], ['pG', 'rG'], ['pA', 'rA']];
function setPlay(s, g, a) { $('pS').value = $('rS').value = s; $('pG').value = $('rG').value = g; $('pA').value = $('rA').value = a; }
let playTimer = null;
function runPlay() {
  clearTimeout(playTimer);
  playTimer = setTimeout(async () => {
    try {
      const q = 's=' + $('pS').value + '&g=' + $('pG').value + '&a=' + $('pA').value;
      const d = await (await fetch('/api/calc?' + q)).json();
      $('pScore').textContent = fmt(d.score);
      $('pBreak').replaceChildren(breakdown(d.base, d.list_bonus));
    } catch (e) { $('pScore').textContent = '–'; }
  }, 100);
}
pairs.forEach(([n, r]) => {
  $(n).addEventListener('input', () => { $(r).value = $(n).value; runPlay(); });
  $(r).addEventListener('input', () => { $(n).value = $(r).value; runPlay(); });
});

/* ---------- rank table ---------- */
let rankLoaded = false;
async function loadRankTable() {
  if (rankLoaded) return;
  const ranks = [1, 5, 10, 25, 50, 100, 150, 200, 300];
  try {
    const res = await Promise.all(ranks.map(r => fetch('/api/calc?s=1&g=0&a=' + r).then(x => x.json())));
    const body = $('rankTable'); body.replaceChildren();
    ranks.forEach((r, i) => body.appendChild(el('tr', null, [el('td', { text: '#' + r }), el('td', { class: 'num', text: fmt(res[i].list_bonus) })])));
    rankLoaded = true;
  } catch (e) {}
}

$('searchBtn').addEventListener('click', searchLevels);
$('searchInput').addEventListener('keydown', e => { if (e.key === 'Enter') searchLevels(); });
renderRecent(); renderCompare();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(debug=True)

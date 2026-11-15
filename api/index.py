import os
import re
import time
import threading

import requests
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# ----------------------------------------------------------------------------
# Config. If a data source changes its URL, change it here (or via env vars).
# ----------------------------------------------------------------------------
GDBROWSER_BASE = os.environ.get("GDBROWSER_BASE", "https://gdbrowser.com/api")
GDDL_BASE = os.environ.get("GDDL_BASE", "https://gdladder.com/api")
GDDL_API_KEY = os.environ.get("GDDL_API_KEY", "")  # only needed if GDDL requires it
AREDL_LIST_URL = os.environ.get("AREDL_LIST_URL", "https://api.aredl.net/v2/api/aredl/levels")

HEADERS = {"User-Agent": "Mozilla/5.0 (GD Difficulty Calculator)", "Accept": "application/json"}
TIMEOUT = 8
MAX_RESULTS = 25

http = requests.Session()
http.headers.update(HEADERS)


# ----------------------------------------------------------------------------
# Formula (unchanged)
# ----------------------------------------------------------------------------
def calculate_score(s, g, a):
    base = 1.3 ** (g + s - 1)
    list_bonus = (10 / 9) ** (149 - (a - 1) / 7) if a > 0 else 1.0
    return round(base + list_bonus - 2, 2)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def get_json(url, **kwargs):
    """GET a URL and return parsed JSON, or None on any failure."""
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
    """Official level info (name, author, stars) via GDBrowser."""
    data = get_json(f"{GDBROWSER_BASE}/level/{level_id}")
    return normalize_gdbrowser_level(data)


# --- GDDL tier -------------------------------------------------------------
def fetch_gddl_tier(level_id):
    headers = {}
    if GDDL_API_KEY:
        headers["Authorization"] = f"Bearer {GDDL_API_KEY}"
    data = get_json(f"{GDDL_BASE}/level/{level_id}", headers=headers)
    if not isinstance(data, dict):
        return 0.0
    # Some responses wrap the level in another object.
    for key in ("level", "data"):
        if isinstance(data.get(key), dict):
            data = data[key]
            break
    for key in ("Rating", "rating", "Tier", "tier"):
        if data.get(key) is not None:
            return to_float(data.get(key))
    return 0.0


# --- AREDL position (full list cached, indexed by in-game level id) -----------
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
        if by_id:  # don't overwrite a good cache with a failed fetch
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

    # A pure number is treated as a level ID lookup first.
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
        if lvl["stars"] <= 0:  # only rated levels have a score
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
    aredl_rank = 0

    if stars == 10:  # demons only
        gddl_tier = fetch_gddl_tier(level_id)
        aredl_rank = fetch_aredl_rank(level_id)

    score = calculate_score(stars, gddl_tier, aredl_rank)

    return jsonify({
        "name": lvl["name"],
        "author": lvl["author"],
        "id": level_id,
        "stars": stars,
        "gddl_tier": round(gddl_tier, 2),
        "aredl_rank": aredl_rank,
        "score": score,
    })


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
    <title>GD Difficulty Calculator</title>
    <style>
        body { font-family: system-ui, -apple-system, sans-serif; background: #121212; color: #fff; display: flex; justify-content: center; padding: 40px 20px; margin: 0; }
        .container { background: #1e1e1e; padding: 30px; border-radius: 12px; width: 100%; max-width: 500px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
        .search-box { display: flex; gap: 10px; }
        input, select { padding: 12px; border-radius: 8px; border: 1px solid #333; background: #2c2c2c; color: #fff; font-size: 15px; outline: none; }
        input { flex: 1; min-width: 0; }
        input:focus, select:focus { border-color: #4caf50; }
        button { padding: 12px 18px; border-radius: 8px; border: none; background: #4caf50; color: #fff; font-weight: bold; cursor: pointer; font-size: 15px; }
        button:hover { background: #43a047; }
        .non-demon-bar { display: flex; align-items: center; gap: 10px; margin-top: 12px; padding: 10px 12px; background: #252525; border-radius: 8px; font-size: 0.9em; color: #ccc; }
        .non-demon-bar select { padding: 8px 10px; font-size: 14px; }
        .non-demon-bar button { padding: 8px 14px; font-size: 14px; background: #383838; margin-left: auto; }
        .non-demon-bar button:hover { background: #484848; }
        .section-title { font-size: 0.8em; text-transform: uppercase; letter-spacing: 1px; color: #888; margin: 18px 0 8px 0; font-weight: bold; }
        .results-list { display: flex; flex-direction: column; gap: 8px; }
        .level-item { background: #282828; padding: 12px 15px; border-radius: 8px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; border: 1px solid transparent; transition: 0.15s; }
        .level-item:hover { border-color: #4caf50; background: #303030; }
        .level-item.exact { border-left: 3px solid #4caf50; }
        .level-meta { font-size: 0.85em; color: #aaa; margin-top: 2px; }
        .badge { background: #383838; padding: 4px 8px; border-radius: 6px; font-size: 0.85em; font-weight: bold; }
        .score-card { margin-top: 25px; padding: 20px; background: #252525; border-radius: 10px; border-left: 4px solid #4caf50; display: none; }
        .score-number { font-size: 2.4em; font-weight: 800; color: #4caf50; margin: 8px 0; word-break: break-all; }
        .msg { padding: 12px 0; color: #aaa; }
        .msg.err { color: #ff5252; }
    </style>
</head>
<body>
    <div class="container">
        <h2 style="margin-top:0;">GD Difficulty Score</h2>
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Search level name or ID...">
            <button id="searchBtn">Search</button>
        </div>

        <div class="non-demon-bar">
            <span>Quick non-demon calc:</span>
            <select id="nonDemonStars">
                <option value="1">1★ (Auto)</option>
                <option value="2">2★ (Easy)</option>
                <option value="3">3★ (Normal)</option>
                <option value="4">4★ (Hard)</option>
                <option value="5">5★ (Hard)</option>
                <option value="6">6★ (Harder)</option>
                <option value="7">7★ (Harder)</option>
                <option value="8" selected>8★ (Insane)</option>
                <option value="9">9★ (Insane)</option>
            </select>
            <button id="calcBtn">Calc Stars</button>
        </div>

        <div id="resultsArea"></div>
        <div id="scoreCard" class="score-card"></div>
    </div>

    <script>
        function el(tag, props, children) {
            const e = document.createElement(tag);
            if (props) {
                for (const k in props) {
                    if (k === 'class') e.className = props[k];
                    else if (k === 'text') e.textContent = props[k];
                    else if (k === 'style') e.setAttribute('style', props[k]);
                    else e[k] = props[k];
                }
            }
            (children || []).forEach(c => e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
            return e;
        }

        function message(text, isErr) {
            return el('div', { class: 'msg' + (isErr ? ' err' : ''), text: text });
        }

        function setArea(node) {
            const area = document.getElementById('resultsArea');
            area.replaceChildren(node);
        }

        function levelCard(lvl, isExact) {
            const item = el('div', { class: 'level-item' + (isExact ? ' exact' : '') }, [
                el('div', null, [
                    el('div', { style: 'font-weight:bold;', text: lvl.name }),
                    el('div', { class: 'level-meta', text: 'by ' + lvl.author + ' • ID: ' + lvl.id })
                ]),
                el('div', { class: 'badge', text: lvl.stars + '★' })
            ]);
            item.addEventListener('click', () => selectLevel(lvl.id));
            return item;
        }

        function section(title, levels, isExact) {
            const frag = document.createDocumentFragment();
            frag.appendChild(el('div', { class: 'section-title', text: title }));
            frag.appendChild(el('div', { class: 'results-list' }, levels.map(l => levelCard(l, isExact))));
            return frag;
        }

        function showCard(nodes) {
            const card = document.getElementById('scoreCard');
            card.style.display = 'block';
            card.replaceChildren(...nodes);
        }

        function metaLine(parts) {
            const line = el('div', { class: 'level-meta' });
            parts.forEach((p, i) => {
                if (i > 0) line.appendChild(document.createTextNode('  |  '));
                line.appendChild(document.createTextNode(p[0] + ': '));
                line.appendChild(el('strong', { text: p[1] }));
            });
            return line;
        }

        function calcNonDemon() {
            const s = parseInt(document.getElementById('nonDemonStars').value, 10);
            const score = Math.round((Math.pow(1.3, s - 1) - 1) * 100) / 100;
            showCard([
                el('div', { style: 'font-size:1.2em; font-weight:bold;', text: 'Non-Demon (' + s + '★)' }),
                el('div', { class: 'score-number', text: score.toLocaleString() }),
                metaLine([['Stars', s + '★'], ['GDDL Tier', '0'], ['AREDL', 'N/A']])
            ]);
        }

        async function searchLevels() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) return;

            document.getElementById('scoreCard').style.display = 'none';
            setArea(message('Searching...', false));

            try {
                const res = await fetch('/api/search?q=' + encodeURIComponent(query));
                const data = await res.json();

                if (!data.exact.length && !data.contains.length) {
                    setArea(message('No rated levels found matching "' + query + '".', true));
                    return;
                }

                const frag = document.createDocumentFragment();
                if (data.exact.length) frag.appendChild(section('Exact Matches', data.exact, true));
                if (data.contains.length) frag.appendChild(section('Containing "' + query + '"', data.contains, false));
                setArea(frag);
            } catch (err) {
                setArea(message('Error connecting to server.', true));
            }
        }

        async function selectLevel(id) {
            showCard([el('div', { style: 'color:#aaa;', text: 'Calculating score...' })]);
            try {
                const res = await fetch('/api/score/' + id);
                const data = await res.json();

                if (data.error) {
                    showCard([el('div', { style: 'color:#ff5252;', text: data.error })]);
                    return;
                }

                showCard([
                    el('div', { style: 'font-size:1.2em; font-weight:bold;' }, [
                        data.name + ' ',
                        el('span', { style: 'font-weight:normal; color:#aaa; font-size:0.85em;', text: 'by ' + data.author })
                    ]),
                    el('div', { class: 'score-number', text: data.score.toLocaleString() }),
                    metaLine([
                        ['Stars', data.stars + '★'],
                        ['GDDL Tier', data.gddl_tier || 'N/A'],
                        ['AREDL Rank', data.aredl_rank ? '#' + data.aredl_rank : 'Unranked']
                    ])
                ]);
            } catch (err) {
                showCard([el('div', { style: 'color:#ff5252;', text: 'Failed to calculate score.' })]);
            }
        }

        document.getElementById('searchBtn').addEventListener('click', searchLevels);
        document.getElementById('calcBtn').addEventListener('click', calcNonDemon);
        document.getElementById('searchInput').addEventListener('keydown', e => { if (e.key === 'Enter') searchLevels(); });
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(debug=True)

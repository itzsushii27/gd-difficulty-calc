from flask import Flask, request, jsonify, render_template_string
import requests

app = Flask(__name__)

def calculate_score(s, g, a):
    base = 1.3 ** (g + s - 1)
    list_bonus = (10 / 9) ** (149 - (a - 1) / 7) if a > 0 else 1.0
    return round(base + list_bonus - 2, 2)

def parse_gd_levels(raw_text):
    if not raw_text or raw_text == "-1" or "#" not in raw_text:
        return []
    
    parts = raw_text.split("#")
    levels_raw = parts.split("|")
    creators_raw = parts.split("|") if len(parts) > 1 else []
    
    # Map player ID -> creator username
    creators = {}
    for c in creators_raw:
        c_parts = c.split(":")
        if len(c_parts) >= 2:
            creators[c_parts] = c_parts
            
    parsed = []
    for lvl_str in levels_raw:
        if not lvl_str:
            continue
        kv = lvl_str.split(":")
        data = {kv[i]: kv[i + 1] for i in range(0, len(kv) - 1, 2)}
        
        lvl_id = int(data.get("1", 0))
        name = data.get("2", "Unknown")
        creator_id = data.get("6", "")
        stars = int(data.get("18", 0))
        downloads = int(data.get("10", 0))
        
        if lvl_id > 0:
            parsed.append({
                "id": lvl_id,
                "name": name,
                "author": creators.get(creator_id, "Unknown"),
                "stars": stars,
                "downloads": downloads
            })
    return parsed

@app.route("/api/search")
def search_levels():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"exact": [], "contains": []})
    
    try:
        res = requests.post(
            "https://boomlings.com",
            data={
                "str": query,
                "star": 1,
                "type": 0,
                "secret": "Wmfd2893gb7"
            },
            headers={"User-Agent": ""},
            timeout=6
        )
        levels = parse_gd_levels(res.text)
    except Exception:
        levels = []

    q_lower = query.lower()
    exact_matches = []
    contains_matches = []

    for lvl in levels:
        name_lower = lvl["name"].lower()
        if name_lower == q_lower:
            exact_matches.append(lvl)
        elif q_lower in name_lower:
            contains_matches.append(lvl)

    exact_matches.sort(key=lambda x: x["downloads"], reverse=True)
    contains_matches.sort(key=lambda x: x["downloads"], reverse=True)

    return jsonify({
        "exact": exact_matches,
        "contains": contains_matches
    })

@app.route("/api/score/<int:level_id>")
def get_level_score(level_id):
    name = request.args.get("name", "Unknown")
    author = request.args.get("author", "Unknown")
    stars = int(request.args.get("stars", 0))

    if stars == 0:
        return jsonify({"error": f"{name} is unrated (0★) and cannot be scored."}), 400

    gddl_tier = 0.0
    aredl_rank = 0

    # Dropdown is ignored for 10-star Demons; fetch GDDL and AREDL
    if stars == 10:
        try:
            gddl_res = requests.get(f"https://gdladder.com{level_id}", timeout=5)
            if gddl_res.status_code == 200:
                gddl_json = gddl_res.json()
                gddl_tier = float(gddl_json.get("Rating") or 0.0)
        except Exception:
            pass

        try:
            aredl_res = requests.get(f"https://aredl.net{level_id}", timeout=5)
            if aredl_res.status_code == 200:
                aredl_json = aredl_res.json()
                aredl_rank = int(aredl_json.get("position") or 0)
        except Exception:
            pass

    score = calculate_score(stars, gddl_tier, aredl_rank)

    return jsonify({
        "name": name,
        "author": author,
        "id": level_id,
        "stars": stars,
        "gddl_tier": round(gddl_tier, 2),
        "aredl_rank": aredl_rank,
        "score": score
    })

@app.route("/")
@app.route("/api/index")
def index():
    return render_template_string(HTML_TEMPLATE)

HTML_TEMPLATE = """
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
        input { flex: 1; }
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
        .score-number { font-size: 2.4em; font-weight: 800; color: #4caf50; margin: 8px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h2 style="margin-top:0;">GD Difficulty Score</h2>
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Search level (e.g. Acu, B, ReTraY)..." onkeydown="if(event.key==='Enter') searchLevels()">
            <button onclick="searchLevels()">Search</button>
        </div>

        <div class="non-demon-bar">
            <span>Non-Demon Stars:</span>
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
            <button onclick="calcNonDemon()">Calc Stars</button>
        </div>
        
        <div id="resultsArea"></div>
        <div id="scoreCard" class="score-card"></div>
    </div>

    <script>
        function renderLevelCard(lvl, isExact) {
            const safeName = lvl.name.replace(/'/g, "\\'");
            const safeAuthor = lvl.author.replace(/'/g, "\\'");
            return '<div class="level-item ' + (isExact ? 'exact' : '') + '" onclick="selectLevel(' + lvl.id + ', \'' + safeName + '\', \'' + safeAuthor + '\', ' + lvl.stars + ')">' +
                '<div>' +
                    '<div style="font-weight:bold;">' + lvl.name + '</div>' +
                    '<div class="level-meta">by ' + lvl.author + ' • ID: ' + lvl.id + '</div>' +
                '</div>' +
                '<div class="badge">' + lvl.stars + '★</div>' +
            '</div>';
        }

        function calcNonDemon() {
            const s = parseInt(document.getElementById('nonDemonStars').value, 10);
            const score = Math.round((Math.pow(1.3, s - 1) - 1) * 100) / 100;
            const card = document.getElementById('scoreCard');
            card.style.display = 'block';
            card.innerHTML = '<div style="font-size:1.2em; font-weight:bold;">Non-Demon (' + s + '★)</div>' +
                '<div class="score-number">' + score.toLocaleString() + '</div>' +
                '<div class="level-meta">Stars: <strong>' + s + '★</strong> &nbsp;|&nbsp; GDDL Tier: <strong>0</strong> &nbsp;|&nbsp; AREDL: <strong>N/A</strong></div>';
        }

        async function searchLevels() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) return;
            
            const area = document.getElementById('resultsArea');
            const card = document.getElementById('scoreCard');
            card.style.display = 'none';
            area.innerHTML = '<div style="color:#aaa; padding:12px 0;">Searching Geometry Dash servers...</div>';
            
            try {
                const res = await fetch('/api/search?q=' + encodeURIComponent(query));
                const data = await res.json();
                
                if (data.exact.length === 0 && data.contains.length === 0) {
                    area.innerHTML = '<div style="color:#ff5252; padding:12px 0;">No rated levels found matching "' + query + '".</div>';
                    return;
                }
                
                let html = '';
                if (data.exact.length > 0) {
                    html += '<div class="section-title">Exact Matches</div><div class="results-list">';
                    html += data.exact.map(lvl => renderLevelCard(lvl, true)).join('');
                    html += '</div>';
                }
                if (data.contains.length > 0) {
                    html += '<div class="section-title">Containing "' + query + '"</div><div class="results-list">';
                    html += data.contains.map(lvl => renderLevelCard(lvl, false)).join('');
                    html += '</div>';
                }
                area.innerHTML = html;
            } catch (err) {
                area.innerHTML = '<div style="color:#ff5252; padding:12px 0;">Error connecting to server.</div>';
            }
        }

        async function selectLevel(id, name, author, stars) {
            const card = document.getElementById('scoreCard');
            card.style.display = 'block';
            card.innerHTML = '<div style="color:#aaa;">Calculating score...</div>';

            const effectiveStars = (stars === 10) ? 10 : parseInt(document.getElementById('nonDemonStars').value, 10);
            
            try {
                const url = '/api/score/' + id + '?name=' + encodeURIComponent(name) + '&author=' + encodeURIComponent(author) + '&stars=' + effectiveStars;
                const res = await fetch(url);
                const data = await res.json();
                
                if (data.error) {
                    card.innerHTML = '<div style="color:#ff5252;">' + data.error + '</div>';
                    return;
                }
                
                card.innerHTML = '<div style="font-size:1.2em; font-weight:bold;">' + data.name + ' <span style="font-weight:normal; color:#aaa; font-size:0.85em;">by ' + data.author + '</span></div>' +
                    '<div class="score-number">' + data.score.toLocaleString() + '</div>' +
                    '<div class="level-meta">' +
                        'Stars: <strong>' + data.stars + '★</strong> &nbsp;|&nbsp; ' +
                        'GDDL Tier: <strong>' + (data.gddl_tier || 'N/A') + '</strong> &nbsp;|&nbsp; ' +
                        'AREDL Rank: <strong>' + (data.aredl_rank ? '#' + data.aredl_rank : 'Unranked') + '</strong>' +
                    '</div>';
            } catch (err) {
                card.innerHTML = '<div style="color:#ff5252;">Failed to calculate score.</div>';
            }
        }
    </script>
</body>
</html>
"""

from flask import Flask, request, jsonify, render_template_string
import requests

app = Flask(__name__)

def calculate_score(s, g, a):
    base = 1.3 ** (g + s - 1)
    list_bonus = (10 / 9) ** (149 - (a - 1) / 7) if a > 0 else 1.0
    return round(base + list_bonus - 2, 2)

@app.route("/api/search")
def search_levels():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    
    try:
        res = requests.get(
            f"https://gdbrowser.com{requests.utils.quote(query)}",
            params={"count": 10},
            timeout=6
        )
        if res.status_code != 200 or res.text == "-1":
            return jsonify([])
        
        levels = res.json()
        results = []
        for lvl in levels:
            results.append({
                "id": lvl.get("id"),
                "name": lvl.get("name"),
                "author": lvl.get("author"),
                "stars": lvl.get("stars", 0),
                "difficulty": lvl.get("difficulty", "Unknown")
            })
        return jsonify(results)
    except Exception:
        return jsonify([])

@app.route("/api/score/<int:level_id>")
def get_level_score(level_id):
    # 1. Fetch Level Info from GDBrowser
    try:
        gd_res = requests.get(f"https://gdbrowser.com{level_id}", timeout=6)
        if gd_res.status_code != 200 or gd_res.text == "-1":
            return jsonify({"error": "Level not found on GDBrowser"}), 404
        gd_data = gd_res.json()
    except Exception:
        return jsonify({"error": "Failed to connect to GDBrowser"}), 502

    stars = gd_data.get("stars", 0)
    name = gd_data.get("name", "Unknown")
    author = gd_data.get("author", "Unknown")

    if stars == 0:
        return jsonify({"error": f"{name} is unrated (0★) and cannot be scored."}), 400

    # 2. Fetch GDDL Tier (if 10-star Demon)
    gddl_tier = 0.0
    if stars == 10:
        try:
            gddl_res = requests.get(f"https://gdladder.com{level_id}", timeout=5)
            if gddl_res.status_code == 200:
                gddl_json = gddl_res.json()
                gddl_tier = float(gddl_json.get("Rating") or 0.0)
        except Exception:
            pass

    # 3. Fetch AREDL Rank (if 10-star Demon)
    aredl_rank = 0
    if stars == 10:
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
        body { font-family: system-ui, -apple-system, sans-serif; background: #121212; color: #fff; display: flex; justify-content: center; padding: 50px 20px; margin: 0; }
        .container { background: #1e1e1e; padding: 30px; border-radius: 12px; width: 100%; max-width: 480px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
        .search-box { display: flex; gap: 10px; }
        input { flex: 1; padding: 12px; border-radius: 8px; border: 1px solid #333; background: #2c2c2c; color: #fff; font-size: 16px; outline: none; }
        input:focus { border-color: #4caf50; }
        button { padding: 12px 20px; border-radius: 8px; border: none; background: #4caf50; color: #fff; font-weight: bold; cursor: pointer; font-size: 16px; }
        button:hover { background: #43a047; }
        .results-list { margin-top: 15px; display: flex; flex-direction: column; gap: 8px; }
        .level-item { background: #282828; padding: 12px 15px; border-radius: 8px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; border: 1px solid transparent; transition: 0.15s; }
        .level-item:hover { border-color: #4caf50; background: #303030; }
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
            <input type="text" id="searchInput" placeholder="Search level name (e.g. Acu, Bloodbath)..." onkeydown="if(event.key==='Enter') searchLevels()">
            <button onclick="searchLevels()">Search</button>
        </div>
        
        <div id="resultsList" class="results-list"></div>
        <div id="scoreCard" class="score-card"></div>
    </div>

    <script>
        async function searchLevels() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) return;
            
            const list = document.getElementById('resultsList');
            const card = document.getElementById('scoreCard');
            card.style.display = 'none';
            list.innerHTML = '<div style="color:#aaa; padding:10px;">Searching GDBrowser...</div>';
            
            try {
                const res = await fetch('/api/search?q=' + encodeURIComponent(query));
                const levels = await res.json();
                
                if (levels.length === 0) {
                    list.innerHTML = '<div style="color:#ff5252; padding:10px;">No levels found (or GDBrowser rate-limited the request).</div>';
                    return;
                }
                
                list.innerHTML = levels.map(lvl => `
                    <div class="level-item" onclick="selectLevel(${lvl.id})">
                        <div>
                            <div style="font-weight:bold;">${lvl.name}</div>
                            <div class="level-meta">by ${lvl.author} • ID: ${lvl.id}</div>
                        </div>
                        <div class="badge">${lvl.stars > 0 ? lvl.stars + '★' : 'Unrated'}</div>
                    </div>
                `).join('');
            } catch (err) {
                list.innerHTML = '<div style="color:#ff5252; padding:10px;">Error connecting to server.</div>';
            }
        }

        async function selectLevel(id) {
            const card = document.getElementById('scoreCard');
            card.style.display = 'block';
            card.innerHTML = '<div style="color:#aaa;">Calculating score from GDDL & AREDL...</div>';
            
            try {
                const res = await fetch('/api/score/' + id);
                const data = await res.json();
                
                if (data.error) {
                    card.innerHTML = `<div style="color:#ff5252;">${data.error}</div>`;
                    return;
                }
                
                card.innerHTML = `
                    <div style="font-size:1.2em; font-weight:bold;">${data.name} <span style="font-weight:normal; color:#aaa; font-size:0.85em;">by ${data.author}</span></div>
                    <div class="score-number">${data.score.toLocaleString()}</div>
                    <div class="level-meta">
                        Stars: <strong>${data.stars}★</strong> &nbsp;|&nbsp; 
                        GDDL Tier: <strong>${data.gddl_tier || 'N/A'}</strong> &nbsp;|&nbsp; 
                        AREDL Rank: <strong>${data.aredl_rank ? '#' + data.aredl_rank : 'Unranked'}</strong>
                    </div>
                `;
            } catch (err) {
                card.innerHTML = '<div style="color:#ff5252;">Failed to fetch level score.</div>';
            }
        }
    </script>
</body>
</html>
"""

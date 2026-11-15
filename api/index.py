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

    results = []
    # Search GDDL's public level search endpoint (no cloud IP blocks)
    try:
        res = requests.get(
            "https://gdladder.com",
            params={"name": query, "limit": 10},
            headers={"User-Agent": "GDDifficultyCalculator/1.0"},
            timeout=6
        )
        if res.status_code == 200:
            data = res.json()
            levels = data if isinstance(data, list) else data.get("levels", data.get("data", []))
            for lvl in levels[:10]:
                lvl_id = lvl.get("ID") or lvl.get("id")
                name = lvl.get("Name") or lvl.get("name") or "Unknown"
                author = lvl.get("Publisher") or lvl.get("Author") or lvl.get("author") or "Unknown"
                rating = float(lvl.get("Rating") or lvl.get("rating") or 0.0)
                if lvl_id:
                    results.append({
                        "id": lvl_id,
                        "name": name,
                        "author": author,
                        "stars": 10,
                        "gddl_tier": round(rating, 2)
                    })
    except Exception:
        pass

    return jsonify(results)

@app.route("/api/score/<int:level_id>")
def get_level_score(level_id):
    name = request.args.get("name", "Unknown")
    author = request.args.get("author", "Unknown")
    gddl_tier = float(request.args.get("gddl", 0.0))
    aredl_rank = 0

    # Refresh GDDL Tier if needed
    if gddl_tier == 0.0:
        try:
            gddl_res = requests.get(f"https://gdladder.com{level_id}", timeout=5)
            if gddl_res.status_code == 200:
                gddl_json = gddl_res.json()
                gddl_tier = float(gddl_json.get("Rating") or 0.0)
                name = gddl_json.get("Name") or name
        except Exception:
            pass

    # Fetch AREDL Rank
    try:
        aredl_res = requests.get(f"https://aredl.net{level_id}", timeout=5)
        if aredl_res.status_code == 200:
            aredl_json = aredl_res.json()
            aredl_rank = int(aredl_json.get("position") or 0)
            name = aredl_json.get("name") or name
    except Exception:
        pass

    score = calculate_score(10, gddl_tier, aredl_rank)

    return jsonify({
        "name": name,
        "author": author,
        "id": level_id,
        "stars": 10,
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
        .manual-box { margin-top: 30px; padding-top: 20px; border-top: 1px solid #333; }
        .manual-inputs { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin: 10px 0; }
        .manual-inputs input { width: 100%; box-sizing: border-box; font-size: 14px; padding: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h2 style="margin-top:0;">GD Difficulty Score</h2>
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Search Demon name (e.g. Acu, B, GRIEF)..." onkeydown="if(event.key==='Enter') searchLevels()">
            <button onclick="searchLevels()">Search</button>
        </div>
        
        <div id="resultsList" class="results-list"></div>
        <div id="scoreCard" class="score-card"></div>

        <div class="manual-box">
            <div style="font-size:0.9em; color:#aaa; font-weight:bold;">Manual Calculator (Non-Demons / Custom)</div>
            <div class="manual-inputs">
                <input type="number" id="manStars" placeholder="Stars (2-10)" value="8">
                <input type="number" step="0.01" id="manGddl" placeholder="GDDL (0-39)" value="0">
                <input type="number" id="manAredl" placeholder="AREDL (0-1600)" value="0">
            </div>
            <button style="width:100%; background:#383838;" onclick="calcManual()">Calculate Custom Score</button>
        </div>
    </div>

    <script>
        async function searchLevels() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) return;
            
            const list = document.getElementById('resultsList');
            const card = document.getElementById('scoreCard');
            card.style.display = 'none';
            list.innerHTML = '<div style="color:#aaa; padding:10px;">Searching GDDL database...</div>';
            
            try {
                const res = await fetch('/api/search?q=' + encodeURIComponent(query));
                const levels = await res.json();
                
                if (levels.length === 0) {
                    list.innerHTML = '<div style="color:#ff5252; padding:10px;">No Demons found matching that name.</div>';
                    return;
                }
                
                list.innerHTML = levels.map(lvl => `
                    <div class="level-item" onclick="selectLevel(${lvl.id}, '${lvl.name.replace(/'/g, "\\'")}', '${lvl.author.replace(/'/g, "\\'")}', ${lvl.gddl_tier})">
                        <div>
                            <div style="font-weight:bold;">${lvl.name}</div>
                            <div class="level-meta">by ${lvl.author} • ID: ${lvl.id}</div>
                        </div>
                        <div class="badge">Tier ${lvl.gddl_tier}</div>
                    </div>
                `).join('');
            } catch (err) {
                list.innerHTML = '<div style="color:#ff5252; padding:10px;">Error searching levels.</div>';
            }
        }

        async function selectLevel(id, name, author, gddl) {
            const card = document.getElementById('scoreCard');
            card.style.display = 'block';
            card.innerHTML = '<div style="color:#aaa;">Checking AREDL rank & calculating...</div>';
            
            try {
                const url = `/api/score/${id}?name=${encodeURIComponent(name)}&author=${encodeURIComponent(author)}&gddl=${gddl}`;
                const res = await fetch(url);
                const data = await res.json();
                
                card.innerHTML = `
                    <div style="font-size:1.2em; font-weight:bold;">${data.name} <span style="font-weight:normal; color:#aaa; font-size:0.85em;">by ${data.author}</span></div>
                    <div class="score-number">${data.score.toLocaleString()}</div>
                    <div class="level-meta">
                        Stars: <strong>10★</strong> &nbsp;|&nbsp; 
                        GDDL Tier: <strong>${data.gddl_tier || 'N/A'}</strong> &nbsp;|&nbsp; 
                        AREDL Rank: <strong>${data.aredl_rank ? '#' + data.aredl_rank : 'Unranked'}</strong>
                    </div>
                `;
            } catch (err) {
                card.innerHTML = '<div style="color:#ff5252;">Failed to calculate score.</div>';
            }
        }

                function calcManual() {
            const s = parseFloat(document.getElementById('manStars').value) || 0;
            const g = parseFloat(document.getElementById('manGddl').value) || 0;
            const a = parseFloat(document.getElementById('manAredl').value) || 0;
            const base = Math.pow(1.3, g + s - 1);
            const listBonus = a > 0 ? Math.pow(10 / 9, 149 - (a - 1) / 7) : 1.0;
            const score = Math.round((base + listBonus - 2) * 100) / 100;

            const card = document.getElementById('scoreCard');
            card.style.display = 'block';
            card.innerHTML = '<div style="font-size:1.1em; font-weight:bold;">Custom Level Calculation</div>' +
                '<div class="score-number">' + score.toLocaleString() + '</div>' +
                '<div class="level-meta">Stars: <strong>' + s + '★</strong> | GDDL: <strong>' + g + '</strong> | AREDL: <strong>' + (a > 0 ? '#' + a : 'None') + '</strong></div>';
        }
    </script>
</body>
</html>
"""

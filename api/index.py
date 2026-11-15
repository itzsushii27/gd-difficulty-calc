from flask import Flask, jsonify, render_template_string
import requests

app = Flask(__name__)

@app.route("/api/demon-stats/<int:level_id>")
def get_demon_stats(level_id):
    gddl_tier = 0.0
    aredl_rank = 0

    # 1. Fetch GDDL Tier
    try:
        gddl_res = requests.get(f"https://gdladder.com{level_id}", timeout=5)
        if gddl_res.status_code == 200:
            gddl_json = gddl_res.json()
            gddl_tier = float(gddl_json.get("Rating") or 0.0)
    except Exception:
        pass

    # 2. Fetch AREDL Rank
    try:
        aredl_res = requests.get(f"https://aredl.net{level_id}", timeout=5)
        if aredl_res.status_code == 200:
            aredl_json = aredl_res.json()
            aredl_rank = int(aredl_json.get("position") or 0)
    except Exception:
        pass

    return jsonify({
        "gddl_tier": round(gddl_tier, 2),
        "aredl_rank": aredl_rank
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
        let cachedLevels = {};

        function calculateScore(s, g, a) {
            const base = Math.pow(1.3, g + s - 1);
            const listBonus = a > 0 ? Math.pow(10 / 9, 149 - (a - 1) / 7) : 1.0;
            return Math.round((base + listBonus - 2) * 100) / 100;
        }

        async function searchLevels() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) return;
            
            const list = document.getElementById('resultsList');
            const card = document.getElementById('scoreCard');
            card.style.display = 'none';
            list.innerHTML = '<div style="color:#aaa; padding:10px;">Searching GDBrowser...</div>';
            
            try {
                // Fetch directly from the user's browser to avoid Vercel IP blocks
                const res = await fetch('https://gdbrowser.com' + encodeURIComponent(query) + '?count=10');
                const text = await res.text();
                
                if (!res.ok || text === '-1') {
                    list.innerHTML = '<div style="color:#ff5252; padding:10px;">No levels found.</div>';
                    return;
                }
                
                const levels = JSON.parse(text);
                cachedLevels = {};
                levels.forEach(lvl => { cachedLevels[lvl.id] = lvl; });
                
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
                list.innerHTML = '<div style="color:#ff5252; padding:10px;">Browser blocked CORS or GDBrowser is offline.</div>';
            }
        }

        async function selectLevel(id) {
            const lvl = cachedLevels[id];
            const card = document.getElementById('scoreCard');
            card.style.display = 'block';

            if (!lvl || lvl.stars === 0) {
                card.innerHTML = `<div style="color:#ff5252;">${lvl ? lvl.name : 'Level'} is unrated (0★) and cannot be scored.</div>`;
                return;
            }

            card.innerHTML = '<div style="color:#aaa;">Fetching GDDL & AREDL stats...</div>';
            
            let gddl = 0;
            let aredl = 0;

            if (lvl.stars === 10) {
                try {
                    const res = await fetch('/api/demon-stats/' + id);
                    const stats = await res.json();
                    gddl = stats.gddl_tier || 0;
                    aredl = stats.aredl_rank || 0;
                } catch (err) {
                    console.error(err);
                }
            }

            const score = calculateScore(lvl.stars, gddl, aredl);

            card.innerHTML = `
                <div style="font-size:1.2em; font-weight:bold;">${lvl.name} <span style="font-weight:normal; color:#aaa; font-size:0.85em;">by ${lvl.author}</span></div>
                <div class="score-number">${score.toLocaleString()}</div>
                <div class="level-meta">
                    Stars: <strong>${lvl.stars}★</strong> &nbsp;|&nbsp; 
                    GDDL Tier: <strong>${gddl || 'N/A'}</strong> &nbsp;|&nbsp; 
                    AREDL Rank: <strong>${aredl ? '#' + aredl : 'Unranked'}</strong>
                </div>
            `;
        }
    </script>
</body>
</html>
"""

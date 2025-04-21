import os
import io
import sqlite3
import requests
from flask import Flask, request, send_file, render_template, redirect, url_for
from datetime import datetime
import pytz
import geoip2.database

app = Flask(__name__)
DB_FILE = 'email_tracker.db'
GEO_FILE = 'GeoLite2-City.mmdb'
GDRIVE_URL = 'https://drive.google.com/uc?export=download&id=18SEl_i1V5zIaS1_y4bW1WYas7rxSLcwG'

cn_tz = pytz.timezone('Asia/Shanghai')
us_tz = pytz.timezone('America/New_York')

# ------------------------- 自动下载 mmdb -------------------------
def ensure_geoip_file():
    if not os.path.exists(GEO_FILE):
        print("[INFO] GeoIP 数据库不存在，尝试下载...")
        try:
            r = requests.get(GDRIVE_URL, allow_redirects=True)
            if r.status_code == 200:
                with open(GEO_FILE, 'wb') as f:
                    f.write(r.content)
                print("[SUCCESS] GeoLite2-City.mmdb 下载完成！")
            else:
                print(f"[ERROR] 下载失败，状态码: {r.status_code}")
        except Exception as e:
            print(f"[ERROR] 下载 GeoIP 数据库时出错: {e}")

def resolve_ip(ip):
    try:
        reader = geoip2.database.Reader(GEO_FILE)
        res = reader.city(ip)
        return f"{res.country.name or ''} {res.subdivisions.most_specific.name or ''} {res.city.name or ''}"
    except:
        return "未知"

ensure_geoip_file()

# ------------------------- 初始化数据库 -------------------------
def init_db():
    with sqlite3.connect(DB_FILE) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS open_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        track_id TEXT,
                        opened_at TEXT,
                        ip TEXT,
                        user_agent TEXT
                    )''')
        db.commit()

init_db()

# ------------------------- 处理追踪像素请求 -------------------------
@app.route('/track/<track_id>.png')
def track_pixel(track_id):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    user_agent = request.headers.get('User-Agent', 'unknown')
    opened_at = datetime.utcnow().isoformat()

    with sqlite3.connect(DB_FILE) as db:
        db.execute("INSERT INTO open_events (track_id, opened_at, ip, user_agent) VALUES (?, ?, ?, ?)",
                   (track_id, opened_at, ip, user_agent))
        db.commit()

    pixel_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDAT\x08\xd7c``\x00\x00\x00\x04\x00\x01\r\n*\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    return send_file(io.BytesIO(pixel_data), mimetype='image/png')

# ------------------------- 首页筛选 + 展示 -------------------------
@app.route("/")
def home():
    selected_id = request.args.get("filter_id")
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT track_id FROM open_events")
        all_ids = [row[0] for row in cur.fetchall()]

        events = []
        if selected_id:
            cur.execute("SELECT opened_at, ip, user_agent FROM open_events WHERE track_id = ? ORDER BY opened_at DESC", (selected_id,))
            for row in cur.fetchall():
                ts, ip, ua = row
                try:
                    utc_time = datetime.fromisoformat(ts)
                    cn_time = utc_time.astimezone(cn_tz).strftime("%Y-%m-%d %H:%M:%S")
                    us_time = utc_time.astimezone(us_tz).strftime("%Y-%m-%d %H:%M:%S")
                except:
                    cn_time = us_time = ts
                location = resolve_ip(ip)
                events.append({"time_cn": cn_time, "time_us": us_time, "ip": ip, "location": location, "ua": ua})

    return render_template("index.html", all_ids=all_ids, selected_id=selected_id, events=events)

# ------------------------- 某个追踪ID详情 -------------------------
@app.route('/admin/<track_id>')
def detail(track_id):
    with sqlite3.connect(DB_FILE) as db:
        cur = db.execute("SELECT opened_at, ip, user_agent FROM open_events WHERE track_id = ? ORDER BY opened_at DESC", (track_id,))
        rows = cur.fetchall()

    try:
        reader = geoip2.database.Reader(GEO_FILE)
    except Exception as e:
        reader = None
        print(f"[WARN] 无法读取 GeoIP 数据库: {e}")

    formatted = []
    for i, (ts, ip, ua) in enumerate(rows):
        try:
            utc_time = datetime.fromisoformat(ts)
            beijing_time = utc_time.astimezone(pytz.timezone('Asia/Shanghai'))
            ny_time = utc_time.astimezone(pytz.timezone('America/New_York'))
        except:
            beijing_time = ny_time = ts

        if reader:
            try:
                response = reader.city(ip)
                location = f"{response.country.name or ''} {response.subdivisions.most_specific.name or ''} {response.city.name or ''}"
            except:
                location = "未知"
        else:
            location = "(GeoLite2 未加载)"

        formatted.append({
            'index': i + 1,
            'ip': ip,
            'ua': ua,
            'utc': ts,
            'beijing': beijing_time,
            'us': ny_time,
            'location': location
        })

    return render_template('detail.html', track_id=track_id, events=formatted)

# ------------------------- 统计仪表盘 -------------------------
@app.route('/dashboard')
def dashboard():
    with sqlite3.connect(DB_FILE) as db:
        total_sends = db.execute("SELECT COUNT(DISTINCT track_id) FROM open_events").fetchone()[0]
        total_opens = db.execute("SELECT COUNT(*) FROM open_events").fetchone()[0]
        hourly = db.execute("SELECT strftime('%H', opened_at), COUNT(*) FROM open_events GROUP BY 1 ORDER BY 1").fetchall()
        top_ips = db.execute("SELECT ip, COUNT(*) FROM open_events GROUP BY ip ORDER BY 2 DESC LIMIT 5").fetchall()

    return render_template('dashboard.html', total_sends=total_sends, total_opens=total_opens,
                           hourly=hourly, top_ips=top_ips)

# ------------------------- 启动 -------------------------
if __name__ == '__main__':
    app.run(debug=True)

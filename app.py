from flask import Flask, send_file, request, g, render_template
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
DATABASE = 'tracker.db'

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute("""CREATE TABLE IF NOT EXISTS tracking (
                        id TEXT PRIMARY KEY,
                        created_at TEXT
                    )""")
        db.execute("""CREATE TABLE IF NOT EXISTS open_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        track_id TEXT,
                        opened_at TEXT,
                        ip TEXT,
                        user_agent TEXT
                    )""")
        db.commit()

@app.route("/track/<track_id>.png")
def track_pixel(track_id):
    db = get_db()
    cur = db.execute("SELECT * FROM tracking WHERE id = ?", (track_id,))
    row = cur.fetchone()
    if row is None:
        db.execute("INSERT INTO tracking (id, created_at) VALUES (?, ?)", (track_id, datetime.utcnow().isoformat()))
        db.commit()

    db.execute("INSERT INTO open_events (track_id, opened_at, ip, user_agent) VALUES (?, ?, ?, ?)", (
        track_id,
        datetime.utcnow().isoformat(),
        request.remote_addr,
        request.headers.get('User-Agent')
    ))
    db.commit()

    return send_file("static/pixel.png", mimetype="image/png")

@app.route("/admin/<track_id>")
def admin_view(track_id):
    db = get_db()
    cur = db.execute("SELECT * FROM open_events WHERE track_id = ? ORDER BY opened_at DESC", (track_id,))
    events = cur.fetchall()
    return render_template("admin.html", track_id=track_id, events=events)

if __name__ == "__main__":
    if not os.path.exists(DATABASE):
        init_db()
    app.run(host='0.0.0.0', port=5000)

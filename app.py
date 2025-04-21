from flask import Flask, request, render_template, send_file, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pytz
import io
import plotly.graph_objs as go
from collections import Counter
import geoip2.database
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tracker.db'
db = SQLAlchemy(app)

class OpenEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    track_id = db.Column(db.String(100))
    ip = db.Column(db.String(100))
    user_agent = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

@app.before_first_request
def create_tables():
    db.create_all()

@app.route('/')
def index():
    ids = db.session.query(OpenEvent.track_id).distinct().all()
    return render_template('index.html', ids=[id[0] for id in ids])

@app.route('/track/<track_id>.png')
def track_pixel(track_id):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ua = request.headers.get('User-Agent')
    event = OpenEvent(track_id=track_id, ip=ip, user_agent=ua)
    db.session.add(event)
    db.session.commit()

    # 返回透明像素图
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xFF\xFF\xFF\x21\xF9\x04\x01\x00\x00\x00\x00\x2C\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x4C\x01\x00\x3B'
    return send_file(io.BytesIO(pixel), mimetype='image/gif')

@app.route('/admin/<track_id>')
def admin_view(track_id):
    events = OpenEvent.query.filter_by(track_id=track_id).order_by(OpenEvent.timestamp.desc()).all()
    try:
        reader = geoip2.database.Reader('GeoLite2-City.mmdb')
    except:
        reader = None

    results = []
    for e in events:
        beijing = e.timestamp.replace(tzinfo=pytz.utc).astimezone(pytz.timezone('Asia/Shanghai'))
        eastern = e.timestamp.replace(tzinfo=pytz.utc).astimezone(pytz.timezone('US/Eastern'))
        geo = 'Unknown'
        if reader:
            try:
                res = reader.city(e.ip)
                geo = f"{res.country.name}, {res.subdivisions.most_specific.name}"
            except:
                pass
        results.append({
            'time_cn': beijing.strftime('%Y-%m-%d %H:%M:%S'),
            'time_us': eastern.strftime('%Y-%m-%d %H:%M:%S'),
            'ip': e.ip,
            'ua': e.user_agent,
            'geo': geo
        })
    return render_template('detail.html', track_id=track_id, results=results)

@app.route('/dashboard')
def dashboard():
    records = OpenEvent.query.all()
    ids = Counter([r.track_id for r in records])
    hours = [r.timestamp.replace(tzinfo=pytz.utc).astimezone(pytz.timezone('Asia/Shanghai')).hour for r in records]
    ip_counts = Counter([r.ip for r in records])

    bar_ids = go.Bar(x=list(ids.keys()), y=list(ids.values()), name='Opens per Tracking ID')
    bar_hours = go.Histogram(x=hours, nbinsx=24, name='Open Time (Beijing Hour)')
    pie_ip = go.Pie(labels=[ip for ip, _ in ip_counts.most_common(5)], values=[v for _, v in ip_counts.most_common(5)], name='Top IPs')

    graphs = [
        dict(id="bar_ids", figure=bar_ids),
        dict(id="bar_hours", figure=bar_hours),
        dict(id="pie_ip", figure=pie_ip)
    ]

    return render_template('dashboard.html', graphs=graphs)

if __name__ == '__main__':
    app.run(debug=True)


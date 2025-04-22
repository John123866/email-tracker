import os
import io
from datetime import datetime, timedelta

import pytz
import requests
import geoip2.database
from flask import Flask, request, send_file, render_template
from flask_sqlalchemy import SQLAlchemy

# -----------------------------------
# 基本初始化
# -----------------------------------
app = Flask(__name__)
# 从环境变量读取 Postgres 连接串
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# -----------------------------------
# 常量 & 时区
# -----------------------------------
GEO_FILE    = 'GeoLite2-City.mmdb'
GDRIVE_URL  = 'https://drive.google.com/uc?export=download&id=18SEl_i1V5zIaS1_y4bW1WYas7rxSLcwG'
cn_tz       = pytz.timezone('Asia/Shanghai')
us_tz       = pytz.timezone('America/New_York')
BLOCKED_IPS = ['38.109.126.23']     # 测试时要屏蔽的本地/内部 IP

# -----------------------------------
# 自动下载 GeoIP 数据库
# -----------------------------------
def ensure_geoip_file():
    if not os.path.exists(GEO_FILE):
        print("[INFO] GeoIP 数据库不存在，开始下载...")
        try:
            r = requests.get(GDRIVE_URL, allow_redirects=True, timeout=15)
            r.raise_for_status()
            with open(GEO_FILE, 'wb') as f:
                f.write(r.content)
            print("[SUCCESS] GeoLite2-City.mmdb 下载完成！")
        except Exception as e:
            print(f"[ERROR] 下载 GeoIP 数据库时出错: {e}")

def resolve_ip(ip: str) -> str:
    try:
        reader = geoip2.database.Reader(GEO_FILE)
        res = reader.city(ip)
        country = res.country.name or ''
        region  = res.subdivisions.most_specific.name or ''
        city    = res.city.name or ''
        return f"{country} {region} {city}".strip()
    except:
        return "未知"

ensure_geoip_file()

# -----------------------------------
# 数据模型
# -----------------------------------
class OpenEvent(db.Model):
    __tablename__ = 'open_events'
    id         = db.Column(db.Integer,   primary_key=True, autoincrement=True)
    track_id   = db.Column(db.String(128), index=True, nullable=False)
    opened_at  = db.Column(db.DateTime,  default=datetime.utcnow, nullable=False)
    ip         = db.Column(db.String(64), nullable=False)
    user_agent = db.Column(db.Text,      nullable=False)

# 首次部署时创建表
with app.app_context():
    db.create_all()

# -----------------------------------
# 路由：追踪像素
# -----------------------------------
@app.route('/track/<track_id>.png')
def track_pixel(track_id):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ua = request.headers.get('User-Agent', 'unknown')
    now = datetime.utcnow()
    # 屏蔽内部测试 IP
    if ip not in BLOCKED_IPS:
        # 24 小时内同一 track_id + ip 只记录一次
        cutoff = now - timedelta(hours=24)
        exists = OpenEvent.query\
            .filter_by(track_id=track_id, ip=ip)\
            .filter(OpenEvent.opened_at >= cutoff)\
            .first()
        if not exists:
            evt = OpenEvent(track_id=track_id, ip=ip, user_agent=ua, opened_at=now)
            db.session.add(evt)
            db.session.commit()
    # 返回 1x1 透明 PNG
    pixel = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
        b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
        b'\x1f\x15\xc4\x89\x00\x00\x00\x0cIDAT\x08\xd7c``\x00'
        b'\x00\x00\x04\x00\x01\r\n*\xb4\x00\x00\x00\x00IEND'
        b'\xaeB`\x82'
    )
    return send_file(io.BytesIO(pixel), mimetype='image/png')

# -----------------------------------
# 路由：首页（筛选 & 展示）
# -----------------------------------
@app.route('/')
def home():
    selected_id = request.args.get('filter_id', '')
    # 获取所有不同的 track_id
    all_ids = [row[0] for row in db.session.query(OpenEvent.track_id).distinct()]
    events = []
    if selected_id:
        rows = OpenEvent.query\
            .filter_by(track_id=selected_id)\
            .order_by(OpenEvent.opened_at.desc())\
            .all()
        for e in rows:
            # 转换时区并格式化
            cn = e.opened_at.replace(tzinfo=pytz.utc).astimezone(cn_tz)\
                  .strftime('%Y-%m-%d %H:%M:%S')
            us = e.opened_at.replace(tzinfo=pytz.utc).astimezone(us_tz)\
                  .strftime('%Y-%m-%d %H:%M:%S')
            loc = resolve_ip(e.ip)
            events.append({
                'time_cn': cn,
                'time_us': us,
                'ip': e.ip,
                'location': loc,
                'ua': e.user_agent
            })
    return render_template('index.html',
                           all_ids=all_ids,
                           selected_id=selected_id,
                           events=events)

# -----------------------------------
# 路由：仪表盘（全局统计）
# -----------------------------------
@app.route('/dashboard')
def dashboard():
    total_sends = db.session.query(OpenEvent.track_id).distinct().count()
    total_opens = OpenEvent.query.count()
    # 每小时打开次数分布
    hourly = db.session\
        .query(db.func.date_part('hour', OpenEvent.opened_at).label('hr'),
               db.func.count(OpenEvent.id))\
        .group_by('hr')\
        .order_by('hr')\
        .all()
    # Top 5 IP
    top_ips = db.session\
        .query(OpenEvent.ip, db.func.count(OpenEvent.id).label('cnt'))\
        .group_by(OpenEvent.ip)\
        .order_by(db.desc('cnt'))\
        .limit(5).all()
    return render_template('dashboard.html',
                           total_sends=int(total_sends),
                           total_opens=int(total_opens),
                           hourly=hourly,
                           top_ips=top_ips)

# -----------------------------------
# 启动
# -----------------------------------
if __name__ == '__main__':
    # 可根据需要调整 host/port
    app.run(host='0.0.0.0', port=5000, debug=True)

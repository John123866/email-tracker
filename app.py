import os
import io
from datetime import datetime, timedelta

import pytz
import requests
import geoip2.database
from flask import Flask, request, send_file, render_template
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, desc

# -----------------------------------
# 基本初始化
# -----------------------------------
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# -----------------------------------
# 常量 & 时区
# -----------------------------------
GEO_FILE    = 'GeoLite2-City.mmdb'
GDRIVE_URL  = 'https://drive.google.com/uc?export=download&id=18SEl_i1V5zIaS1_y4bW1WYas7rxSLcwG'
cn_tz       = pytz.timezone('Asia/Shanghai')
us_tz       = pytz.timezone('America/New_York')
BLOCKED_IPS = ['38.109.126.23']  # 你自己内部点击时的 IP

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
# 追踪像素路由
# -----------------------------------
@app.route('/track/<track_id>.png')
def track_pixel(track_id):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ua = request.headers.get('User-Agent', 'unknown')
    now = datetime.utcnow()

    if ip not in BLOCKED_IPS:
        cutoff = now - timedelta(hours=24)
        exists = OpenEvent.query\
            .filter_by(track_id=track_id, ip=ip)\
            .filter(OpenEvent.opened_at >= cutoff)\
            .first()
        if not exists:
            db.session.add(OpenEvent(track_id=track_id, ip=ip, user_agent=ua, opened_at=now))
            db.session.commit()

    pixel = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
        b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
        b'\x1f\x15\xc4\x89\x00\x00\x00\x0cIDAT\x08\xd7c``\x00'
        b'\x00\x00\x04\x00\x01\r\n*\xb4\x00\x00\x00\x00IEND'
        b'\xaeB`\x82'
    )
    return send_file(io.BytesIO(pixel), mimetype='image/png')

# -----------------------------------
# 首页：按 campaign 分组 + 显示 Email、首开时间、打开次数、UA
# -----------------------------------
@app.route('/')
def home():
    # 用户选择的前缀（campaign）
    sel_pref = request.args.get('prefix', '')

    # 1) 拿到所有 distinct track_id
    all_ids = [row[0] for row in db.session.query(OpenEvent.track_id).distinct()]

    # 2) 从每个 track_id 里提取 campaign 前缀
    prefixes = sorted({ tid.rsplit('-', 1)[0] for tid in all_ids })

    # 3) 如果选了前缀，只保留以它开头的那一批
    query = OpenEvent.query
    if sel_pref:
        query = query.filter(OpenEvent.track_id.startswith(sel_pref + '-'))

    # 4) 按 track_id 和 ip 分组，统计首开时间 & 打开次数 & UA
    #    这里只演示最简单的“取最早那次 open、计数”
    raw = query.with_entities(
        OpenEvent.track_id,
        func.min(OpenEvent.opened_at).label('first_open'),
        func.count().label('cnt'),
        func.array_agg(OpenEvent.user_agent).label('uas')
    ).group_by(OpenEvent.track_id).all()

    events = []
    for tid, first_open, cnt, uas in raw:
        # 拆 email & campaign
        campaign, email = tid.rsplit('-', 1)
        # 时区转换
        cn = first_open.replace(tzinfo=pytz.utc).astimezone(cn_tz).strftime('%Y-%m-%d %H:%M:%S')
        us = first_open.replace(tzinfo=pytz.utc).astimezone(us_tz).strftime('%Y-%m-%d %H:%M:%S')
        # 简单取第一条 UA 来做描述
        ua0 = uas[0] if uas else ''
        # 你可以自己在这里插入更复杂的 UA 解析逻辑
        ua_desc = ua0

        events.append({
            'campaign': campaign,
            'email': email,
            'time_cn': cn,
            'time_us': us,
            'count': cnt,
            'ua_desc': ua_desc
        })

    return render_template('index.html',
                           prefixes=prefixes,
                           sel_pref=sel_pref,
                           events=events)

# -----------------------------------
# 仪表盘（不变）
# -----------------------------------
@app.route('/dashboard')
def dashboard():
    total_sends = db.session.query(OpenEvent.track_id).distinct().count()
    total_opens = OpenEvent.query.count()
    hourly = db.session\
        .query(func.date_part('hour', OpenEvent.opened_at).label('hr'),
               func.count(OpenEvent.id).label('cnt'))\
        .group_by('hr')\
        .order_by('hr').all()
    top_ips = db.session\
        .query(OpenEvent.ip, func.count(OpenEvent.id).label('cnt'))\
        .group_by(OpenEvent.ip)\
        .order_by(desc('cnt'))\
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
    app.run(host='0.0.0.0', port=5000)


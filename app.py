import os, re, io
from datetime import datetime, timedelta

import pytz
import requests
import geoip2.database
from flask import Flask, request, send_file, render_template
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, and_

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
# 屏蔽我们自己查看时的 IP 段
BLOCKED_PATTERNS = [
    re.compile(r'^38\.109\.126\.'),  # 38.109.126.x
    re.compile(r'^172\.70\.'),       # 172.70.x.x
    re.compile(r'^10\.')             # 10.x.x.x
]

# -----------------------------------
# 自动下载 GeoIP 数据库
# -----------------------------------
def ensure_geoip_file():
    if not os.path.exists(GEO_FILE):
        print("[INFO] GeoIP 数据库不存在，开始下载…")
        try:
            r = requests.get(GDRIVE_URL, allow_redirects=True, timeout=15)
            r.raise_for_status()
            open(GEO_FILE,'wb').write(r.content)
            print("[SUCCESS] GeoLite2-City.mmdb 下载完成！")
        except Exception as e:
            print(f"[ERROR] 下载 GeoIP 时出错: {e}")
ensure_geoip_file()

def resolve_ip(ip: str) -> str:
    try:
        reader = geoip2.database.Reader(GEO_FILE)
        city = reader.city(ip)
        parts = [city.country.name, city.subdivisions.most_specific.name, city.city.name]
        return ' '.join(p for p in parts if p)
    except:
        return "未知"

def parse_ua(ua: str) -> str:
    browser = "Unknown Browser"
    osys    = "Unknown OS"
    # 简单提取浏览器
    if 'Firefox/' in ua:
        m = re.search(r'Firefox/([\d\.]+)', ua); browser = f"Firefox {m.group(1)}" if m else "Firefox"
    elif 'Chrome/' in ua and 'Chromium' not in ua:
        m = re.search(r'Chrome/([\d\.]+)', ua); browser = f"Chrome {m.group(1)}" if m else "Chrome"
    elif 'Safari/' in ua and 'Chrome' not in ua:
        m = re.search(r'Version/([\d\.]+)', ua); browser = f"Safari {m.group(1)}" if m else "Safari"
    # 简单提取系统
    if 'Windows NT 10.0' in ua: osys = 'Windows 10'
    elif 'Windows NT 6.1' in ua: osys = 'Windows 7'
    elif 'Mac OS X' in ua:        osys = 'macOS'
    elif 'Linux' in ua:           osys = 'Linux'
    return f"{browser} on {osys}"

# -----------------------------------
# ORM 模型
# -----------------------------------
class OpenEvent(db.Model):
    __tablename__ = 'open_events'
    id         = db.Column(db.Integer,   primary_key=True, autoincrement=True)
    track_id   = db.Column(db.String(256), index=True, nullable=False)
    opened_at  = db.Column(db.DateTime,  default=datetime.utcnow, nullable=False)
    ip         = db.Column(db.String(64), nullable=False)
    user_agent = db.Column(db.Text,      nullable=False)

# 首次运行建表
with app.app_context():
    db.create_all()

# -----------------------------------
# 路由：追踪像素
# -----------------------------------
@app.route('/track/<track_id>.png')
def track_pixel(track_id):
    ip  = request.headers.get('X-Forwarded-For', request.remote_addr)
    ua  = request.headers.get('User-Agent','')
    now = datetime.utcnow()

    # 自动清理 100 天前的数据
    delete_before = now - timedelta(days=100)
    db.session.query(OpenEvent)\
        .filter(OpenEvent.opened_at < delete_before)\
        .delete()
    db.session.commit()

    # 屏蔽内部/测试 IP
    if not any(p.match(ip) for p in BLOCKED_PATTERNS):
        # 24h 内同一 prefix+ip 只记录一次
        cutoff = now - timedelta(hours=24)
        exists = db.session.query(OpenEvent)\
            .filter_by(track_id=track_id, ip=ip)\
            .filter(OpenEvent.opened_at >= cutoff)\
            .first()
        if not exists:
            evt = OpenEvent(track_id=track_id, ip=ip, user_agent=ua, opened_at=now)
            db.session.add(evt); db.session.commit()

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
# 路由：首页（ID 前缀 + 时间范围筛选）
# -----------------------------------
@app.route('/')
def home():
    # 取出所有 raw ID，计算其 prefix
    raw_ids = [r[0] for r in db.session.query(OpenEvent.track_id).distinct()]
    # prefix = 最前面的字母数字串（@ 前、以及遇到非字母数字时截断）
    prefixes = sorted({
        re.split(r'[^0-9A-Za-z]+', rid.split('@')[0])[0]
        for rid in raw_ids
    })

    # 获取 URL 参数
    sel_pref   = request.args.get('prefix','')
    date_preset= request.args.get('preset','')  # '7', '30', '90'
    start_str  = request.args.get('start','')
    end_str    = request.args.get('end','')

    # 计算时间范围
    now = datetime.utcnow()
    if date_preset in ('7','30','90'):
        start_dt = now - timedelta(days=int(date_preset))
        end_dt   = now
    else:
        try:
            start_dt = datetime.fromisoformat(start_str)
            end_dt   = datetime.fromisoformat(end_str)
        except:
            # 默认 30 天
            start_dt = now - timedelta(days=30); end_dt = now

    # 查询匹配的事件
    query = OpenEvent.query
    if sel_pref:
        query = query.filter(OpenEvent.track_id.startswith(sel_pref))
    query = query.filter(and_(
        OpenEvent.opened_at >= start_dt,
        OpenEvent.opened_at <= end_dt
    ))

    # 聚合 IP：只记录一次，统计次数 & 首次打开
    agg = {}
    for e in query.all():
        ip = e.ip
        if any(p.match(ip) for p in BLOCKED_PATTERNS):
            continue
        if ip not in agg:
            agg[ip] = {
                'count': 0,
                'first': e.opened_at,
                'ua': parse_ua(e.user_agent),
                'location': resolve_ip(ip)
            }
        agg[ip]['count'] += 1
        if e.opened_at < agg[ip]['first']:
            agg[ip]['first'] = e.opened_at

    # 准备渲染列表
    events = []
    for ip, info in agg.items():
        cn = info['first'].replace(tzinfo=pytz.utc).astimezone(cn_tz)\
             .strftime('%Y-%m-%d %H:%M:%S')
        us = info['first'].replace(tzinfo=pytz.utc).astimezone(us_tz)\
             .strftime('%Y-%m-%d %H:%M:%S')
        events.append({
            'ip': ip,
            'location': info['location'],
            'ua_desc': info['ua'],
            'count': info['count'],
            'time_cn': cn,
            'time_us': us
        })

    return render_template('index.html',
        prefixes=prefixes,
        sel_pref=sel_pref,
        start_dt=start_dt.isoformat(),
        end_dt=end_dt.isoformat(),
        events=events,
    )

# -----------------------------------
# 路由：Dashboard（基于当前筛选结果的统计）
# -----------------------------------
@app.route('/dashboard')
def dashboard():
    # 为了演示，简化：这里取全量数据统计。若要基于筛选，可传同样的 prefix/start/end 参数。
    total_sends = db.session.query(OpenEvent.track_id).distinct().count()
    total_opens = OpenEvent.query.count()
    # 小时分布
    hourly = db.session.query(
        func.date_part('hour', OpenEvent.opened_at).label('hr'),
        func.count(OpenEvent.id)
    ).group_by('hr').order_by('hr').all()
    # Top IP
    top_ips = db.session.query(
        OpenEvent.ip, func.count(OpenEvent.id).label('cnt')
    ).group_by(OpenEvent.ip).order_by(func.count(OpenEvent.id).desc()).limit(5).all()

    return render_template('dashboard.html',
        total_sends=total_sends,
        total_opens=total_opens,
        hourly=hourly,
        top_ips=top_ips
    )

# -----------------------------------
# 启动
# -----------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)


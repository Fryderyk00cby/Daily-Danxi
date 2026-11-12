"""Loopback-only HTTP interface for the local DanXi reader."""
from contextlib import redirect_stdout, nullcontext
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import webbrowser

from . import forum as daily
from .ranking import DEFAULTS, rank_posts, fetch_replies
from .images import ImageStore, ImageError

STATIC = daily.ROOT / 'web'


def validate(raw):
    rules = dict(DEFAULTS)
    for key in rules:
        if key in raw: rules[key] = raw[key]
    for key, low, high in [('hours', 1, 168), ('pages', 1, 1000), ('limit', 1, 100), ('min_reply', 0, 100000),
                          ('reply_weight', 0, 100), ('favorite_weight', 0, 100), ('view_weight', 0, 100)]:
        rules[key] = int(rules[key])
        if not low <= rules[key] <= high: raise ValueError(f'{key} 应在 {low}–{high} 之间')
    for key in ('include', 'exclude'):
        if not isinstance(rules[key], str) or len(rules[key]) > 1000: raise ValueError('关键词过长或格式错误')
    if rules['sort'] not in ('综合热度', '回复最多', '浏览最多', '最新发布'): raise ValueError('排序方式无效')
    if not isinstance(rules['webvpn'], bool): raise ValueError('访问方式无效')
    return rules


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.rules = dict(DEFAULTS)
        try: self.rules = validate(json.loads((daily.DATA / 'reader_settings.json').read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError): pass
        self.posts = []
        self.busy = False
        self.message = '准备好了，生成你的第一份校园日报。'
        self.error = ''
        self.coverage = ''
        self.reply_result = None
        self.login_event = threading.Event()
        self.login_needed = False
        self.credentials = None
        self.stop_event = threading.Event()
        self.checkpoint = None

    def write(self, text):
        if text.strip():
            with self.lock: self.message = text.strip()

    def flush(self): pass

    def login(self):
        with self.lock:
            if self.stop_event.is_set(): raise RuntimeError('已停止当前任务。')
            self.credentials = None; self.login_event.clear(); self.login_needed = True
        if not self.login_event.wait(600):
            with self.lock: self.login_needed = False
            raise RuntimeError('等待树洞登录超时，请重新生成。')
        with self.lock:
            credentials = self.credentials; self.credentials = None
        if not credentials: raise RuntimeError('已取消树洞登录。')
        return credentials

    def save(self, rules):
        rules = validate(rules)
        daily.DATA.mkdir(exist_ok=True)
        (daily.DATA / 'reader_settings.json').write_text(json.dumps(rules, ensure_ascii=False), encoding='utf-8')
        with self.lock: self.rules = rules

    def snapshot(self):
        with self.lock:
            selected, heat = rank_posts(self.posts, self.rules)
            return dict(rules=self.rules, busy=self.busy, message=self.message, error=self.error,
                        can_resume=bool(self.checkpoint) and not self.busy,
                        login_needed=self.login_needed, coverage=self.coverage,
                        total=len(self.posts), posts=[dict(p, heat=round(heat(p), 2)) for p in selected], replies=self.reply_result)

    def stop(self):
        with self.lock:
            self.stop_event.set()
            self.message = '正在停止，等待当前请求结束，已获取内容会保留。'
            if self.login_needed:
                self.credentials = None; self.login_needed = False; self.login_event.set()

    def start(self, reply=None, resume=False):
        with self.lock:
            if self.busy: raise ValueError('任务进行中，请稍后操作。')
            self.busy = True; self.error = ''; self.reply_result = None
            self.stop_event.clear()
            checkpoint = self.checkpoint if resume else None
            rules = dict(checkpoint['rules'] if checkpoint else self.rules)
            if not reply and not resume:
                self.posts = []; self.checkpoint = None
            if not reply:
                self.checkpoint = checkpoint or dict(now=datetime.now(daily.TZ), rules=rules, posts=[], cursor=None)
            self.message = '正在继续查找…' if resume else '正在连接…'
        threading.Thread(target=self.worker, args=(rules, reply), daemon=True).start()

    def worker(self, rules, reply):
        try:
            with redirect_stdout(self):
                from .webvpn import WebVPN
                with WebVPN(self.stop_event) if rules['webvpn'] else nullcontext(None) as transport:
                    if self.stop_event.is_set(): raise RuntimeError('已停止当前任务。')
                    client = daily.Client(transport, login_callback=self.login)
                    if reply:
                        floors = fetch_replies(client, reply['id'], reply['offset'])
                    else:
                        checkpoint = self.checkpoint
                        now = checkpoint['now']
                        def progress(posts, cursor):
                            with self.lock:
                                self.posts = posts
                                self.checkpoint.update(posts=list(posts), cursor=cursor)
                                self.coverage = f'{now:%m-%d %H:%M} · 最近 {rules["hours"]} 小时新帖 · 获取中'
                        posts, complete = daily.fetch(client, now, rules['hours'], rules['pages'],
                            stop_event=self.stop_event, on_page=progress,
                            start_cursor=checkpoint['cursor'], initial_posts=checkpoint['posts'])
            with self.lock:
                if reply:
                    self.reply_result = dict(id=reply['id'], offset=reply['offset'], floors=floors)
                    self.message = '跟帖已更新'
                else:
                    self.posts = posts
                    self.coverage = f'{now:%m-%d %H:%M} · 最近 {rules["hours"]} 小时新帖 · ' + ('已到时间边界' if complete else '仅覆盖已抓取范围')
                    if self.stop_event.is_set() and not complete:
                        self.message = '已暂停查找，已获取内容保留；可点击“继续查找”。'
                    else:
                        self.checkpoint = None
                        self.message = '日报已生成，调整筛选即可即时更新。'
        except Exception as error:
            with self.lock:
                self.error = '' if self.stop_event.is_set() else (f'接口返回 HTTP {error.code}' if isinstance(error, daily.urllib.error.HTTPError) else str(error))
                self.message = '已停止，已获取内容保留，可继续查找。' if self.stop_event.is_set() else '请求失败，可检查设置后重试。'
        finally:
            with self.lock: self.busy = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def send(self, status, content, mime='application/json; charset=utf-8'):
        data = json.dumps(content, ensure_ascii=False).encode() if not isinstance(content, bytes) else content
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' blob:; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(data)

    def authorized(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}' and secrets.compare_digest(self.headers.get('X-Reader-Key', ''), self.server.key)

    def do_GET(self):
        if self.path == '/api/state':
            if not self.authorized(): return self.send(403, {'error': '无权访问'})
            return self.send(200, self.server.state.snapshot())
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/images.js': ('images.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if self.path not in files: return self.send(404, {'error': '不存在'})
        name, mime = files[self.path]
        self.send(200, (STATIC / name).read_bytes(), mime)

    def do_POST(self):
        if not self.authorized(): return self.send(403, {'error': '无权访问'})
        state = self.server.state
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 16000: raise ValueError('请求过大')
            data = json.loads(self.rfile.read(length) or '{}')
            if self.path == '/api/image':
                content, mime = self.server.images.load(data.get('url'), prefer_vpn=state.rules['webvpn'])
                return self.send(200, content, mime)
            elif self.path == '/api/login':
                with state.lock:
                    if not state.login_needed: raise ValueError('当前不需要登录')
                    state.credentials = None if data.get('cancel') else dict(email=str(data.get('email', '')).strip(), password=str(data.get('password', '')))
                    if state.credentials and not all(state.credentials.values()): raise ValueError('请输入邮箱和密码')
                    state.login_needed = False; state.login_event.set()
            elif self.path == '/api/settings': state.save(data)
            elif self.path == '/api/generate': state.start()
            elif self.path == '/api/stop': state.stop()
            elif self.path == '/api/resume':
                if not state.checkpoint: raise ValueError('没有可继续的查找')
                state.start(resume=True)
            elif self.path == '/api/replies':
                identity, offset = int(data['id']), int(data['offset'])
                if identity <= 0 or not 0 <= offset <= 100000: raise ValueError('楼层参数错误')
                state.start(dict(id=identity, offset=offset))
            elif self.path == '/api/logout':
                with state.lock:
                    if state.busy: raise ValueError('请等待当前任务完成')
                    for name in ('session.dpapi','webvpn.dpapi','webvpn.tmp'): (daily.DATA / name).unlink(missing_ok=True)
                    self.server.images.clear()
                    state.message = '登录缓存已清除'
            elif self.path == '/api/shutdown':
                if state.busy: raise ValueError('请等待任务结束，或取消登录后退出')
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else: return self.send(404, {'error': '不存在'})
            self.send(200, {'ok': True})
        except ImageError as error:
            self.send(502, {'error': str(error)})
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.send(400, {'error': str(error)})


def make_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.key = secrets.token_urlsafe(32)
    server.state = State()
    server.images = ImageStore()
    return server


def run():
    server = make_server()
    webbrowser.open(f'http://127.0.0.1:{server.server_port}/#key={server.key}')
    try: server.serve_forever()
    finally: server.server_close()

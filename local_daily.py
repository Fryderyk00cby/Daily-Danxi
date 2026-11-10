"""DanXi local daily reader. Python standard library only."""
import argparse
from contextlib import nullcontext
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import html
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'local_data'
TZ = timezone(timedelta(hours=8))
AUTH = 'https://auth.fduhole.com/api'
FORUM = 'https://forum.fduhole.com/api'
PAGE_SIZE = 10  # Match Constant.POST_COUNT_PER_PAGE in the DanXi client.


def api_time(value):
    # Dart's toUtc().toIso8601String() uses Z, not Python's +00:00 suffix.
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def protect(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def login_dialog():
    import tkinter as tk
    from tkinter import ttk, messagebox
    result = {}
    window = tk.Tk()
    window.title('树洞日报 · 登录旦夕账号')
    window.geometry('470x310')
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='使用旦夕 / 树洞账号，不是统一身份认证密码。').pack(anchor='w')
    ttk.Label(frame, text='请输入注册树洞时的邮箱（含 @ 后缀）和独立密码。').pack(anchor='w')
    ttk.Label(frame, text='邮箱').pack(anchor='w', pady=(14, 0))
    email = ttk.Entry(frame, width=48)
    email.pack()
    ttk.Label(frame, text='密码（不保存）').pack(anchor='w', pady=(8, 0))
    password = ttk.Entry(frame, show='●', width=48)
    password.pack()
    def submit():
        if not email.get().strip() or not password.get():
            messagebox.showerror('无法登录', '请输入邮箱和密码。', parent=window)
            return
        result.update(email=email.get().strip(), password=password.get())
        window.destroy()
    ttk.Button(frame, text='登录并生成日报', command=submit).pack(pady=16)
    ttk.Button(frame, text='忘记树洞密码：打开官方重置页面', command=lambda: webbrowser.open(
        'https://auth.fduhole.com/register?type=forget_password')).pack()
    window.bind('<Return>', lambda event: submit())
    email.focus_set()
    window.mainloop()
    if not result:
        raise RuntimeError('已取消登录。')
    return result


class Client:
    def __init__(self, transport=None):
        self.transport = transport
        self.token = None
        self.path = DATA / 'session.dpapi'
        if self.path.exists():
            try:
                self.token = json.loads(protect(self.path.read_bytes(), decrypt=True))
            except (OSError, ValueError):
                print('本机登录缓存无法读取，请重新登录。')

    def save(self, token):
        if not isinstance(token, dict) or not token.get('access') or not token.get('refresh'):
            raise RuntimeError('登录响应不含有效令牌，可能需要先在旦夕客户端完成验证。')
        self.token = token
        DATA.mkdir(exist_ok=True)
        self.path.write_bytes(protect(json.dumps(token).encode()))

    def request(self, url, data=None, token=None):
        if self.transport:
            return self.transport.request(url, data, token)
        headers = {'User-Agent': 'DanXiLocalDaily/0.1', 'Accept': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        body = None if data is None else json.dumps(data).encode()
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=body, headers=headers)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise

    def login(self):
        print('需要验证旦夕账号（与刚才的复旦 WebVPN 登录不同）。', flush=True)
        try:
            self.save(self.request(AUTH + '/login', login_dialog()))
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise RuntimeError('旦夕账号登录被拒绝（/login，HTTP 401）。请使用旦夕/树洞注册邮箱和独立密码，不是复旦统一认证密码。') from None
            raise RuntimeError(f'旦夕账号登录失败（/login，HTTP {error.code}），请先在客户端确认账号可正常登录。') from None

    def clear_session(self):
        self.token = None
        self.path.unlink(missing_ok=True)

    def get(self, path, params):
        if not self.token:
            self.login()
        url = FORUM + path + '?' + urllib.parse.urlencode(params)
        try:
            return self.request(url, token=self.token['access'])
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
        print('论坛未接受当前访问令牌，正在刷新登录…', flush=True)
        try:
            self.save(self.request(AUTH + '/refresh', {}, self.token['refresh']))
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
            self.clear_session()
            self.login()
            return self.get_after_login(url)
        try:
            return self.request(url, token=self.token['access'])
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
        # A refresh endpoint can issue tokens that the forum still rejects.
        # Allow one fresh login, then stop with a precise error instead of looping.
        print('刷新后的令牌仍被论坛拒绝，改为重新登录旦夕账号。', flush=True)
        self.clear_session()
        self.login()
        return self.get_after_login(url)

    def get_after_login(self, url):
        try:
            return self.request(url, token=self.token['access'])
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
            self.clear_session()
            raise RuntimeError('旦夕登录已返回令牌，但论坛仍拒绝访问（HTTP 401）。可能涉及 WebVPN 转发或账号验证；已清除无效缓存，请勿反复修改密码。') from None


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, AttributeError):
        return None


def score(post):
    return round(5 * (post.get('reply') or 0) + 3 * (post.get('favorite_count') or 0)
                 + math.log1p(max(0, post.get('view') or 0)), 2)


def fetch(client, now, hours, max_pages, stop_event=None, on_page=None, start_cursor=None, initial_posts=None):
    cutoff = now - timedelta(hours=hours)
    cursor = start_cursor or now.astimezone(timezone.utc)
    posts = {p['hole_id']: p for p in (initial_posts or [])}
    complete = False
    for page in range(max_pages):
        if stop_event is not None and stop_event.is_set():
            break
        print(f'正在获取第 {page + 1} 页…', flush=True)
        batch = client.get('/holes', {'start_time': api_time(cursor), 'length': PAGE_SIZE, 'order': 'time_created'})
        if not isinstance(batch, list):
            raise RuntimeError('帖子接口响应格式与源码不符。')
        if not batch:
            complete = True
            break
        dates = []
        for post in batch:
            created = parse_time(post.get('time_created'))
            if created is None:
                raise RuntimeError('帖子缺少有效发布时间，无法可靠分页。')
            dates.append(created)
            first = (post.get('floors') or {}).get('first_floor') or {}
            if cutoff <= created <= now and not (post.get('hidden') or post.get('time_deleted') or first.get('deleted')):
                posts[post['hole_id']] = post
        oldest = min(dates)
        if on_page is not None:
            on_page(list(posts.values()), oldest)
        if oldest < cutoff:
            complete = True
            break
        if oldest >= cursor:
            break
        cursor = oldest
        if stop_event is not None:
            if stop_event.wait(0.35): break
        else:
            time.sleep(0.35)
    return sorted(posts.values(), key=lambda p: (score(p), p['hole_id']), reverse=True), complete


def render(posts, now, hours, complete, demo=False):
    esc = html.escape
    cards = []
    for index, post in enumerate(posts[:20], 1):
        first = (post.get('floors') or {}).get('first_floor') or {}
        content = str(first.get('content') or '该帖未返回首层正文，请在旦夕客户端按洞号查看。')
        tags = ' · '.join(str(t.get('name', '')) for t in post.get('tags') or [])
        cards.append(f'''<article><div class="meta">TOP {index:02d} · 洞号 #{int(post['hole_id'])} · 热度 {score(post)}</div>
<p class="body">{esc(content)}</p><footer>{esc(tags)}<br>回复 {post.get('reply') or 0}　浏览 {post.get('view') or 0}　收藏 {post.get('favorite_count') or 0}</footer></article>''')
    status = '已扫描至时间窗口边界' if complete else '达到抓取上限或分页未前进，仅对已获取帖子排序'
    title = '演示日报 · 虚构数据' if demo else '树洞每日热议'
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{title}</title><style>
body{{margin:0;background:#f2f5f4;color:#203b35;font:16px/1.8 "Microsoft YaHei",sans-serif}}main{{max-width:860px;margin:48px auto;padding:0 22px}}h1{{font-size:38px;margin:8px 0}}.eyebrow,.meta{{color:#387e6b;font-weight:bold;letter-spacing:1px}}.note,footer{{color:#657a74;font-size:14px}}article{{background:white;border:1px solid #dce6e0;border-radius:16px;padding:25px;margin:18px 0}}.body{{white-space:pre-wrap;overflow-wrap:anywhere}}header{{margin-bottom:30px}}footer{{border-top:1px solid #edf1ee;padding-top:12px}}@media(max-width:600px){{main{{margin:24px auto}}h1{{font-size:29px}}article{{padding:18px}}}}
</style><main><header><div class="eyebrow">DANXI / LOCAL DAILY</div><h1>{title}</h1>
<div>{now.strftime('%Y-%m-%d %H:%M')}（北京时间） · 最近 {hours} 小时新发布帖子</div>
<p class="note">{status} · 共获取 {len(posts)} 条，展示前 20 条。<br>自定义热度 = 回复×5 + 收藏×3 + ln(浏览+1)，并非官方热榜。<br>正文以原始文本展示；在旦夕中搜索洞号可查看完整讨论。</p></header>
{''.join(cards) or '<article>这个时间范围内没有获取到可展示的帖子。</article>'}</main></html>'''
    DATA.mkdir(exist_ok=True)
    path = DATA / ('demo.html' if demo else 'daily.html')
    path.write_text(document, encoding='utf-8')
    return path


def main():
    parser = argparse.ArgumentParser(description='本地树洞热议日报')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--logout', action='store_true')
    parser.add_argument('--webvpn', action='store_true', help='在浏览器中登录复旦 WebVPN 后获取日报')
    parser.add_argument('--hours', type=int, default=24)
    parser.add_argument('--max-pages', type=int, default=20)
    args = parser.parse_args()
    if args.logout:
        (DATA / 'session.dpapi').unlink(missing_ok=True)
        (DATA / 'webvpn.dpapi').unlink(missing_ok=True)
        (DATA / 'webvpn.tmp').unlink(missing_ok=True)
        print('已删除本地树洞令牌及 WebVPN 会话。')
        return
    if not 1 <= args.hours <= 168 or not 1 <= args.max_pages <= 100:
        parser.error('hours 应为 1–168，max-pages 应为 1–100。')
    now = datetime.now(TZ)
    if args.demo:
        posts = [{'hole_id': 1000 + i, 'reply': 42 - i * 3, 'view': 530 - i * 12,
                  'favorite_count': 6, 'floors': {'first_floor': {'content': text}}}
                 for i, text in enumerate(['【演示】今天的校园活动有哪些？\n这里展示帖子首层正文。', '【演示】大家最近在读什么书？', '【演示】图书馆学习地点讨论'])]
        complete = True
    else:
        if args.webvpn:
            from webvpn_transport import WebVPN
            connection = WebVPN()
        else:
            connection = nullcontext(None)
        with connection as transport:
            posts, complete = fetch(Client(transport), now, args.hours, args.max_pages)
    path = render(posts, now, args.hours, complete, args.demo)
    print(f'日报已生成：{path}')
    if not args.no_open:
        webbrowser.open(path.as_uri())


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        print(f'请求失败（HTTP {error.code}）。401：登录失效；403：账号权限或访问限制；429：请求频率限制。')
        raise SystemExit(1)
    except urllib.error.URLError:
        print('HTTPS 连接失败。若旦夕开启了“自动使用 WebVPN 代理”，请双击 open_daily.cmd 使用 WebVPN 模式。')
        print('直连模式需要可访问论坛的网络；本工具不会关闭证书验证。')
        raise SystemExit(1)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'运行失败：{error}')
        raise SystemExit(1)

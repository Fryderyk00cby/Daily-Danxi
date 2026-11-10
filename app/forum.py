"""Forum authentication, Windows credential storage and paginated post retrieval."""
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
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


class Client:
    def __init__(self, transport=None, login_callback=None):
        self.transport = transport
        self.login_callback = login_callback
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
            if self.login_callback is None:
                raise RuntimeError("A login callback is required.")
            self.save(self.request(AUTH + '/login', self.login_callback()))
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


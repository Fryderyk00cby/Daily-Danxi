"""Explicit, interactive Fudan WebVPN transport; never reads client credentials."""
import json
import time
import urllib.error
import urllib.parse
import re


def validation_summary(payload):
    """Only extract validation descriptions, excluding echoed inputs and credentials."""
    messages = []
    if isinstance(payload, dict):
        for key in ('message', 'msg', 'error', 'detail'):
            value = payload.get(key)
            if isinstance(value, str):
                messages.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and isinstance(item.get('msg'), str):
                        loc = item.get('loc', [])
                        fields = '.'.join(str(x) for x in loc if x in ('query', 'start_time', 'length', 'order', 'division_id')) if isinstance(loc, list) else ''
                        messages.append(fields + ': ' + item['msg'])
    text = '；'.join(messages)
    text = re.sub(r'(?i)Bearer\s+\S+', '[令牌已隐藏]', text)
    text = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[令牌已隐藏]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+', '[邮箱已隐藏]', text)
    return ''.join(c for c in text if c.isprintable())[:400]


def translate(url):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'forum.fduhole.com', 'auth.fduhole.com'} or parsed.port:
        raise ValueError('WebVPN 仅支持预设的论坛和认证 HTTPS 地址。')
    key = b'wrdvpnisthebest!'
    host = parsed.hostname.encode('ascii')
    encryptor = Cipher(algorithms.AES(key), modes.CFB(key)).encryptor()
    encoded = (encryptor.update(host) + encryptor.finalize()).hex()
    return urllib.parse.urlunsplit(('https', 'webvpn.fudan.edu.cn',
        '/https/' + key.hex() + encoded + parsed.path, parsed.query, ''))


class WebVPN:
    def __init__(self, stop_event=None):
        self.stop_event = stop_event

    def check_stopped(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise RuntimeError('已停止当前任务。')
    def cache_path(self):
        from local_daily import DATA
        return DATA / 'webvpn.dpapi'

    def restore_session(self):
        from local_daily import protect
        path = self.cache_path()
        if not path.exists():
            return False
        try:
            cookies = json.loads(protect(path.read_bytes(), decrypt=True))
            if not isinstance(cookies, list):
                raise ValueError('Invalid cookie cache')
            cookies = [c for c in cookies if isinstance(c, dict)
                       and c.get('domain', '').lstrip('.') == 'webvpn.fudan.edu.cn']
            if not cookies:
                raise ValueError('Empty cookie cache')
            self.context.add_cookies(cookies)
            if self.probe():
                self.save_session()
                print('已恢复本机加密保存的 WebVPN 会话，无需重新登录。', flush=True)
                return True
        except Exception:
            pass
        self.context.clear_cookies()
        path.unlink(missing_ok=True)
        print('保存的 WebVPN 会话已失效或无法恢复，需要重新登录。', flush=True)
        return False

    def save_session(self):
        from local_daily import protect
        cookies = [c for c in self.context.cookies()
                   if c.get('domain', '').lstrip('.') == 'webvpn.fudan.edu.cn']
        if not cookies:
            return
        path = self.cache_path()
        path.parent.mkdir(exist_ok=True)
        encrypted = protect(json.dumps(cookies).encode('utf-8'))
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(encrypted)
        temporary.replace(path)

    def probe(self):
        response = self.context.request.get(translate('https://forum.fduhole.com/api/divisions'),
                                            timeout=15000, max_redirects=0)
        try:
            if 'json' not in response.headers.get('content-type', '') or response.status not in (200, 401):
                return False
            return isinstance(response.json(), (dict, list))
        finally:
            response.dispose()

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright, Error
        except ImportError:
            raise RuntimeError('请先运行 python -m pip install -r requirements-local.txt') from None
        self.engine = sync_playwright().start()
        self.browser = None
        try:
            for channel in ('msedge', 'chrome'):
                try:
                    self.browser = self.engine.chromium.launch(channel=channel, headless=False)
                    break
                except Error:
                    continue
            if self.browser is None:
                raise RuntimeError('无法启动 Edge 或 Chrome，请安装其中一种浏览器。')
            self.context = self.browser.new_context()
            if self.restore_session():
                return self
            self.page = self.context.new_page()
            print('请在新打开的浏览器中完成复旦 WebVPN 登录（统一身份认证）。', flush=True)
            self.page.goto('https://webvpn.fudan.edu.cn/login?cas_login=true', wait_until='domcontentloaded', timeout=60000)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                self.check_stopped()
                if self.page.is_closed():
                    raise RuntimeError('WebVPN 登录窗口已关闭。')
                # Only probe after the user returns from the login page to the VPN portal.
                current = urllib.parse.urlsplit(self.page.url)
                if current.hostname == 'webvpn.fudan.edu.cn' and not current.path.startswith('/login'):
                    if self.probe():
                        self.save_session()
                        print('WebVPN 已连通，会话已加密保存，继续获取日报。', flush=True)
                        return self
                self.page.wait_for_timeout(2000)
            raise RuntimeError('等待 WebVPN 登录超时，请重新运行。')
        except Exception as error:
            self.close()
            if isinstance(error, RuntimeError):
                raise
            raise RuntimeError('WebVPN 浏览器连接失败，请确认能打开 webvpn.fudan.edu.cn 后重试。') from None

    def request(self, url, data=None, token=None):
        headers = {'Accept': 'application/json', 'User-Agent': 'DanXiLocalDaily/0.1'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        options = dict(headers=headers, timeout=30000, max_redirects=0)
        if data is not None:
            options['data'] = json.dumps(data)
            headers['Content-Type'] = 'application/json'
        try:
            response = self.context.request.fetch(translate(url), method='GET' if data is None else 'POST', **options)
        except Exception:
            raise RuntimeError('WebVPN 请求连接中断，请重新运行并登录。') from None
        try:
            if response.status in (301, 302, 303, 307, 308):
                self.cache_path().unlink(missing_ok=True)
                raise RuntimeError('WebVPN 会话失效或发生重定向，请重新运行并登录。')
            if response.status >= 400:
                # Record only endpoint and status; never print credentials, tokens or response bodies.
                target = urllib.parse.urlsplit(url)
                print(f'接口返回 HTTP {response.status}：{target.hostname}{target.path}', flush=True)
                if response.status in (400, 422) and target.hostname == 'forum.fduhole.com' and target.path == '/api/holes':
                    try:
                        summary = validation_summary(response.json())
                    except (ValueError, TypeError):
                        summary = ''
                    if summary:
                        print('帖子列表参数校验详情：' + summary, flush=True)
                raise urllib.error.HTTPError(url, response.status, 'WebVPN API error', {}, None)
            if 'json' not in response.headers.get('content-type', ''):
                raise RuntimeError('WebVPN 返回登录页面或非 JSON 内容，请重新登录。')
            result = response.json()
            self.save_session()
            return result
        finally:
            response.dispose()

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            self.engine.stop()

    def __exit__(self, *args):
        self.close()

"""Bounded, authenticated image loading from the forum's own image host."""
from collections import OrderedDict
from http.cookiejar import Cookie, CookieJar
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import forum
from .webvpn import translate

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_CACHE_BYTES = 32 * 1024 * 1024

def image_type(content):
    """Identify raster files even when the gateway labels them text/plain."""
    if content.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if content.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if content[:6] in (b'GIF87a', b'GIF89a'): return 'image/gif'
    if content[:4] == b'RIFF' and content[8:12] == b'WEBP': return 'image/webp'
    if content[4:8] == b'ftyp':
        size = int.from_bytes(content[:4], 'big')
        if 16 <= size <= len(content):
            brands = [content[8:12]] + [content[i:i + 4] for i in range(16, size - 3, 4)]
            if any(brand in (b'avif', b'avis') for brand in brands): return 'image/avif'
    return None


class ImageError(RuntimeError):
    pass


def image_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError('图片地址格式不正确。')
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or parsed.hostname != 'image.fduhole.com'
            or parsed.username or parsed.password or parsed.port or not parsed.path.startswith('/')):
        raise ValueError('目前只支持旦夕官方图床 image.fduhole.com 的图片。')
    return urllib.parse.urlunsplit(('https', 'image.fduhole.com', parsed.path, parsed.query, ''))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Do not forward VPN cookies or fetch destinations supplied by a redirect.
        return None


def vpn_cookies():
    path = forum.DATA / 'webvpn.dpapi'
    try:
        records = json.loads(forum.protect(path.read_bytes(), decrypt=True))
        if not isinstance(records, list): raise ValueError('Invalid cache')
        jar = CookieJar()
        for record in records:
            if record.get('domain', '').lstrip('.') != 'webvpn.fudan.edu.cn': continue
            expires = record.get('expires', -1)
            if expires > 0 and expires <= time.time(): continue
            jar.set_cookie(Cookie(0, record['name'], record['value'], None, False,
                record['domain'], True, record['domain'].startswith('.'),
                record.get('path', '/'), True, True,
                int(expires) if expires > 0 else None, expires <= 0,
                None, None, {}, False))
        if not list(jar): raise ValueError('No usable VPN cookie')
        return jar
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ImageError('没有可用的 WebVPN 会话，请先生成日报并完成登录，再重试图片。') from None


def download(url, through_vpn):
    handlers = [NoRedirect()]
    if through_vpn:
        handlers.append(urllib.request.HTTPCookieProcessor(vpn_cookies()))
        url = translate(url)
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={
        'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/gif',
        'User-Agent': 'DanXiLocalDaily/0.1',
    })
    try:
        with opener.open(request, timeout=15) as response:
            length = response.headers.get('Content-Length')
            if length and int(length) > MAX_IMAGE_BYTES:
                raise ImageError('图片超过 12 MB，暂不加载。')
            content = response.read(MAX_IMAGE_BYTES + 1)
            if len(content) > MAX_IMAGE_BYTES: raise ImageError('图片超过 12 MB，暂不加载。')
            if not content: raise ImageError('服务器返回了空图片。')
            mime = image_type(content)
            if mime is None:
                raise ImageError('服务器返回的内容不是支持的图片格式，可能是网关页面或图床错误；请重试或在旦夕客户端查看。')
            return content, mime
    except urllib.error.HTTPError as error:
        if error.code == 404: raise ImageError('图片已被删除或地址不存在（404）。') from None
        if error.code in (301, 302, 303, 307, 308):
            raise ImageError('图片访问被拒绝或需要重新认证，请重新生成日报后重试。') from None
        if error.code in (401, 403) and through_vpn:
            raise ImageError('WebVPN 会话已过期或需要重新认证，请重新生成日报后重试。') from None
        raise ImageError(f'图床暂时无法访问（HTTP {error.code}），请稍后重试。') from None
    except (OSError, ValueError):
        if through_vpn:
            raise ImageError('WebVPN 图片连接失败，请检查网络或重新登录 WebVPN 后重试。') from None
        raise ImageError('图片连接失败，请检查网络。') from None


class ImageStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(3)
        self.cache = OrderedDict()
        self.size = 0
        self.epoch = 0

    def clear(self):
        with self.lock:
            self.cache.clear(); self.size = 0; self.epoch += 1

    def load(self, value, prefer_vpn=True):
        url = image_url(value)
        with self.lock:
            if url in self.cache:
                self.cache.move_to_end(url)
                return self.cache[url]
            epoch = self.epoch
        if not self.slots.acquire(timeout=20): raise ImageError('图片加载队列繁忙，请稍后重试。')
        try:
            # Honor the selected route: outside the campus network a failed VPN
            # request should not silently switch to an unusable direct connection.
            result = download(url, through_vpn=prefer_vpn)
            with self.lock:
                if self.epoch == epoch:
                    previous = self.cache.pop(url, None)
                    if previous: self.size -= len(previous[0])
                    self.cache[url] = result; self.size += len(result[0])
                    while self.size > MAX_CACHE_BYTES or len(self.cache) > 128:
                        _, removed = self.cache.popitem(last=False); self.size -= len(removed[0])
            return result
        finally:
            self.slots.release()

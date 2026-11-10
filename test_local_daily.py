import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
import tempfile
from pathlib import Path
import local_daily as d

class DailyTests(unittest.TestCase):
    def test_webvpn_encrypted_cache_restore_and_expiry(self):
        from unittest.mock import Mock
        from webvpn_transport import WebVPN
        import json
        vpn_cookie = {'name': 'vpn-session', 'value': 'fake-private-session', 'domain': 'webvpn.fudan.edu.cn', 'path': '/'}
        id_cookie = {'name': 'identity', 'value': 'do-not-store', 'domain': 'id.fudan.edu.cn', 'path': '/'}
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'DATA', Path(folder)):
            vpn = WebVPN()
            vpn.context = Mock()
            vpn.context.cookies.return_value = [vpn_cookie, id_cookie]
            vpn.save_session()
            encrypted = vpn.cache_path().read_bytes()
            self.assertNotIn(b'fake-private-session', encrypted)
            self.assertEqual(json.loads(d.protect(encrypted, True)), [vpn_cookie])
            vpn.probe = Mock(return_value=True)
            self.assertTrue(vpn.restore_session())
            vpn.context.add_cookies.assert_called_once_with([vpn_cookie])
            vpn.probe.return_value = False
            self.assertFalse(vpn.restore_session())
            self.assertFalse(vpn.cache_path().exists())
            vpn.context.clear_cookies.assert_called_once()

    def test_webvpn_corrupt_cache_returns_to_login(self):
        from unittest.mock import Mock
        from webvpn_transport import WebVPN
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'DATA', Path(folder)):
            vpn = WebVPN()
            vpn.context = Mock()
            vpn.cache_path().write_bytes(b'corrupted')
            self.assertFalse(vpn.restore_session())
            self.assertFalse(vpn.cache_path().exists())

    def test_list_parameters_match_client(self):
        from unittest.mock import Mock
        now = datetime(2026, 11, 10, 20, 30, 0, 123456, tzinfo=d.TZ)
        client = Mock()
        client.get.return_value = []
        d.fetch(client, now, 24, 20)
        client.get.assert_called_once_with('/holes', {
            'start_time': '2026-11-10T12:30:00.123456Z', 'length': 10, 'order': 'time_created'})

    def test_validation_details_exclude_echoed_input(self):
        from webvpn_transport import validation_summary
        summary = validation_summary({'detail': [{'loc': ['query', 'length'], 'msg': 'must be <= 10',
                                                   'input': {'password': 'never-display'}}]})
        self.assertEqual(summary, 'query.length: must be <= 10')
        self.assertNotIn('never-display', summary)

    def test_login_401_identifies_endpoint(self):
        from unittest.mock import Mock
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'DATA', Path(folder)):
            client = d.Client()
            client.request = Mock(side_effect=HTTPError(d.AUTH + '/login', 401, 'denied', {}, None))
            with patch.object(d, 'login_dialog', return_value={'email': 'fake', 'password': 'secret'}):
                with self.assertRaisesRegex(RuntimeError, '/login，HTTP 401') as caught:
                    client.login()
            self.assertNotIn('secret', str(caught.exception))

    def test_refresh_rejected_then_fresh_login(self):
        from unittest.mock import Mock
        from urllib.error import HTTPError
        denied = lambda: HTTPError(d.FORUM + '/holes', 401, 'denied', {}, None)
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'DATA', Path(folder)):
            client = d.Client()
            client.save({'access': 'old', 'refresh': 'old-refresh'})
            client.request = Mock(side_effect=[denied(), denied(), {'access': 'new', 'refresh': 'new-refresh'}, []])
            with patch.object(d, 'login_dialog', return_value={'email': 'fake', 'password': 'secret'}):
                self.assertEqual(client.get('/holes', {}), [])
            self.assertEqual(client.token['access'], 'new')

    def test_fresh_token_still_rejected_stops_and_clears(self):
        from unittest.mock import Mock
        from urllib.error import HTTPError
        denied = lambda: HTTPError(d.FORUM + '/holes', 401, 'denied', {}, None)
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'DATA', Path(folder)):
            client = d.Client()
            client.save({'access': 'old', 'refresh': 'old-refresh'})
            client.request = Mock(side_effect=[denied(), {'access': 'refreshed', 'refresh': 'r'}, denied(),
                                               {'access': 'new', 'refresh': 'n'}, denied()])
            with patch.object(d, 'login_dialog', return_value={'email': 'fake', 'password': 'secret'}):
                with self.assertRaisesRegex(RuntimeError, '登录已返回令牌'):
                    client.get('/holes', {})
            self.assertIsNone(client.token)
            self.assertFalse(client.path.exists())

    def test_webvpn_translation(self):
        from webvpn_transport import translate
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from urllib.parse import urlsplit
        original = 'https://forum.fduhole.com/api/holes?length=50&order=time_created'
        result = urlsplit(translate(original))
        self.assertEqual(result.hostname, 'webvpn.fudan.edu.cn')
        encrypted = result.path.split('/')[2]
        key = b'wrdvpnisthebest!'
        self.assertEqual(encrypted[:32], key.hex())
        decryptor = Cipher(algorithms.AES(key), modes.CFB(key)).decryptor()
        self.assertEqual(decryptor.update(bytes.fromhex(encrypted[32:])), b'forum.fduhole.com')
        self.assertEqual(result.query, urlsplit(original).query)
        with self.assertRaises(ValueError):
            translate('https://example.com/api')

    def test_custom_transport_receives_request(self):
        from unittest.mock import Mock
        transport = Mock()
        transport.request.return_value = {'access': 'fake'}
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'DATA', Path(folder)):
            client = d.Client(transport)
            self.assertEqual(client.request(d.AUTH + '/login', {'email': 'test'}, 'fake'), {'access': 'fake'})
        transport.request.assert_called_once_with(d.AUTH + '/login', {'email': 'test'}, 'fake')

    def test_windows_encryption(self):
        payload = b'fake-test-token'
        encrypted = d.protect(payload)
        self.assertNotIn(payload, encrypted)
        self.assertEqual(d.protect(encrypted, True), payload)

    def test_pagination_window_and_hidden(self):
        now = datetime.now(d.TZ)
        def post(i, hours, **kw):
            return dict(hole_id=i, time_created=(now-timedelta(hours=hours)).isoformat(), **kw)
        class Fake:
            def __init__(self): self.pages = iter([[post(1, 1, reply=1), post(2, 2, hidden=True)], [post(3, 3, reply=10), post(4, 25)]])
            def get(self, path, params): return next(self.pages)
        with patch.object(d.time, 'sleep'):
            posts, complete = d.fetch(Fake(), now, 24, 5)
        self.assertTrue(complete)
        self.assertEqual([p['hole_id'] for p in posts], [3, 1])

    def test_limit_and_html_escape(self):
        now = datetime.now(d.TZ)
        class Fake:
            def get(self, path, params):
                return [dict(hole_id=1, time_created=(now-timedelta(hours=1)).isoformat(), floors={'first_floor': {'content': '<script>alert(1)</script>'}})]
        with patch.object(d.time, 'sleep'):
            posts, complete = d.fetch(Fake(), now, 24, 1)
        self.assertFalse(complete)
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'DATA', Path(folder)):
            output = d.render(posts, now, 24, complete).read_text(encoding='utf8')
        self.assertNotIn('<script>', output)
        self.assertIn('&lt;script&gt;', output)
        self.assertIn('达到抓取上限', output)

if __name__ == '__main__': unittest.main()

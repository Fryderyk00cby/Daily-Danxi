import json
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from pathlib import Path
import tempfile
import local_web as web


class WebTests(unittest.TestCase):
    def test_stop_keeps_posts_and_resume_uses_cursor(self):
        from datetime import datetime, timedelta
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder, patch.object(web.daily, 'DATA', Path(folder)):
            state = web.State()
            rules = dict(web.DEFAULTS, webvpn=False, pages=3)
            now = datetime.now(web.daily.TZ)
            state.checkpoint = dict(now=now, rules=rules, posts=[], cursor=None)
            state.busy = True
            post = dict(hole_id=42, time_created=(now-timedelta(hours=1)).isoformat())
            client = Mock()
            def first_page(*args):
                state.stop()
                return [post]
            client.get.side_effect = first_page
            with patch.object(web.daily, 'Client', return_value=client): state.worker(rules, None)
            self.assertEqual(state.posts, [post])
            self.assertTrue(state.snapshot()['can_resume'])
            cursor = state.checkpoint['cursor']
            state.stop_event.clear(); state.busy = True
            client.get.side_effect = None; client.get.return_value = []
            with patch.object(web.daily, 'Client', return_value=client): state.worker(rules, None)
            self.assertEqual(client.get.call_args.args[1]['start_time'], web.daily.api_time(cursor))
            self.assertEqual(state.posts, [post])
            self.assertFalse(state.snapshot()['can_resume'])

    def test_loopback_auth_demo_and_filters(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(web.daily, 'DATA', Path(folder)):
            server = web.make_server()
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def request(path, data=None, key=None):
                headers = {'X-Reader-Key': key or server.key}
                raw = None if data is None else json.dumps(data).encode()
                with urllib.request.urlopen(urllib.request.Request(base + path, data=raw, headers=headers)) as response:
                    return response.read()
            try:
                self.assertIn(b'<!doctype html>', request('/'))
                with self.assertRaises(urllib.error.HTTPError) as error: request('/api/state', key='wrong')
                self.assertEqual(error.exception.code, 403)
                request('/api/demo', {})
                state = json.loads(request('/api/state'))
                self.assertEqual(len(state['posts']), 3)
                request('/api/settings', dict(web.DEFAULTS, min_reply=31))
                self.assertEqual(len(json.loads(request('/api/state'))['posts']), 1)
                self.assertTrue((Path(folder) / 'reader_settings.json').exists())
            finally:
                server.shutdown(); server.server_close(); worker.join()


if __name__ == '__main__': unittest.main()

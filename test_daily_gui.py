import unittest
from daily_gui import DEFAULTS, rank_posts, tags_text, fetch_replies


class RankingTests(unittest.TestCase):
    def test_tags_and_reply_pagination(self):
        from unittest.mock import Mock
        self.assertEqual(tags_text({'tags': [{'name': '学习'}, {'name': '校园'}]}), '#学习  #校园')
        self.assertEqual(tags_text({}), '暂无标签')
        client = Mock()
        client.get.return_value = [{'floor_id': 123, 'content': '跟帖'}]
        self.assertEqual(fetch_replies(client, 42, 10), client.get.return_value)
        client.get.assert_called_once_with('/holes/42/floors', {'offset': 10, 'size': 10})
        client.get.return_value = {'error': 'wrong schema'}
        with self.assertRaises(RuntimeError): fetch_replies(client, 42, 0)

    def setUp(self):
        self.posts = [dict(hole_id=1, reply=2, view=1000, favorite_count=0,
                           floors={'first_floor': {'content': '图书馆 阅读'}}),
                      dict(hole_id=2, reply=10, view=30, favorite_count=4,
                           floors={'first_floor': {'content': '食堂 推荐'}}),
                      dict(hole_id=3, reply=4, view=40, favorite_count=1,
                           tags=[{'name': '图书馆'}], floors={'first_floor': {'content': '自习 招募'}})]

    def test_include_exclude_and_minimum(self):
        rules = dict(DEFAULTS, include='图书馆，食堂', exclude='招募', min_reply=3)
        posts, _ = rank_posts(self.posts, rules)
        self.assertEqual([p['hole_id'] for p in posts], [2])

    def test_weights_sort_and_limit(self):
        posts, _ = rank_posts(self.posts, dict(DEFAULTS, reply_weight=0, favorite_weight=0, view_weight=1, limit=1))
        self.assertEqual(posts[0]['hole_id'], 1)
        posts, _ = rank_posts(self.posts, dict(DEFAULTS, sort='回复最多', limit=1))
        self.assertEqual(posts[0]['hole_id'], 2)


if __name__ == '__main__': unittest.main()

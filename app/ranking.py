"""Post filtering, ranking and reply pagination."""
import math
from . import forum as daily

DEFAULTS = dict(hours=24, pages=100, limit=20, min_reply=0, include='', exclude='',
                reply_weight=5, favorite_weight=3, view_weight=1, sort='综合热度', webvpn=True)


def body(post):
    return str(((post.get('floors') or {}).get('first_floor') or {}).get('content') or '')


def tags_text(post):
    return '  '.join('#' + str(t['name']) for t in post.get('tags') or [] if t.get('name')) or '暂无标签'


def fetch_replies(client, hole_id, offset):
    floors = client.get(f'/holes/{int(hole_id)}/floors', {'offset': offset, 'size': 10})
    if not isinstance(floors, list) or any(not isinstance(f, dict) for f in floors):
        raise RuntimeError('跟帖接口响应格式异常。')
    return floors


def rank_posts(posts, rules):
    include = rules['include'].replace('，', ',').lower().split(',')
    exclude = rules['exclude'].replace('，', ',').lower().split(',')
    def heat(p):
        return (rules['reply_weight'] * (p.get('reply') or 0)
                + rules['favorite_weight'] * (p.get('favorite_count') or 0)
                + rules['view_weight'] * math.log1p(max(0, p.get('view') or 0)))
    selected = []
    for post in posts:
        text = (body(post) + ' ' + ' '.join(str(t.get('name', '')) for t in post.get('tags') or [])).lower()
        if (post.get('reply') or 0) < rules['min_reply']:
            continue
        if any(w.strip() for w in include) and not any(w.strip() in text for w in include if w.strip()):
            continue
        if any(w.strip() in text for w in exclude if w.strip()):
            continue
        selected.append(post)
    order = rules['sort']
    def key(p):
        if order == '回复最多': return p.get('reply') or 0
        if order == '浏览最多': return p.get('view') or 0
        if order == '最新发布':
            stamp = daily.parse_time(p.get('time_created'))
            return stamp.timestamp() if stamp else 0
        return heat(p)
    return sorted(selected, key=lambda p: (key(p), p['hole_id']), reverse=True)[:rules['limit']], heat


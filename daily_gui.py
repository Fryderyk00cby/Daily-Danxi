"""Desktop reader and editable ranking rules for the local DanXi daily."""
from contextlib import redirect_stdout, nullcontext
from datetime import datetime
import json
import math
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import local_daily as daily

DEFAULTS = dict(hours=24, pages=40, limit=20, min_reply=0, include='', exclude='',
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


class LogSink:
    def __init__(self, events): self.events = events
    def write(self, text):
        if text.strip(): self.events.put(('progress', text.strip()))
    def flush(self): pass


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.posts = []
        self.snapshot = None
        self.busy = False
        self.demo = False
        root.title('树洞日刊 · DanXi Daily')
        root.geometry('1180x900')
        root.minsize(940, 850)
        root.configure(bg='#eef3f1')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#eef3f1', foreground='#203e36')
        style.configure('TButton', padding=(14, 9))
        style.configure('Accent.TButton', background='#246b55', foreground='white', font=('Microsoft YaHei UI', 11, 'bold'))
        style.map('Accent.TButton', background=[('active', '#174a3c')])
        style.configure('TEntry', padding=6, fieldbackground='white')
        style.configure('TSpinbox', padding=5, fieldbackground='white')
        header = tk.Frame(root, bg='#163e33', padx=30, pady=23)
        header.pack(fill='x')
        tk.Label(header, text='DANXI  /  DAILY READER', bg='#163e33', fg='#9dc9b7', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        tk.Label(header, text='树洞日刊', bg='#163e33', fg='white', font=('Microsoft YaHei UI', 25, 'bold')).pack(anchor='w')
        tk.Label(header, text='把校园讨论，整理成你的每日阅读。', bg='#163e33', fg='#c7ded4', font=('Microsoft YaHei UI', 10)).pack(anchor='w', pady=(4, 0))
        main = ttk.Frame(root, padding=22)
        main.pack(fill='both', expand=True)
        sidebar = ttk.Frame(main, width=272)
        sidebar.pack(side='left', fill='y', padx=(0, 24))
        sidebar.pack_propagate(False)
        rules = dict(DEFAULTS)
        try:
            saved = json.loads((daily.DATA / 'reader_settings.json').read_text(encoding='utf-8'))
            rules.update({k: saved[k] for k in DEFAULTS if k in saved})
        except (OSError, ValueError, TypeError): pass
        self.vars = {}
        ttk.Label(sidebar, text='定制你的日报', font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w', pady=(0, 5))
        for label, key, low, high in [('最近多少小时的新帖', 'hours', 1, 168), ('最多抓取页数 · 每页 10 条', 'pages', 1, 100),
                                       ('展示条数', 'limit', 1, 100), ('最低回复数', 'min_reply', 0, 100000)]:
            ttk.Label(sidebar, text=label).pack(anchor='w', pady=(5, 2))
            var = tk.StringVar(value=rules[key]); self.vars[key] = var
            ttk.Spinbox(sidebar, from_=low, to=high, textvariable=var, width=26).pack(fill='x')
        for label, key in [('包含关键词 · 任意命中，逗号分隔', 'include'), ('排除关键词 · 命中即隐藏', 'exclude')]:
            ttk.Label(sidebar, text=label).pack(anchor='w', pady=(9, 3))
            var = tk.StringVar(value=rules[key]); self.vars[key] = var
            ttk.Entry(sidebar, textvariable=var).pack(fill='x')
        ttk.Label(sidebar, text='排序方式').pack(anchor='w', pady=(10, 3))
        self.vars['sort'] = tk.StringVar(value=rules['sort'])
        ttk.Combobox(sidebar, textvariable=self.vars['sort'], values=['综合热度', '回复最多', '浏览最多', '最新发布'], state='readonly').pack(fill='x')
        ttk.Label(sidebar, text='热度权重：回复 / 收藏 / 浏览对数').pack(anchor='w', pady=(10, 3))
        weights = ttk.Frame(sidebar); weights.pack(fill='x')
        for key in ('reply_weight', 'favorite_weight', 'view_weight'):
            self.vars[key] = tk.StringVar(value=rules[key])
            ttk.Spinbox(weights, from_=0, to=100, width=6, textvariable=self.vars[key]).pack(side='left', expand=True, fill='x', padx=(0, 4))
        self.vars['webvpn'] = tk.BooleanVar(value=rules['webvpn'])
        ttk.Checkbutton(sidebar, text='通过复旦 WebVPN 访问', variable=self.vars['webvpn']).pack(anchor='w', pady=(10, 0))
        ttk.Button(sidebar, text='应用筛选并保存设置', command=self.apply).pack(fill='x', pady=(10, 4))
        ttk.Button(sidebar, text='清除登录缓存', command=self.logout).pack(fill='x')
        right = ttk.Frame(main); right.pack(side='left', fill='both', expand=True)
        toolbar = ttk.Frame(right); toolbar.pack(fill='x')
        self.generate = ttk.Button(toolbar, text='一键生成日报', style='Accent.TButton', command=self.start)
        self.generate.pack(side='left')
        self.preview = ttk.Button(toolbar, text='查看演示', command=self.show_demo); self.preview.pack(side='left', padx=10)
        self.status = tk.StringVar(value='准备好了 · 登录会话会自动恢复')
        ttk.Label(right, textvariable=self.status, wraplength=720).pack(anchor='w', pady=(15, 8))
        self.progress = ttk.Progressbar(right, mode='indeterminate'); self.progress.pack(fill='x', pady=(0, 12))
        self.canvas = tk.Canvas(right, bg='#eef3f1', highlightthickness=0)
        scroll = ttk.Scrollbar(right, command=self.canvas.yview)
        scroll.pack(side='right', fill='y'); self.canvas.pack(fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.cards = tk.Frame(self.canvas, bg='#eef3f1')
        self.card_window = self.canvas.create_window((0, 0), window=self.cards, anchor='nw')
        self.cards.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self.resize)
        self.canvas.bind_all('<MouseWheel>', lambda e: self.canvas.yview_scroll(-int(e.delta / 120), 'units'))
        self.empty('你的校园讨论阅读台', '点击“一键生成日报”获取帖子，或先查看演示。\n右侧阅读，左侧定制；筛选结果只保存在本机。')
        root.after(100, self.poll)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def resize(self, event):
        self.canvas.itemconfigure(self.card_window, width=event.width)
        for widget in self.cards.winfo_children():
            for child in widget.winfo_children():
                if isinstance(child, tk.Label): child.configure(wraplength=max(240, event.width - 60))

    def empty(self, title, subtitle):
        card = tk.Frame(self.cards, bg='white', padx=26, pady=35); card.pack(fill='x', pady=5)
        tk.Label(card, text=title, bg='white', fg='#234d3e', font=('Microsoft YaHei UI', 18, 'bold')).pack(anchor='w')
        tk.Label(card, text=subtitle, bg='white', fg='#6d8079', justify='left', font=('Microsoft YaHei UI', 11)).pack(anchor='w', pady=12)

    def settings(self):
        rules = {key: value.get() for key, value in self.vars.items()}
        for key, low, high in [('hours', 1, 168), ('pages', 1, 100), ('limit', 1, 100), ('min_reply', 0, 100000),
                              ('reply_weight', 0, 100), ('favorite_weight', 0, 100), ('view_weight', 0, 100)]:
            try: rules[key] = int(rules[key])
            except ValueError: raise ValueError('时间、页数、条数、回复数和权重需要填写整数。') from None
            if not low <= rules[key] <= high: raise ValueError(f'{key} 的范围为 {low}–{high}。')
        daily.DATA.mkdir(exist_ok=True)
        (daily.DATA / 'reader_settings.json').write_text(json.dumps(rules, ensure_ascii=False), encoding='utf-8')
        return rules

    def apply(self):
        try: rules = self.settings()
        except (ValueError, OSError) as error:
            messagebox.showerror('请检查设置', str(error), parent=self.root); return
        for child in self.cards.winfo_children(): child.destroy()
        selected, heat = rank_posts(self.posts, rules)
        if not selected: self.empty('暂时没有匹配的帖子', '可以减少最低回复数、放宽关键词，或重新生成日报。')
        width = max(350, self.canvas.winfo_width() - 60)
        for index, post in enumerate(selected, 1):
            card = tk.Frame(self.cards, bg='white', padx=24, pady=20, highlightbackground='#dce6e0', highlightthickness=1)
            card.pack(fill='x', pady=(0, 12))
            tk.Label(card, text=f"{'演示 · ' if self.demo else ''}TOP {index:02d}    /    洞号 #{post['hole_id']}    /    热度 {heat(post):.1f}", bg='white', fg='#277557', font=('Microsoft YaHei UI', 10, 'bold')).pack(anchor='w')
            tk.Label(card, text=tags_text(post), bg='#edf5ef', fg='#347355', wraplength=width, justify='left').pack(anchor='w', pady=(10, 0))
            content = body(post) or '接口未返回首层正文，请按洞号在旦夕客户端查看。'
            tk.Label(card, text=content[:700] + ('…' if len(content) > 700 else ''), bg='white', fg='#263f36', font=('Microsoft YaHei UI', 11), justify='left', wraplength=width).pack(anchor='w', pady=14)
            tk.Label(card, text=f"回复 {post.get('reply') or 0}      浏览 {post.get('view') or 0}      收藏 {post.get('favorite_count') or 0}", bg='white', fg='#71857b').pack(anchor='w')
            ttk.Button(card, text='查看正文与跟帖', command=lambda p=post: self.detail(p)).pack(anchor='e')
        self.canvas.yview_moveto(0)
        if self.snapshot:
            now, hours, complete = self.snapshot
            self.status.set(f"{'演示数据 · ' if self.demo else ''}{now:%m-%d %H:%M} · 最近 {hours} 小时新帖 · 已获取 {len(self.posts)} 条 · 展示 {len(selected)} 条 · {'已到时间边界' if complete else '仅覆盖已抓取范围'}" + (' · 时间/页数已更改，需重新生成' if rules['hours'] != hours else ''))

    def detail(self, post):
        is_demo = self.demo
        popup = tk.Toplevel(self.root); popup.title(f"洞号 #{post['hole_id']} · 讨论详情"); popup.geometry('780x680')
        toolbar = ttk.Frame(popup, padding=12); toolbar.pack(fill='x', side='bottom')
        status = tk.StringVar(value='跟帖按接口顺序加载，每次 10 层')
        ttk.Label(toolbar, textvariable=status).pack(side='left')
        text = tk.Text(popup, wrap='word', padx=24, pady=20, font=('Microsoft YaHei UI', 12), bg='#fafcfb', relief='flat')
        scroll = ttk.Scrollbar(popup, command=text.yview); scroll.pack(side='right', fill='y')
        text.configure(yscrollcommand=scroll.set); text.pack(fill='both', expand=True)
        text.tag_configure('heading', foreground='#267151', font=('Microsoft YaHei UI', 11, 'bold'))
        text.insert('end', tags_text(post) + '\n\n', 'heading')
        text.insert('end', '楼主 · 首层正文\n', 'heading')
        text.insert('end', body(post) + '\n\n'); text.configure(state='disabled')
        first = (post.get('floors') or {}).get('first_floor') or {}
        seen = {first['floor_id']} if first.get('floor_id') is not None else set()
        state = {'offset': 0, 'finished': False}
        def received(batch, error=None):
            if not popup.winfo_exists(): return
            button.configure(state='normal')
            if error:
                status.set('加载失败，可点击重试'); return
            state['offset'] += len(batch)
            added = 0
            text.configure(state='normal')
            for floor in batch:
                identity = floor.get('floor_id')
                if identity in seen: continue
                if identity is not None: seen.add(identity)
                added += 1
                name = floor.get('anonyname') or '匿名'
                timestamp = daily.parse_time(floor.get('time_created'))
                stamp = timestamp.astimezone(daily.TZ).strftime('%m-%d %H:%M') if timestamp else ''
                text.insert('end', f"{name}  ·  楼层 ID {identity or '未知'}  ·  {stamp}\n", 'heading')
                content = '该层已删除' if floor.get('deleted') else str(floor.get('content') or '（无文本内容）')
                text.insert('end', content + '\n\n')
            text.configure(state='disabled')
            # An empty page is the reliable end marker; short pages may be filtered by the server.
            state['finished'] = not batch or (not added and state['offset'] > 10)
            status.set(f"已读取 {state['offset']} 层（含首层）" + (' · 已到末尾' if state['finished'] else ''))
            button.configure(state='disabled' if state['finished'] else 'normal')
        def load():
            if self.busy:
                status.set('其他任务进行中，请稍后点击加载'); return
            if is_demo:
                received([{'floor_id': 90001, 'anonyname': '演示回复 A', 'content': '这里是第一条跟帖。'},
                          {'floor_id': 90002, 'anonyname': '演示回复 B', 'content': '支持在详情中连续阅读讨论。'}] if state['offset'] == 0 else [])
                return
            try: rules = self.settings()
            except (ValueError, OSError) as error:
                messagebox.showerror('设置错误', str(error), parent=popup); return
            button.configure(state='disabled'); status.set('正在获取跟帖…')
            self.busy = True; self.generate.configure(state='disabled'); self.preview.configure(state='disabled'); self.progress.start(12)
            threading.Thread(target=self.worker, args=(rules, (post['hole_id'], state['offset'], received)), daemon=True).start()
        button = ttk.Button(toolbar, text='加载更多跟帖', command=load); button.pack(side='right')
        load()

    def show_demo(self):
        self.demo = True
        self.posts = [dict(hole_id=1000+i, reply=42-i*9, view=500-i*60, favorite_count=6-i,
                           tags=[{'name': ['校园生活', '学习交流', '阅读'][i]}],
                           floors={'first_floor': {'content': text}}) for i, text in enumerate([
            '【演示】秋天的校园，有哪些值得散步的路线？\n今天傍晚的光线很好，想找一条安静的小路。',
            '【演示】图书馆学习搭子招募\n准备期末复习，交流一下大家的学习安排。',
            '【演示】最近读到的一本好书\n欢迎分享你的阅读清单。'])]
        self.snapshot = datetime.now(daily.TZ), 24, True
        self.apply()

    def start(self):
        if self.busy: return
        try: rules = self.settings()
        except (ValueError, OSError) as error:
            messagebox.showerror('请检查设置', str(error), parent=self.root); return
        self.busy = True; self.generate.configure(state='disabled'); self.preview.configure(state='disabled')
        self.progress.start(12); self.status.set('正在连接 · 会话失效时会打开认证窗口')
        threading.Thread(target=self.worker, args=(rules,), daemon=True).start()

    def worker(self, rules, replies=None):
        original_login = daily.login_dialog
        def request_login():
            event = threading.Event(); result = {}
            self.events.put(('login', (event, result)))
            event.wait()
            if 'error' in result: raise result['error']
            return result['credentials']
        try:
            daily.login_dialog = request_login
            with redirect_stdout(LogSink(self.events)):
                from webvpn_transport import WebVPN
                with WebVPN() if rules['webvpn'] else nullcontext(None) as transport:
                    client = daily.Client(transport)
                    if replies:
                        batch = fetch_replies(client, replies[0], replies[1])
                    else:
                        now = datetime.now(daily.TZ)
                        posts, complete = daily.fetch(client, now, rules['hours'], rules['pages'])
            if replies: self.events.put(('replies', (replies[2], batch)))
            else: self.events.put(('done', (posts, now, rules['hours'], complete)))
        except Exception as error:
            if isinstance(error, daily.urllib.error.HTTPError):
                text = f'接口返回 HTTP {error.code}，请检查账号状态或稍后重试。'
            else: text = str(error)
            self.events.put(('reply_error', (replies[2], text)) if replies else ('error', text))
        finally: daily.login_dialog = original_login

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress': self.status.set(value)
                elif kind == 'login':
                    event, result = value
                    try: result['credentials'] = self.login()
                    except Exception as error: result['error'] = error
                    finally: event.set()
                else:
                    self.busy = False; self.progress.stop()
                    self.generate.configure(state='normal'); self.preview.configure(state='normal')
                    if kind == 'done':
                        self.posts, now, hours, complete = value
                        self.demo = False; self.snapshot = now, hours, complete; self.apply()
                    elif kind == 'replies':
                        callback, batch = value
                        callback(batch)
                        self.status.set('跟帖加载完成')
                    elif kind == 'reply_error':
                        callback, error = value
                        callback([], error)
                        self.status.set('跟帖加载失败，可重试')
                        messagebox.showerror('跟帖加载失败', error, parent=self.root)
                    elif kind == 'error':
                        self.status.set('生成失败 · 可修改设置后重试')
                        messagebox.showerror('生成失败', value, parent=self.root)
        except queue.Empty: pass
        self.root.after(100, self.poll)

    def login(self):
        popup = tk.Toplevel(self.root); popup.title('登录树洞账号'); popup.geometry('440x285'); popup.transient(self.root)
        frame = ttk.Frame(popup, padding=24); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='树洞注册邮箱和独立密码', font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='与复旦统一身份认证不同，密码不保存。').pack(anchor='w', pady=(4, 14))
        email = ttk.Entry(frame); email.pack(fill='x', pady=5)
        password = ttk.Entry(frame, show='●'); password.pack(fill='x', pady=5)
        result = {}
        def submit():
            if not email.get().strip() or not password.get(): return
            result.update(email=email.get().strip(), password=password.get()); popup.destroy()
        ttk.Button(frame, text='登录并继续', style='Accent.TButton', command=submit).pack(fill='x', pady=12)
        popup.bind('<Return>', lambda e: submit()); email.focus_set(); popup.grab_set(); self.root.wait_window(popup)
        if not result: raise RuntimeError('已取消树洞登录。')
        return result

    def logout(self):
        if self.busy:
            messagebox.showinfo('正在生成', '请等待本次生成完成后再清除登录。', parent=self.root); return
        for name in ('session.dpapi', 'webvpn.dpapi', 'webvpn.tmp'): (daily.DATA / name).unlink(missing_ok=True)
        messagebox.showinfo('已清除', '树洞与 WebVPN 登录缓存已删除，下次生成需要重新登录。', parent=self.root)

    def close(self):
        if self.busy:
            messagebox.showinfo('正在生成', '请先完成或关闭认证窗口，等待本次任务结束后退出。', parent=self.root); return
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()

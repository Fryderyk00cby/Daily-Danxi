# 维护说明

## 入口和模块

`main.py` 调用 `app.server.run()`。服务仅监听 `127.0.0.1` 的随机端口，并将页面打开到默认浏览器。每次启动生成访问密钥，通过初次 URL 的 fragment 交给前端，随后从地址栏移除并放到该标签页的 `sessionStorage`；API 请求使用 `X-Reader-Key`，后端同时检查 Host。

| 模块 | 职责 |
| --- | --- |
| `app/forum.py` | `/api/login`、令牌刷新、帖子时间分页、DPAPI 加解密 |
| `app/webvpn.py` | WebVPN URL 转换、交互认证、网关 Cookie 缓存 |
| `app/ranking.py` | 默认设置、关键词匹配、热度排序、楼层分页 |
| `app/images.py` | 官方图床请求、WebVPN Cookie 复用、大小限制与内存缓存 |
| `app/server.py` | 设置校验、本地 API、后台任务、暂停进度、页面登录回调 |
| `web/app.js` | 轮询状态、展示卡片和讨论、提交设置及登录 |
| `web/images.js` | 图片语法解析、受保护的图片请求、懒加载、重试和放大 |

网页正文通过文本节点展示，不把帖子原文当作 HTML 执行。图片仅提取 URL 和替代文本，不复制原帖的 HTML 属性。`POST /api/image` 同样要求本机访问密钥，只允许官方图床地址，不跟随重定向，不对客户端返回 WebVPN Cookie。图片由浏览器 Blob URL 展示，替换内容时释放 URL。静态文件按明确的白名单提供，不对外开放整个项目目录。

图片沿用用户选择的直连/WebVPN 路由，不因 WebVPN 请求失败而自动切换网络。单张最多 12 MB，后端最多同时获取 3 张，内存缓存最多 32 MB / 128 张；图片不持久保存。该路径不会为每张图片弹出认证窗口，会话失效需要先重新生成日报完成登录。

图片响应按文件签名识别 JPEG、PNG、GIF、WebP 和 AVIF，并向浏览器返回对应 MIME。实测 WebVPN 图片响应可能标为 `text/plain`，因此不能仅凭上游 Content-Type 拒绝图片或判断会话过期；HTML 等非图片内容仍会被拒绝。

## 本地开发

先运行根目录的 `setup.cmd`。之后在根目录执行：

```powershell
.\.venv\Scripts\python.exe main.py
```

修改 HTML、CSS、JavaScript 后刷新页面；修改 Python 后，先从网页退出服务，再重新启动。响应使用 `Cache-Control: no-store`。

首次安装只配置当前项目的 `.venv`，不需要全局安装前端工具链。`setup.cmd` 会对安装进程使用 `-ExecutionPolicy Bypass`，不会永久修改系统执行策略。

## 任务和暂停

`State` 管理一次串行后台任务。帖子分页在每次请求之间检查停止事件，在收到当前页后保存内容和游标。继续时恢复原时间基准、设置与游标；退出服务后内存进度消失。

树洞登录通过传给 `Client` 的回调等待网页输入；WebVPN 通过 Playwright 打开独立浏览器。不要将账号、密码、令牌或 Cookie 写入调试输出。登录缓存位置始终相对于项目根目录，移动业务模块不会改变原缓存路径。

## 重新构建启动器

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build_launcher.ps1
```

启动器优先使用项目 `.venv\Scripts\pythonw.exe`；不存在时读取已忽略提交的 `local_data/python_path.txt`。二进制不嵌入开发者电脑的 Python 路径，也不包含 Python 或依赖。构建过程利用 Windows PowerShell 的 `Add-Type` 生成 Windows 应用程序，无需安装额外打包工具。

运行时需要保留 EXE、`main.py`、`app/` 和 `web/` 的相对位置。为别人分发时提供整个仓库，让对方运行 `setup.cmd`。无需打包 `local_data/`、`.venv/` 或参考客户端目录。

## 修改后的检查

测试文件已按项目清理要求移除。修改功能后至少检查对应流程：

1. 页面能打开，浏览器没有 JavaScript 错误；缺少密钥的 API 请求被拒绝。
2. 演示模式的卡片、昵称、标签和讨论详情显示正常。
3. 筛选和权重变化生效，刷新页面能恢复设置。
4. 停止保留当前结果，继续使用上次游标；重新生成开始新任务。
5. 真实账号下，验证登录、会话恢复、分页和跟帖。不要把个人缓存加入提交。

语法检查可运行：

```powershell
.\.venv\Scripts\python.exe -m compileall -q main.py app
```

在线可用性取决于论坛和学校网络。离线演示验证只能证明本地界面与处理流程，不代替真实接口验证。

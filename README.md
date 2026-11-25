# timelog · 时间记录小工具

> "感觉时间过得很快，但说不清今天到底干了什么。"

这个工具在后台记录你**正在用哪个软件、窗口标题是什么**（例如 `报告.docx - Word`、`需求评审 - Chrome`），
自动算出每件事花了多久，生成**每日 / 每周 Markdown 报告**。
你也可以随时手动补一条："刚才在开需求评审会，45 分钟"。

配一个**本地网页看板**，桌面双击就能开，随时看今天干了啥。

- 纯标准库，**不需要 pip install 任何东西**
- 数据全部留在本机，不联网、不上传
- 报告是纯 Markdown，可以直接贴进笔记、Obsidian、Notion

---

## 网页看板（推荐日常用法）

桌面上有个 **「时间记录看板」** 快捷方式，双击就开：

```
http://127.0.0.1:8765
```

看板分四个页：

| 页 | 内容 |
|---|---|
| **今日** | 活跃/空闲/切换次数等指标、应用排行、今天碰到的东西、逐段时间线、手动记录、观察 |
| **本周** | 每日分布条形图、本周应用排行、24 小时活跃度、周报汇总 |
| **报告** | 生成日报/周报并直接渲染成网页，可一键复制 Markdown 原文 |
| **设置** | 启停采集器、直接编辑 config.json |

顶部还可以**直接手动补一条**，也可以**一键启停采集器**。数据每 15 秒自动刷新一次。

看板只监听 `127.0.0.1`，**外部机器访问不到**，不对外暴露。

手动启动看板：

```bat
open-web.bat                          :: 双击（带控制台，关窗口即停）
python timelog.py web                 :: 同样的事
python timelog.py web --port 9000     :: 换端口
python timelog.py web --no-browser    :: 不自动开浏览器
```

> 看板和采集器是**两个独立进程**：关掉浏览器不会停采集，停采集也不影响看板。
> 看板开着的时候再点一次快捷方式，不会起第二个服务，只会重新打开浏览器。

---

## 命令行用法

```bat
start.bat          :: 双击，开始后台记录
stop.bat           :: 双击，停止记录（并顺手生成当天日报）
```

手动记一笔：

```bat
python timelog.py add "开需求评审会" --dur 45m
```

看今天的情况：

```bat
python timelog.py status
python timelog.py report daily      :: 生成今天的日报
python timelog.py report weekly     :: 生成本周周报
```

---

## 命令速查

| 命令 | 作用 |
|---|---|
| `python timelog.py start` | 前台启动监控（`Ctrl+C` 停止） |
| `python timelog.py start --bg` | 后台静默启动 |
| `python timelog.py stop` | 停止后台监控（会先收尾，再补生成当天日报） |
| `python timelog.py status` | 运行状态 + 今日概况 + 数据天数 |
| `python timelog.py web` | 启动网页看板（默认 8765 端口） |
| `python timelog.py add "做的事"` | 手动记一条（不带时长 = 一个时间点） |
| `python timelog.py add "做的事" --dur 45m` | 手动记一段，带时长 |
| `python timelog.py add "做的事" --at 21:30 --dur 25m` | 补记某个时间段 |
| `python timelog.py report daily` | 生成今天的日报 |
| `python timelog.py report daily --date 2026-11-25` | 生成指定某天的日报 |
| `python timelog.py report weekly` | 生成本周周报 |
| `python timelog.py report weekly --last` | 生成上一周周报 |
| `python timelog.py autostart install` | 装开机自启 |
| `python timelog.py autostart remove` | 卸载开机自启 |
| `python timelog.py autostart status` | 看开机自启状态 |
| `python timelog.py sync setup <仓库地址>` | 配置跨机器数据同步（首次） |
| `python timelog.py sync run` | 同步一次：拉取别人的 + 推自己的 + 重算报告 |
| `python timelog.py sync status` | 看同步状态和各机器的数据 |
| `python timelog.py sync autostart --mode install` | 装自动同步（每 30 分钟 + 登录时） |
| `python timelog.py sync autostart --mode remove` | 卸载自动同步 |
| `python timelog.py machine --name 台式机` | 给这台机器起名 |
| `python timelog.py config` | 查看当前配置 |
| `python timelog.py doctor` | 自检（能不能拿到窗口标题、目录能不能写） |
| `python timelog.py open` | 打开报告目录 |

`--dur` 支持 `30m`、`1h`、`1h30m`、`90`（默认按分钟算）。
`--at` 支持 `21:30`、`11-25 21:30`、`2026-11-25 21:30`。

---

## 两台电脑一起用

每台电脑各装一份，数据每天互相同步，报告自动合并。

### 一次性配置

1. **建一个私有数据仓库**（GitHub 上新建，名字比如 `timelog-data`）
   - ⚠️ **必须选 Private** —— 里面的窗口标题可能带客户名、文件名、聊天内容
   - 不要勾 README（要建空仓）
2. 两台电脑各跑一次：

```bat
python timelog.py sync setup https://github.com/你的用户名/timelog-data.git
python timelog.py sync run
```

首次 `sync run` 会弹浏览器让你登录 GitHub，**登录一次就记住了**，不用再登。

### 日常用法

每天收工前（或随手）跑一次：

```bat
sync.bat                    :: 等价于 python timelog.py sync run
```

它会做四件事：把自己今天的数据推上去 → 把另一台电脑的数据拉回来 → **重新生成受影响的日报/周报** → 提交推送。

跑完看板上的「今日」就是两台电脑合起来的完整画面。

### 自动同步（不用记得手动跑）

```bat
python timelog.py sync autostart --mode install --minutes 30
```

装两个触发器：

| 触发器 | 实现方式 | 说明 |
|---|---|---|
| 每 30 分钟 | Windows 计划任务 `TimelogSync` | 静默跑，**不闪黑框**（用 pythonw） |
| 每次登录 | 注册表 `HKCU\...\Run\TimelogSyncAtLogon` | 开机后先同步一次 |

> 「登录时」用注册表而不是计划任务的 onlogon 触发器 —— 后者要管理员权限，普通用户会被
> "Access is denied" 拒掉，而注册表 HKCU 不需要提权。

看状态 / 卸载：

```bat
python timelog.py sync autostart --mode status
python timelog.py sync autostart --mode remove
```

同步过程写日志到 `data/sync.log`，跑出问题先看它。

### ⚠️ 关于网络

国内访问 GitHub 时好时坏。工具为此做了四件事：

- git 子进程**不继承 stdin**（否则会卡在等凭据提示上）、带 90 秒超时
- 网络失败**自动重试 3 次**
- 数据仓库配了 `http.lowSpeedLimit`，网速太慢就放弃等下一轮
- **git 通道走不通时自动降级到 API 通道**（见下）

### 两条同步通道

| 通道 | 怎么走 | 什么时候用 |
|---|---|---|
| **git**（默认） | 本地 git 仓库 ↔ GitHub 私有仓 | 有 VPN、网络正常时。有版本历史，仓库页面能直接看 |
| **api** | 直接调 GitHub REST API 读写文件 | 不开 VPN 时（`api.github.com` 通常比 git 端口稳）。无状态，更简单 |

默认走 git，**失败会自动改走 API**，所以断网时每天的同步也不会断。想固定用哪条：

```bat
python timelog.py sync setup <仓库地址> --transport api
```

API 通道把文件直接写进同一个仓库，GitHub 那边照样能看到版本历史。

### 合并规则（重要）

- **数据文件按机器分开**：`2026-11-25.a3f9c2.jsonl`。两台电脑推同一个仓库**永远不会撞文件名**，git 不用处理任何冲突。
- **活跃时长取墙钟时间的并集**：两台机器同时在用（比如远程连着台式机）时，同一分钟只算一次，不会翻倍。
- **一台机器的结束标记不会截断另一台**：每台机器内部单独算自己的时间段，再合并。

### 给机器起名

```bat
python timelog.py machine --name "公司笔记本"
```

名字会出现在报告和看板的「设备」列里。改名字不影响历史记录。

> 换新电脑时**别把 `machine.json` 一起拷过去** —— 删掉它，新机器会自动生成新的机器号，
> 否则两台机器会用同一个机器号，数据会打架。

---

## 发布到 GitHub / 另一台电脑安装

### 发布代码（公开仓库）

```bat
publish.bat
```

脚本会：初始化 git → 检查提交身份 → 本地提交 → 提示你在 GitHub 建个**公开**仓库
（给你准备好了预填好名字的页面地址）→ 你把地址粘回来 → 推上去。

第一次推送会弹浏览器登录 GitHub。

### 另一台电脑安装

```bat
git clone https://github.com/anyizhe/timelog.git
cd timelog
python join.py --name 公司笔记本
```

`join.py` 一次搞定：起名 → 建桌面快捷方式 → 接数据仓库 → **首次同步（把另一台的数据拉回来）**
→ 装自动同步 → 装开机自启。

首次推送需要登录 GitHub，浏览器会弹窗，登录一次就记住了。

也可以用 `python setup.py` 走手动引导版（每步都问你）。

### 两个仓库，别搞混

| 仓库 | 可见性 | 装什么 |
|---|---|---|
| [`anyizhe/timelog`](https://github.com/anyizhe/timelog) | **公开** | 工具本体、README |
| `anyizhe/timelog-data` | **私有** | 每天的活动记录（**含窗口标题**） |

代码仓公开没关系，`.gitignore` 已经把 `data/`、`reports/`、`machine.json` 全排除了。
**数据仓必须私有。**

---

## 报告里有什么

**日报**（`reports/daily-2026-11-25.md`）：

1. **总览** —— 屏幕活跃时长、空闲时长、手动记录条数、切换次数
2. **应用使用排行** —— 每个软件用了多久、占比、主要窗口
3. **今天碰到的东西** —— 按停留时长排的窗口标题清单（这份就是"我今天干了啥"的答案）
4. **时间线** —— 逐段列出：几点到几点、在用什么、标题是什么
5. **手动记录** —— 你自己补的条目
6. **观察** —— 自动生成的观察，比如"比最近 7 天均值多了 30%"、"注意力集中在 XX"

**周报**（`reports/weekly-48-2026.md`）在此基础上多了：

- 每日分布条形图 + 每日明细表
- 本周应用排行、本周碰到的东西
- 24 小时活跃度（看你几点最清醒）

---

## 目录结构

```
timelog/
├── timelog.py        命令行入口
├── collector.py      前台窗口采集（Win32 API）
├── store.py          事件存储（按天 + 按机器分文件）
├── report.py         日报 / 周报生成（含跨机器合并）
├── web.py            网页看板服务 + JSON API
├── ui/index.html     看板前端（单文件，零依赖）
├── machine.py        本机身份（机器号 + 名字）
├── sync.py           跨机器数据同步（git 通道）
├── sync_api.py       跨机器数据同步（API 通道，git 走不通时自动顶上）
├── publish.py        一键发布到 GitHub
├── join.py           新电脑一键接入
├── setup.py          新电脑安装引导（手动版）
├── shortcut.py       桌面快捷方式
├── config.py         配置读取
├── config.json       配置（自己改）
├── machine.json      本机身份（本地，不进 git）
├── sync.json         同步配置（本地，不进 git）
├── clock.ico         桌面快捷方式图标
├── start.bat         双击开始记录
├── stop.bat          双击停止
├── report.bat        生成报告并打开目录
├── open-web.bat      双击打开看板
├── sync.bat          双击同步数据
├── publish.bat       双击发布到 GitHub
├── setup.bat         双击安装引导
├── autostart.bat     一键装/卸开机自启
├── data/             原始数据：2026-11-25.a3f9c2.jsonl
│   ├── 2026-11-25.a3f9c2.jsonl
│   ├── collector.log
│   ├── sync.log      每次同步的结果
│   └── web.log       看板启动异常写这里
├── reports/          生成的报告
│   ├── daily-2026-11-25.md
│   └── weekly-48-2026.md
└── tests/
    ├── selftest.py         单机自测
    ├── selftest_sync.py    多机合并 + 同步往返自测
    ├── selftest_api.py     API 通道往返自测
    └── selftest_join.py    新电脑接入端到端自测
```

---

## 网页接口

看板前端就是调这些接口（都在 `127.0.0.1` 上）：

| 接口 | 说明 |
|---|---|
| `GET /api/status` | 采集器状态、目录、已有数据天数 |
| `GET /api/day?date=YYYY-MM-DD` | 某天的完整数据（分段/应用/标题/手动记录/观察） |
| `GET /api/week?week=N` | 某周的数据（ISO 周号，缺省本周） |
| `GET /api/report?kind=daily\|weekly` | 生成并返回报告 Markdown |
| `POST /api/add` | 手动记一条 `{"note":"...","dur":"45m","at":"21:30"}` |
| `POST /api/collector/start` / `stop` | 启停采集器 |
| `GET /api/config` / `POST /api/config` | 读写配置 |

---

## 配置（config.json）

打开 `config.json` 改，保存后**下次启动监控时生效**。

| 键 | 默认 | 说明 |
|---|---|---|
| `poll_seconds` | 2 | 采样间隔（秒）。调小更精确但更费电 |
| `heartbeat_seconds` | 120 | 状态没变时的心跳间隔，用来给同一活动封顶 |
| `idle_seconds` | 600 | 超过这个时间没键鼠输入就算"空闲"（防止深夜没动鼠标被算成在用电脑） |
| `hide_title_keywords` | 密码/token/cookie 等 | 标题命中就整条隐藏成 `[已隐藏]` |
| `ignore_apps` | `[]` | 这些进程**完全不记录**（填进程名，如 `keepsecret.exe`） |
| `hide_apps` | `[]` | 这些进程只记时长、隐藏标题 |
| `report_on_stop` | true | 停止监控时顺手生成当天日报 |
| `auto_report` | true | 跨天自动补昨天日报；周一自动补上周周报 |

> `ignore_apps` / `hide_apps` 填的是**进程名**（带 `.exe`），先跑一次监控，
> 再看 `data/当天.jsonl` 里的 `app` 字段就知道进程名叫什么了。

---

## 开机自启

```bat
python timelog.py autostart install
```

装在 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`（当前用户，不需要管理员权限）。
用 `pythonw.exe` 启动，不会有黑框窗口。

```bat
python timelog.py autostart status   :: 看状态
python timelog.py autostart remove    :: 卸载
```

---

## 隐私

- 所有数据只写在本机 `data/` 目录，**不联网、不上传**
- 记录的只有：进程名 + 窗口标题 + 时间戳
- 密码类关键词默认自动遮蔽成 `[已隐藏]`
- 敏感应用请自己加进 `config.json` 的 `ignore_apps`
- 想彻底清空：`删除 data/ 目录`（报告会失去数据源）
- **开了跨机器同步之后**：数据会上传到 GitHub，所以**数据仓库必须选 Private**。
  代码仓公开没关系，`data/` 已经被 `.gitignore` 排除。
  如果某天你不想再同步，`sync.json` 里有仓库地址，删掉它就不再联网上传了。

---

## 自测

```bat
python tests/selftest.py         :: 单机：时间线数学、报告渲染、隐私遮蔽
python tests/selftest_sync.py    :: 多机合并 + 同步往返（用本地 bare 仓库模拟 GitHub）
```

自测跑在系统临时目录里，**不会碰你的 `data/`**，也不联网。

---

## 常见问题

**Q：`status` 显示「运行中」但没有数据？**
监控刚启动、还没到 2 秒的采样点，或你正待在一个标题为空的窗口里。跑 `doctor` 看能不能拿到窗口标题。

**Q：某个窗口一直不被记录？**
有些程序（游戏、部分终端）不把标题交给系统。`doctor` 会打印当前拿到的窗口，可以对比看。

**Q：报告里时间是乱的 / 有一段特别长？**
`data/` 里有损坏行会导致解析跳过。删掉当天 jsonl 重来即可；或者调小 `heartbeat_seconds`。

**Q：想换台电脑用？**
整个目录拷过去就行，`python timelog.py doctor` 确认一下。

**Q：双击快捷方式没反应 / 浏览器没打开？**
看板是用 `pythonw` 静默启动的，报错会写进 `data/web.log`。也可以用 `open-web.bat`（有控制台，能直接看到报错）再试。

**Q：端口 8765 被别的程序占了？**
`python timelog.py web --port 9000` 换端口，然后浏览器手动开 `http://127.0.0.1:9000`。

**Q：看板会一直开着吗？**
关掉浏览器页面不会停服务（它还在后台跑着）。要彻底停掉，用任务管理器结束 `pythonw.exe`，
或者下次开机后不点快捷方式就行 —— 它不是开机自启的（想要自启就用 `autostart.bat` 装采集器自启）。

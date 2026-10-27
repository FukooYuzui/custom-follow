<div align="center">

# 定制关注

### 把 B站与 YouTube 想看的博主，放在一起。

自己的视频清单 · 官方网页专注播放 · 关注连播 · 每天定时播放

[![最新版本](https://img.shields.io/github/v/release/FukooYuzui/custom-follow?label=release&color=f391b3)](https://github.com/FukooYuzui/custom-follow/releases/latest) ![Windows 10/11 x64](https://img.shields.io/badge/Windows-10%20%2F%2011%20x64-0078D4) ![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white) [![Windows 构建](https://github.com/FukooYuzui/custom-follow/actions/workflows/windows.yml/badge.svg)](https://github.com/FukooYuzui/custom-follow/actions/workflows/windows.yml)

**[下载 Windows 版](https://github.com/FukooYuzui/custom-follow/releases/latest/download/CustomFollow-Windows-x64.zip)** · **[查看最新版本](https://github.com/FukooYuzui/custom-follow/releases/latest)** · **[快速上手](#开始使用)** · **[问题反馈](https://github.com/FukooYuzui/custom-follow/issues)**

</div>

定制关注是一款 Windows 桌面程序，将你手动添加的 B站 UP 主和 YouTube 频道集中到同一份视频清单。你可以查看更新、使用官方播放器专注观看，也可以每天留出一个时间，自动播放关注博主中还没看过的视频。

清单只收录你主动选择的创作者。关注、播放计划与观看记录保存在本机。

## 界面预览

### 我的关注视频

按平台、博主、关键词或未看状态筛选，在一份清单里查看两个平台的更新。

![定制关注的视频清单示例](docs/screenshots/overview.png)

### 定时播放

选择每天固定时间，或指定日期仅播放一次；可以限定平台、某位博主及播放顺序。

![定制关注的定时播放设置示例](docs/screenshots/schedule.png)

*截图为示例界面，示例频道与播放计划不会自动加入你的程序。*

## 可以做什么

| 功能 | 使用方式 |
| --- | --- |
| **双平台关注** | B站支持昵称查找、UID 和空间主页；YouTube 支持频道主页、`@handle` 和 `UC…` 频道 ID。 |
| **官方专注播放** | 在程序的独立窗口加载官方视频网页，隐藏推荐栏、评论和信息流，保留平台提供的画质、字幕与全屏控制。 |
| **只连播我的关注** | 按打开视频时的筛选清单继续播放，支持 B站分 P 和跨平台切换，最后一条播完即停止。 |
| **定时播放未看视频** | 设置每天或单次计划，到点打开未看清单；支持先看较早发布的，或先看最新发布的。 |
| **已看 / 未看管理** | 官方视频播放到结尾后标记已看，也能在视频卡片上手动修改。 |
| **可选新视频通知** | 后台定期检查、桌面弹窗、声音和跨午夜免打扰；首次收录的旧视频不会集中弹窗。 |
| **本机保存** | 关注、设置、计划与观看记录留在本机；B站扫码凭据使用 Windows DPAPI 加密保存。 |

## 开始使用

### 1. 下载并打开

面向 **Windows 10 / 11 x64**。下载版已包含程序所需的 Python 运行环境，无需另行安装 Python。

1. 点击 **[下载 Windows 版](https://github.com/FukooYuzui/custom-follow/releases/latest/download/CustomFollow-Windows-x64.zip)**，完整解压压缩包。
2. 双击解压目录中的 `启动.cmd`，或运行 `CustomFollow/CustomFollow.exe`。
3. 请保留 `CustomFollow` 文件夹整体，程序需要其中的 `_internal` 文件夹。

首次运行的目录结构如下；`data` 文件夹由程序自动生成：

```text
定制关注/
├─ 启动.cmd
├─ 使用说明.txt
├─ README.md
└─ CustomFollow/
   ├─ CustomFollow.exe
   ├─ _internal/
   └─ data/
```

若提示无法启动，请安装或修复 **[Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)**。

### 2. 添加想看的博主

在左侧 **「关注管理」** 选择平台，输入账号信息后添加。

| 平台 | 支持的输入 | 登录说明 |
| --- | --- | --- |
| B站 | 昵称、数字 UID、`https://space.bilibili.com/数字UID` | 昵称查找后需选择正确账号。查询受限时，可使用自己的 B站 App 扫码登录。 |
| YouTube | 频道主页、`@handle`、`UC…` 频道 ID | 公开更新检查无需 Google 登录；不会自动导入 Google 账号的订阅列表。 |

首次添加会收录近期视频，后续发现新视频时按你的设置通知。添加到本程序不会改变平台账号的关注关系。

### 3. 开始观看

回到 **「我的视频」**，点击一条视频即可打开默认的 **「官方网页专注播放」** 窗口。

- 用顶部的 **「全屏」** 或 **F11** 隐藏窗口边框、菜单、工具栏和任务栏；按 **Esc** 或再按 F11 退出。
- 在官方播放器里选择可用画质、字幕、倍速等选项。
- 用顶部的 **「自动连播：开 / 关」** 控制后续播放。手动打开时，连播使用打开视频时的筛选结果。
- B站多 P 作品会先继续后续分 P，再切换下一部作品；最后一条播完停止。

自动连播用于默认的官方网页专注播放。清单页的 **「简洁嵌入播放」** 是备用方式，保留手动播放操作。

## 定时播放未看视频

例如，你可以设置：**每天 20:00，播放 B站关注博主的未看视频，较早发布的先看。**

1. 打开左侧 **「定时播放」**，开启 **「启用定时播放」**。
2. 选择 **「每天固定时间」** 并设置几点播放；也可选择指定日期仅播放一次。
3. 选择全部平台或某个平台、全部已添加博主或某位博主，再设置播放顺序。
4. 点击 **「保存定时播放」**。页面会显示下次播放时间和最近执行结果。

到点后，程序从**当前已收录的视频**中重新筛选未看作品，并打开官方播放窗口；后续是否继续播放由「自动连播」开关控制。定时连播会继续跳过已看视频和已移除博主的作品。

| 到点时的状态 | 执行方式 |
| --- | --- |
| 有符合条件的未看视频 | 打开播放窗口，按所选顺序播放。 |
| 正在播放其他视频 | 跳过本次，保留当前播放。 |
| 没有符合条件的未看视频 | 不打开播放器，记录执行结果。 |
| 单次计划已经执行 | 自动关闭该单次计划，避免重复启动。 |

> 定时播放默认关闭。启用后，到点会弹出视频窗口，并可能播放声音。程序需要保持运行，可以收进系统托盘；完全退出、电脑休眠或关机时不执行，错过时间不补播，也不会唤醒电脑。时间使用电脑本地时间。

### 「未看」如何判断

- 官方播放器的视频播放到结尾后，程序将其标记为已看；B站多 P 作品以**最后一 P 的结尾**为准。
- 提前关闭或播放失败不会自动标记已看。你可以在卡片上点击 **「标为已看」** 或 **「标为未看」**。
- 嵌入备用模式、外部浏览器中的播放，请在观看后手动标记。
- 记录仅保存在本程序，不与 B站、YouTube 的观看历史同步。
- 旧版没有记录视频是否播完，升级后的旧视频默认未看，可手动调整；原有通知处理记录会保留。

## 通知与后台运行

默认每 **5 分钟** 检查一次更新，可在设置中调整为 **3–60 分钟**，并选择桌面弹窗、提示音和免打扰时段。免打扰可以跨午夜，其间继续同步视频，暂不弹窗或响铃。

| 操作 | 程序行为 |
| --- | --- |
| 关闭主窗口 / 收起到托盘 | 停止当前播放并收起主窗口，继续检查更新和执行已保存的定时计划。 |
| 关闭播放窗口 / 返回清单 | 取消当前连播，保留后续定时计划。 |
| 在设置或托盘中选择「完全退出」 | 停止播放、更新检查和定时任务。 |
| 托盘不可用时关闭主窗口 | 直接退出程序。 |

开机后自动运行可在设置中自行开启。定时播放与更新通知分别设置；免打扰时段只控制更新通知。

## 使用范围与常见问题

<details>
<summary><strong>能看 1080P、4K 等高清视频吗？</strong></summary>

默认使用平台官方视频网页，保留官方提供的清晰度选项。可用画质取决于视频、账号、会员、地区和平台策略。程序不会解锁付费权限、跳过广告或下载视频。

B站已有扫码登录会尝试在本机复用到官方播放窗口。如播放器仍提示登录，可在该窗口自行完成。Google 可能限制嵌入式浏览器登录；如遇此情况，可在默认浏览器观看。

</details>

<details>
<summary><strong>为什么 YouTube 没更新，或发布时间显示“约”？</strong></summary>

本机网络需要能访问 YouTube。程序优先读取官方频道 Atom 更新源；不可用时，备用检查每次最多读取该频道近期 30 条公开视频，并按视频编号与顺序识别更新。历史记录会继续累计，当前所有博主合计保留最近 500 条视频。

卡片优先显示官方发布时间，并保存在本地；只取得相对时间时显示“约”，仅取得发布日期时不虚构时分，未提供时间时显示未知。程序不会用检查或收录时间代替发布时间，补正日期也不会重复提醒。

网页备用方式暂不检查独立的 Shorts、直播分栏；首次添加或长时间离线不会补齐全部历史视频。这是定期检查，更新可能受到检查间隔、网络及平台限制影响。

</details>

<details>
<summary><strong>专注窗口的布局失效怎么办？</strong></summary>

专注样式依赖官方页面结构。官网改版时，可先在播放窗口菜单中切换到完整官方页面，或回到清单使用嵌入备用模式。程序会隐藏推荐栏等常见干扰，但不能保证平台未来所有内部推荐界面都被隐藏。

请通过 [Issues](https://github.com/FukooYuzui/custom-follow/issues) 提供视频链接、平台、版本号及问题截图，方便复现。

</details>

## 数据保存与升级

项目在本机运行，没有自己的业务服务器。平台请求直接发给 B站、YouTube 及其播放器所需服务。

下载版默认使用 `CustomFollow/data`；从源码运行时使用项目根目录的 `data`：

| 文件或目录 | 内容 |
| --- | --- |
| `state.json` | 关注清单、设置、播放计划、视频与观看标记。 |
| `session.bin` | 当前 Windows 用户使用 DPAPI 加密的 B站扫码凭据。 |
| `webview/` | 官方播放器的本机浏览器数据。 |
| `app.log` | 运行问题日志，用于排查错误。 |

升级前请**完全退出旧版**，保留数据文件夹，再替换程序文件。若旁边存在旧版的 `BiliFocus/data/state.json`，新版会继续使用原数据目录，保留登录、关注和记录；不要删除旧版的数据文件夹。

反馈问题时，请勿附带 Cookie、登录二维码或整个 `data` 文件夹。仓库的 `.gitignore` 已排除个人数据与本机运行目录。

## 从源码运行

开发环境：**Windows、Python 3.12、WebView2**。前端测试使用 Node.js，持续集成使用 Node.js 22。

```powershell
git clone https://github.com/FukooYuzui/custom-follow.git
cd custom-follow
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm ci
npm test
```

构建 Windows 程序：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

脚本会检查依赖、运行测试并生成 `CustomFollow` 文件夹，保留已有的 `data`。GitHub Actions 也会执行 Windows 测试和构建，结果可在 [Actions](https://github.com/FukooYuzui/custom-follow/actions) 中查看。

### 源码结构

| 文件 | 作用 |
| --- | --- |
| `main.py` | 主程序、界面通信、后台检查及定时执行。 |
| `focus_player.py` / `native_ui.py` | 官方播放窗口、全屏与窗口生命周期。 |
| `playback_queue.py` / `playback_schedule.py` | 关注连播队列与定时未看筛选。 |
| `bili_api.py` / `youtube_api.py` | 两个平台的账号解析与更新获取。 |
| `state.py` / `windows_support.py` | 本机状态、凭据、系统托盘与开机启动。 |
| `assets/` / `tests/` | 应用界面、播放器专注样式及测试。 |

## 反馈与参考

欢迎在 [Issues](https://github.com/FukooYuzui/custom-follow/issues) 提交问题或功能建议。请尽量附上软件版本、平台、复现步骤和经过遮挡处理的截图。

- [最新下载与更新记录](https://github.com/FukooYuzui/custom-follow/releases/latest)
- [Microsoft Edge WebView2](https://developer.microsoft.com/microsoft-edge/webview2/)
- [YouTube 频道网址与 Handle](https://support.google.com/youtube/answer/6180214)
- [B站官方外链播放器](https://player.bilibili.com/)

本项目是独立的个人工具，与哔哩哔哩、YouTube 及 Google 无隶属关系。平台页面与接口可能变化，播放与访问权限以平台提供的能力为准。

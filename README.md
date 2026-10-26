# 定制关注

一个自己的跨平台观看空间：把想看的 B站和 YouTube 博主放进同一份清单，在这里浏览和观看。

## 功能

- **双平台关注**：B站支持昵称、UID、空间主页链接；YouTube 支持频道主页、`@handle`、`UC…` 频道 ID。
- **可选通知**：托盘后台检查、桌面弹窗、声音开关、跨午夜免打扰、失败退避与重复提醒去重。
- **专注观看**：使用平台官方网页播放器，隐藏推荐栏、评论与页面信息流，保留画质、字幕和全屏选项。
- **本地保存**：关注列表与设置保存在本机，B站扫码凭据由 Windows DPAPI 加密。公开的 YouTube 更新检查不需要 Google 登录。

![定制关注的双平台视频列表](docs/screenshots/overview.png)

上图为示例界面，示例频道不会自动加入你的关注列表。

## 运行

面向 Windows 10/11 x64，需要 Microsoft Edge WebView2 Runtime。

下载 Releases 中的 Windows 压缩包，完整解压后双击 `启动.cmd`。请保留 `CustomFollow` 文件夹整体。

1. 在“关注管理”选择 **B站** 或 **YouTube**。
2. 添加想看的创作者。B站查询遇到限制时，可在程序中扫码登录。
3. 首次添加只收录近期视频；之后发现新视频才弹窗提醒。
4. 点击视频进入官方网页专注播放；可切换完整页面或嵌入备用模式。
5. 关闭主窗口会收进托盘；托盘菜单可以完全退出。

检查依赖本机网络能够访问相应平台。程序不会改变代理、地区、登录或付费权限。

## YouTube 更新方式与边界

优先使用 YouTube 官方频道 Atom 更新源。若官方更新源不可用，读取**该频道的公开视频页**，最多保留近期 30 条，并用新视频编号及最新顺序识别更新。网页只提供相对发布时间时，界面会保留近似时间提示。

网页备用方式暂不检查独立的 Shorts、直播分栏；首次导入和长时间离线也不会补齐全部历史视频。修改视频标题不会造成同一视频重复提醒。

YouTube 画质、字幕、广告、地区和账号权限由官方播放器决定。Google 可能限制嵌入式浏览器登录；如遇此情况，可用默认浏览器观看。此程序不会自动导入 Google 账号里的订阅列表。

## 从源码运行

安装 Python 3.12；Windows PowerShell 中运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm ci
npm test
```

构建 Windows 应用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

输出在 `CustomFollow`。重新构建不会删除已有 `data` 文件夹。

## 本机数据与升级

新安装默认保存在 `CustomFollow/data`。从早期 BiliFocus 版本升级时，如果旁边存在 `BiliFocus/data/state.json`，新版会继续使用原数据文件夹，保留登录与关注。请完全退出旧版再启动新版，避免两个实例同时运行。

`state.json` 包含个人关注与观看标记，`session.bin` 是当前 Windows 用户加密的登录凭据，`webview` 是平台浏览器数据。它们都不应上传到仓库或分享给他人；本仓库的 `.gitignore` 已排除这些目录。

项目只在本机运行，没有自己的业务服务器。平台请求直接发给 B站、YouTube 及其播放器所需服务。

## 资料

- [YouTube 官方更新通知说明](https://developers.google.com/youtube/v3/guides/push_notifications)
- [YouTube 频道网址与 Handle](https://support.google.com/youtube/answer/6180214)
- [B站官方外链播放器](https://player.bilibili.com/)

网站页面与接口会变化。若专注布局失效，可先切换完整官方页面，再反馈具体视频链接和错误；请勿附带 Cookie、登录二维码或整个 `data` 文件夹。

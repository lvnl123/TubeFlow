<div align="center">

# 🎬 TubeFlow

### YouTube 桌面下载器 · PySide6 + yt-dlp + FFmpeg

<p>
  <img alt="Platform" src="https://img.shields.io/badge/平台-Windows-1f6feb?style=for-the-badge&logo=windows">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python">
  <img alt="UI" src="https://img.shields.io/badge/界面-PySide6-2B9348?style=for-the-badge&logo=qt">
  <img alt="Engine" src="https://img.shields.io/badge/引擎-yt--dlp-FF6B35?style=for-the-badge&logo=youtube">
  <img alt="FFmpeg" src="https://img.shields.io/badge/合并-FFmpeg-6A4C93?style=for-the-badge&logo=ffmpeg">
  <img alt="License" src="https://img.shields.io/badge/许可证-MIT-yellow?style=for-the-badge">
  <img alt="Version" src="https://img.shields.io/badge/版本-v1.0-blue?style=for-the-badge">
  <img alt="Status" src="https://img.shields.io/badge/状态-积极维护-brightgreen?style=for-the-badge">
</p>

<p>
  <strong>链接解析 → 格式筛选 → 音视频分离/合并 → 双引擎自动回退</strong><br>
  <sub>不只是 yt-dlp 的图形壳 — 而是一个体验完整的桌面级下载工具</sub>
</p>

</div>

---

## 📖 目录

- [为什么选择 TubeFlow](#-为什么选择-tubeflow)
- [功能一览](#-功能一览)
- [快速开始](#-快速开始)
- [安装说明](#-安装说明)
- [使用指南](#-使用指南)
- [下载引擎策略](#-下载引擎策略)
- [技术栈](#-技术栈)
- [项目结构](#-项目结构)
- [工作流程](#-工作流程)
- [高级设置](#-高级设置)
- [常见问题](#-常见问题)
- [二次开发](#-二次开发)
- [免责声明](#-免责声明)

---

## ✨ 为什么选择 TubeFlow

| 场景 | 直接用 yt-dlp 命令行 | 用 TubeFlow |
|------|---------------------|-------------|
| 只想快速下个视频 | 要记参数、查文档 | 粘贴链接 → 点两下 |
| 看看有哪些格式可选 | `-F` 刷出一大串 | 表格清晰展示，自动筛选常用格式 |
| 下 4K 视频带声音 | 手动查音频格式 ID → 手动合并 | 自动匹配音频 + FFmpeg 合并 |
| 下载失败 | 自己排查、换参数试 | 双引擎自动回退，日志面板直接看原因 |
| 想只下音频/BGM | 翻帮助找 `-f ba` 之类 | 切到"单独音频"分页就行 |
| 代理/Cookie 配置 | 每次命令行拼参数 | 设置面板里填好，自动生效 |

> **TubeFlow 的核心理念**：把 yt-dlp 的强大能力装进一个普通用户看得懂的桌面界面里，让下载这件事从"敲命令"变成"点按钮"。

---

## 🎯 功能一览

### 🔍 两阶段智能解析

| 阶段 | 获取内容 | 耗时 |
|------|---------|------|
| **第一阶段** | 标题、作者、时长、封面 | 秒级 |
| **第二阶段** | 完整格式列表（视频轨 + 音频轨 + 合并方案） | 按需加载 |

> 先让你确认"是不是这条视频"，再加载详细格式 — 避免白等。

### 🎥 三种下载模式

| 模式 | 你得到什么 | 适用场景 |
|------|-----------|---------|
| 🎬 **单独视频** | 纯画面，无声 | 剪辑素材、动图制作、后期配音 |
| 🎵 **单独音频** | 纯声音，无画面 | 音乐保存、播客提取、音频转文字 |
| 🎬🔊 **音视频** ⭐ | 完整视频，有声有画 | 日常下载、4K 收藏、离线观看 |

> 音视频模式下，你只需选想要的画质，程序自动匹配最优音轨并调用 FFmpeg 合并。

### 🧠 双引擎 + 智能回退

```
CLI 引擎 (yt-dlp.exe)  ──失败──▶  Python 引擎 (yt_dlp 库)
        │                                    │
        └────────── 自动模式下优先尝试 ─────────┘
```

- **自动模式**：优先 CLI，失败自动切 Python
- **强制 CLI**：直接调用 exe，速度更快
- **强制 Python**：兼容性更稳，兜底首选

针对 SABR 格式缺失、`--js-runtimes` 参数不兼容等真实场景做了专项兼容。

### 🛠 更多实用特性

- 📋 **格式表格**：分辨率、帧率、编码、码率、预估大小一目了然，支持排序
- � **关键词筛选**：输入 `1080` / `mp4` / `m4a` 等关键词，快速过滤格式列表
- ⚡ **快捷下载**：一键下载推荐画质音视频 / 最佳音质音频
- �🔄 **后台下载**：多线程执行，界面不卡顿，随时暂停/继续/取消
- 🌐 **代理支持**：HTTP/HTTPS/SOCKS5 代理 + 一键连通性测试
- 🍪 **Cookie 支持**：文件导入 + Chrome/Edge 浏览器 Cookie 读取
- 📝 **实时日志**：下载进度、合并过程、错误原因全在底部面板
- 💾 **状态记忆**：记住保存目录、历史链接、偏好设置

---

## 🚀 快速开始

```powershell
# 1. 克隆项目
git clone https://github.com/你的用户名/TubeFlow.git
cd TubeFlow

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动
python run.py
```

> ⚠️ 音视频合并功能需要本机安装 [FFmpeg](https://ffmpeg.org/download.html) 并加入系统 PATH。

---

## 📦 安装说明

### 环境要求

| 组件 | 最低版本 | 推荐版本 |
|------|---------|---------|
| Windows | 10 | 10 / 11 |
| Python | 3.9 | 3.10+ |
| FFmpeg | 任意 | 最新稳定版 |
| yt-dlp | 2025.3.31 | 最新版 |

### 安装 FFmpeg

音视频合并依赖 FFmpeg。请确保以下任一条件满足：

- `ffmpeg.exe` 已加入系统环境变量 PATH
- 或项目运行时能够自动检测到 FFmpeg 路径

> 没有 FFmpeg 时，单独视频 / 单独音频下载仍可正常使用。

---

## 📘 使用指南

### 典型操作流程

```
① 粘贴 YouTube 链接
        ↓
② 点击「快速解析」
   ├─ 第一阶段：秒出标题、作者、时长
   └─ 第二阶段：自动加载完整格式表格
        ↓
③ 切换到对应分页（视频 / 音频 / 音视频）
        ↓
④ 选择想要的格式 & 保存目录
        ↓
⑤ 点击「下载」→ 底部日志区实时反馈
```

### 三个分页详解

| 分页 | 操作 | 输出 |
|------|------|------|
| **单独视频** | 选一个视频格式 | `.mp4` / `.webm`（无声） |
| **单独音频** | 选一个音频格式 | `.m4a` / `.opus`（纯音频） |
| **音视频** ⭐ | 选一个视频画质 | `.mp4`（自动合入音频） |

---

## ⚙️ 下载引擎策略

TubeFlow 内置两条下载链路，针对不同场景智能调度：

| 引擎 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **CLI** | 调用 `yt-dlp.exe` | 接近原生能力、启动快 | 版本敏感、格式可能不全 |
| **Python** | 内嵌 `yt_dlp` 库 | 兼容性稳、格式更全 | 略慢、受 Python 环境限制 |

### 回退逻辑

1. 🟢 自动模式下优先尝试 CLI
2. 🟡 CLI 不可用或格式缺失时自动回退 Python
3. 🔴 手动选 CLI 但下载失败时 → 兜底尝试 Python

> 这个策略极大提升了"最终能下下来"的成功率。

### 已处理的真实问题

- ✅ 旧版 `yt-dlp.exe` 不支持 `--js-runtimes` → 自动探测并跳过
- ✅ YouTube SABR 策略导致部分格式缺失 → 切换引擎重新拉取
- ✅ CLI 模式返回的格式与解析阶段不一致 → 自动回退重试

---

## 🔧 高级设置

在左侧面板 →「高级设置 / 诊断」按钮中可以配置：

| 设置项 | 说明 |
|--------|------|
| 🌐 **代理** | HTTP/HTTPS 代理地址 + 连通性测试 |
| 🍪 **Cookie** | 导入 `cookies.txt` 或读取 Chrome / Edge 浏览器 Cookie |
| ⚙️ **引擎模式** | 自动 / 强制 CLI / 强制 Python |
| 📊 **环境摘要** | 查看当前 yt-dlp 路径、FFmpeg 状态、JS runtime、代理、Cookie 状态 |

---

## 🧱 技术栈

| 技术 | 用途 |
|------|------|
| [PySide6](https://pypi.org/project/PySide6/) | 桌面 GUI 框架 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | YouTube 解析 & 下载核心 |
| [FFmpeg](https://ffmpeg.org/) | 音视频合并 & 转封装 |
| QThread / Python 线程 | 后台任务，保持界面流畅 |

---

## 📁 项目结构

```
TubeFlow/
├── src/
│   └── youtube_downloader/
│       ├── __init__.py       # 包入口
│       ├── app.py            # GUI 界面 & 交互逻辑
│       ├── downloader.py     # 下载调度 & 引擎回退 & FFmpeg 合并
│       ├── helper_cli.py     # Python 引擎兼容子进程
│       └── models.py         # 数据模型（格式项、媒体方案、元信息）
├── run.py                    # 程序入口
├── requirements.txt          # Python 依赖
├── README.md                 # 本文件
└── .gitignore                # Git 忽略规则
```

> ℹ️ `app_state.json` 不在源码中，首次运行时会自动生成，用于保存用户偏好设置和最近链接。已通过 `.gitignore` 排除。

---

## 🔄 工作流程

```mermaid
flowchart LR
    A["🔗 输入链接"] --> B["⚡ 快速解析\n标题 / 作者 / 时长"]
    B --> C["📋 加载格式列表\n视频轨 + 音频轨"]
    C --> D["🎯 选择下载模式\n视频 / 音频 / 音视频"]
    D --> E["🚀 CLI 引擎下载"]
    E --> F{"成功？"}
    F -- ✅ 是 --> G["📦 保存 / FFmpeg 合并"]
    F -- ❌ 否 --> H["🔄 Python 引擎回退"]
    H --> G
    G --> I["📝 日志反馈"]
```

---

## ❓ 常见问题

<details>
<summary><strong>🔹 提示 "Requested format is not available"</strong></summary>

当前选择的格式在该下载链路中不可用。通常是 YouTube SABR 策略或 CLI/Python 引擎返回格式不一致导致。

**解决方法**：
1. 切换引擎模式（CLI ↔ Python）
2. 重新点击"加载格式"刷新列表
3. 更新 yt-dlp：`pip install -U yt-dlp`
</details>

<details>
<summary><strong>🔹 高清视频下载后没有声音</strong></summary>

这不是 bug。YouTube 高清资源（1080p+）的视频轨和音频轨是分开存放的。

**正确做法**：使用「音视频」模式，程序会自动匹配合适的音频轨并调用 FFmpeg 合并。
</details>

<details>
<summary><strong>🔹 提示 `--js-runtimes` 不支持</strong></summary>

本机 `yt-dlp.exe` 版本过旧。TubeFlow 已内置参数探测，不支持时自动跳过，通常不影响正常下载。建议升级 yt-dlp。
</details>

<details>
<summary><strong>🔹 Python 3.9 出现 Deprecated Feature 提示</strong></summary>

yt-dlp 对旧版 Python 的兼容警告，当前功能不受影响。建议升级到 Python 3.10+ 以获得更好的长期支持。
</details>

<details>
<summary><strong>🔹 某些链接无法解析</strong></summary>

**排查顺序**：
1. 换一个公开视频试试 → 判断是否链接本身的问题
2. 检查代理是否可用 → 用内置连通性测试
3. 检查 Cookie 是否正确 → 重新导入或读取浏览器 Cookie
4. 更新 yt-dlp → `pip install -U yt-dlp`
</details>

---

## 🧪 二次开发

如果你准备继续打磨这个项目，以下方向值得探索：

| 优先级 | 方向 | 说明 |
|--------|------|------|
| ⭐⭐⭐ | 批量下载队列 | 多链接排队下载 |
| ⭐⭐⭐ | 下载历史面板 | 已下载记录、重新下载 |
| ⭐⭐⭐ | PyInstaller 打包 | 一键打包成 exe |
| ⭐⭐ | 下载完成通知 | 系统托盘通知 / 声音提示 |
| ⭐⭐ | 文件命名模板 | 自定义输出文件名规则 |
| ⭐⭐ | 国际化 (i18n) | 英文界面支持 |
| ⭐ | 自动更新检查 | GitHub Release 版本比对 |
| ⭐ | 更多浏览器 Cookie | Firefox、Brave 等 |

欢迎提交 Issue 和 Pull Request！🫶

---

## ⚠️ 免责声明

本项目仅供 **学习、研究和个人技术交流** 使用。

- 请在遵守当地法律法规、YouTube 服务条款及版权要求的前提下使用
- 使用者应自行承担因使用本工具而产生的所有责任与风险
- 本项目不鼓励、不支持任何形式的侵权或违规使用行为

---

<div align="center">

**如果这个项目对你有帮助，欢迎给个 ⭐ Star！**

<sub>Made with ❤️ by a developer who just wanted an easier way to download videos</sub>

</div>

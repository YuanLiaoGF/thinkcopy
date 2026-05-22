# ThinkCopy

一套基于 DeepSeek API 的桌面端剪贴板智能工具。复制任意文本，右侧悬浮窗自动弹出 AI 处理结果——无需切换窗口，无需任何手动操作。

## 工具列表

| 工具 | 文件 | 功能 |
|------|------|------|
| 事实核查 | `clipboard_overlay.py` | 联网搜索核实剪贴板内容的真实性，指出正确与错误之处 |
| 智能翻译 | `clipboard_translate.py` | 自动识别语种，非中文 → 中文，中文 → 英文 |

## 特性

### 核心功能
- **自动监测剪贴板**：后台轮询系统剪贴板，复制新内容后自动触发处理，无需任何手动操作
- **AI 驱动**：调用 DeepSeek API（`deepseek-v4-flash` 模型），响应迅速
- **右侧悬浮窗**：结果在屏幕右侧以半透明悬浮窗口展示，不遮挡主工作区
- **Markdown 渲染**：内置轻量级 Markdown 解析器，支持标题（H1-H3）、粗体、斜体、行内代码、无序列表等样式

### 工程特性
- **零外部依赖**：仅使用 Python 标准库（`tkinter`、`urllib`、`json`、`subprocess`、`re`），无需 `pip install` 任何包
- **跨平台**：Windows / macOS / Linux 均可运行
- **轻量**：每个脚本仅约 220 行，单文件即可运行
- **窗口半透明**：窗口透明度 85%，与桌面融合自然

## 环境要求

- Python 3.8+
- Windows / macOS / Linux
- DeepSeek API Key（[免费注册获取](https://platform.deepseek.com/)）

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/你的用户名/thinkcopy.git
cd thinkcopy
```

### 2. 配置 API Key

打开要运行的脚本，将 `API_KEY` 的值替换为你的 DeepSeek API Key：

```python
API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

两个脚本各自独立配置，互不影响。

### 3. 运行

```bash
# 事实核查
python clipboard_overlay.py

# 智能翻译
python clipboard_translate.py
```

两个工具可以**同时运行**，各自独立窗口，互不干扰。

复制任意文本，右侧即弹出 AI 处理结果。

## 架构详解

### 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                      系统剪贴板                          │
└──────────────────────┬──────────────────────────────────┘
                       │ 定时轮询 (3s)
                       ▼
┌─────────────────────────────────────────────────────────┐
│                  get_clipboard()                         │
│  Windows: PowerShell Get-Clipboard                      │
│  macOS/Linux: tkinter.clipboard_get()                   │
└──────────────────────┬──────────────────────────────────┘
                       │ 文本内容
                       ▼
┌─────────────────────────────────────────────────────────┐
│              ClipboardOverlay 主循环                      │
│  refresh() ──► 检测变化 ──► 调用 API ──► 渲染结果       │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP POST (JSON)
                       ▼
┌─────────────────────────────────────────────────────────┐
│               DeepSeek API                               │
│  POST /chat/completions                                  │
│  Model: deepseek-v4-flash                                │
└──────────────────────┬──────────────────────────────────┘
                       │ Markdown 文本
                       ▼
┌─────────────────────────────────────────────────────────┐
│               render_md()                                │
│  逐行解析：标题 / 列表 / 粗体 / 斜体 / 行内代码          │
│  输出到 tkinter Text 组件                                │
└─────────────────────────────────────────────────────────┘
```

### 剪贴板读取 (`get_clipboard`)

- **Windows**：通过 `subprocess` 调用 `powershell Get-Clipboard`，稳定可靠，支持 Unicode
- **macOS / Linux**：使用 tkinter 内置的 `clipboard_get()`，无需外部进程

### 轮询机制 (`refresh`)

- 使用 `root.after()` 递归调度，不阻塞 GUI 主循环
- 剪贴板有内容时每 **3 秒** 检查一次，无内容时降低频率至 **0.5 秒**
- 通过 `last_content` 变量去重，同一内容不会重复处理
- 通过 `evaluating` / `translating` 标志位防止并发请求

### Markdown 渲染 (`render_md`)

内置轻量级 Markdown 解析器，分两层处理：

**块级解析 (`_parse_block`)：**
| 语法 | 效果 |
|------|------|
| `# 标题` | H1，15px 粗体 |
| `## 标题` | H2，13px 粗体 |
| `### 标题` | H3，11px 粗体 |
| `- 项目` / `* 项目` | 无序列表，`•` 前缀 |

**行内解析 (`_insert_inline`)：**
| 语法 | 效果 |
|------|------|
| `**粗体**` | 粗体字 |
| `*斜体*` | 斜体字 |
| `` `代码` `` | Consolas 等宽字体，粉红色，灰色背景 |

### API 调用参数对比

| 参数 | 事实核查 | 智能翻译 |
|------|---------|---------|
| `model` | `deepseek-v4-flash` | `deepseek-v4-flash` |
| `search` | `True`（联网搜索） | 不启用 |
| `max_completion_tokens` | 300 | 2000 |
| `temperature` | 0.3 | 0.3 |
| `timeout` | 30s | 30s |

- **事实核查**启用 `search: True`，让模型联网搜索最新信息来核实内容真实性；限制 300 token 确保评估简洁
- **智能翻译**不启用搜索以降低延迟；2000 token 允许输出较长翻译结果

### 窗口配置差异

| 属性 | 事实核查 | 智能翻译 |
|------|---------|---------|
| 窗口标题 | 剪贴板评估 | 剪贴板翻译 |
| `-topmost` | `False` | `True`（始终置顶） |
| `-alpha`（透明度） | 0.85 | 0.85 |
| 宽度 | 360px | 360px |

## 项目结构

```
thinkcopy/
├── clipboard_overlay.py    # 事实核查工具（~225 行）
├── clipboard_translate.py  # 智能翻译工具（~224 行）
├── .gitattributes          # Git 换行符规范化配置
└── README.md               # 项目文档
```

## 自定义

### 修改轮询间隔

在 `refresh()` 方法中调整 `next_check` 值（单位：毫秒）：

```python
# 有内容时默认 3000ms，可改为 2000ms 提高响应速度
next_check = 2000 if content else 500
```

### 修改窗口宽度

在 `__init__()` 中修改 `self.sidebar_width`：

```python
self.sidebar_width = 420  # 默认 360
```

### 修改窗口透明度

在 `__init__()` 中修改 alpha 值（0.0 完全透明 ~ 1.0 完全不透明）：

```python
self.root.wm_attributes("-alpha", 0.9)
```

### 切换 API 模型

在 `call_deepseek()` / `call_translate()` 中修改 `model` 字段：

```python
"model": "deepseek-chat",  # 替换为其他模型
```

### 添加更多 API 配置

```python
# 环境变量方式（更安全，避免 API Key 硬编码在代码中）
import os
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
```

## 常见问题

**Q: 窗口一闪而过？**  
A: 确保已正确填写 API Key，且网络可访问 `api.deepseek.com`。终端会显示 API 调用失败的详细信息。

**Q: 中文显示乱码？**  
A: 确保系统已安装 `Microsoft YaHei UI` 字体（Windows 自带）。其他系统可替换为对应中文字体。

**Q: 翻译窗口总在最前遮挡内容？**  
A: 翻译版默认 `-topmost = True`（置顶），改为 `False` 即可让其他窗口覆盖它。

**Q: 可以同时运行两个工具吗？**  
A: 可以。两个脚本独立运行，各开一个悬浮窗，互不影响。

## 许可证

MIT License

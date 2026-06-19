# AiNiee-Next

<div align="center">
  <img src="https://img.shields.io/badge/Interface-CLI%20%2F%20TUI-0078D4?style=for-the-badge&logo=windows-terminal&logoColor=white" alt="CLI">
  <img src="https://img.shields.io/badge/Runtime-uv-purple?style=for-the-badge&logo=python&logoColor=white" alt="uv">
  <img src="https://img.shields.io/badge/Status-Stable-success?style=for-the-badge" alt="Status">
</div>

<br/>

[简体中文](README.md) | [English](README_EN.md) | [繁體中文](Docs/README_zh_CNTW.md) | [日本語](Docs/README_JA.md) | [한국어](Docs/README_KO.md) | [Русский](Docs/README_RU.md) | [Español](Docs/README_ES.md)

**AiNiee-Next** 是针对 [AiNiee](https://github.com/NEKOparapa/AiNiee) 核心逻辑进行工程化重构的命令行版本。

本项目引入了现代化的 Python 包管理工具 **uv**，并对底层运行时进行了多项稳定性优化。通过接管底层 IO 流与异常处理，构建了一个适合长时间挂机、服务器部署及自动化工作流的高健壮性 TUI 环境。

---

## 内置智能诊断与问题反馈辅助

AiNiee-Next 内置了错误诊断与反馈整理能力，用户可以放心使用：即使任务运行中出现异常，也不会只留下难懂的报错和无从下手的日志。系统会收集错误栈、运行环境、当前平台与模型、最近操作流程等上下文，并通过规则诊断与可选 LLM 分析，辅助判断问题更可能来自 API、网络、配置、环境，还是项目代码本身。

如果诊断结果指向疑似代码问题，程序还可以自动整理出规整化的 GitHub Issue：包含错误描述、环境信息、关键 traceback、初步分析与定位线索。您无需花大量精力组织问题描述，也不必自己解读复杂报错；系统会自动帮您把问题表述成开发者更容易理解、复现和修复的格式，让反馈更轻松，问题也更容易被快速处理。

- **出错有方向**：辅助区分配置问题、环境问题、API 问题、网络问题与疑似代码缺陷
- **反馈更省心**：自动整理包含环境、版本、错误栈和分析结果的 Issue 内容
- **沟通成本更低**：减少用户反复补充信息、开发者反复追问上下文的时间
- **问题更易处理**：让问题报告更接近可直接定位的调试材料，方便开发者快速排查

---

## 性能展示

**本项目为极致的性能释放和稳定性而生。**

下图展示了一个约 20,000 行的待翻译文件，在 50 并发线程下仅用约 4 分钟即可完成翻译任务：

<div align="center">
  <img src="README_IMG/50并发deepseek测试.png" alt="50并发性能测试" width="90%">
  <br>
  <em>50 并发 + DeepSeek API | 20k 行 | ~4 分钟完成 | 99.6% 成功率 | 397k TPM</em>
</div>

---

## 核心特性

### 运行时稳定性
- **IO 流清洗与接管**：重构标准输出流捕获逻辑，屏蔽底层依赖库冗余日志，防止 TUI 界面撕裂或崩溃
- **智能错误恢复**：内置异常拦截与自动重试机制，支持断点续传，适合长时间挂机运行
- **跨平台兼容**：支持 Windows / Linux / macOS / Android (Termux)，Headless 服务器友好

### 智能格式处理
- **全自动格式转换**：支持 .mobi / .azw3 / .kepub / .fb2 等格式的"识别 - 转换 - 翻译 - 还原"闭环
- **多格式原生支持**：Epub、Docx、Txt、Srt、Ass、Vtt、Lrc、Json、Po、Paratranz 等 20+ 格式
- **Calibre 中间件集成**：自动调用 Calibre 处理复杂电子书格式

### 实时任务控制中心
- **动态并发调整**：通过 `+` / `-` 键实时增减并发线程数
- **API Key 热切换**：通过 `K` 键强制触发 API Key 轮换，应对限流
- **任务中途监控**：通过 `M` 键启动 WebServer 并自动打开浏览器
- **系统状态监控**：底部状态栏实时显示运行状态，边框颜色联动
- **成本与时间预估**：任务启动前自动预估 Token 消耗、API 费用及完成时间

### 多配置文件系统
- **Profile 隔离存储**：支持创建、克隆、切换多套配置方案
- **场景化配置**：可区分"快速翻译"与"精细润色"等不同场景
- **配置热重载**：修改配置后无需重启即可生效

### 插件化架构
- **模块化扩展**：无需修改核心代码即可安全扩展功能
- **内置 RAG 插件**：自动检索历史译文，为长篇内容提供上下文参考，提升术语和风格一致性
- **翻译检查插件**：自动检测漏译、错译、格式异常等问题
- **集中化管理**：CLI 主菜单和 Web UI 均提供插件管理页面

### 智能任务队列
- **批量任务配置**：预先配置多个不同文件或翻译策略的任务
- **动态队列调度**：支持拖拽排序（Web）和键盘交互重排（TUI）
- **任务热修改**：队列执行中可实时修改待处理任务参数
- **自动顺序执行**：适合大批量翻译工作流

### 上下文缓存
- **多平台支持**：Anthropic / Google / Amazon Bedrock 上下文缓存
- **费用优化**：缓存系统提示词和术语表，显著降低 API 调用费用
- **智能降级**：自动检测 API 兼容性，不支持时自动关闭并提示

### 思考模式增强
- **全平台兼容**：支持所有主流在线 API 平台及第三方中转站
- **智能参数配置**：为在线 API 和本地模型提供不同的兼容性提示
- **深度推理支持**：支持 DeepSeek R1、Claude 3.5 等模型的深度思考模式

### API 故障转移
- **多 API 池管理**：支持配置多个备用 API
- **自动切换**：主 API 失败时自动切换到备用 API
- **阈值控制**：可配置故障转移触发阈值

### 高并发性能释放
- **异步请求模式**：基于 aiohttp 的异步 I/O，突破线程池瓶颈，支持 100+ 并发
- **智能错误分类**：区分"硬伤错误"（格式/认证问题）与"软伤错误"（限流/超时），硬伤不重试，软伤智能等待
- **Provider 指纹记录**：自动检测并记录各 API 的功能支持情况，下次启动静默降级
- **信号量保护**：高并发时保护本地系统资源（文件描述符、端口数），确保稳定运行
- **自动提示**：当并发数 ≥15 时，自动建议启用异步模式以获得更好性能

---

## 快速开始

> 新用户建议先阅读：[图文快速上手教程](Docs/README_QUICK_START.md)；还没有 API Key 的用户可先看：[DeepSeek API Key 申请教程](Docs/DEEPSEEK_API_KEY.md)；想提升翻译质量可继续看：[提示词、术语表、润色与软件设置教程](Docs/TRANSLATION_WORKFLOW_GUIDE.md)

### 方式一：一键启动（推荐）

**1. 获取代码**
```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
```

**2. 环境准备（首次运行）**

Windows:
```batch
双击 prepare.bat
```

Linux / macOS:
```bash
chmod +x prepare.sh && ./prepare.sh
```

**3. 启动应用**

Windows:
```batch
双击 Launch.bat
```

Linux / macOS:
```bash
./Launch.sh
```

---

### 方式二：手动配置

**1. 安装 uv**

Windows (PowerShell):
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Linux / macOS:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Android (Termux):
```bash
pkg update && pkg upgrade
pkg install python
pip install uv
```

**2. 获取代码并启动**
```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
uv run ainiee_cli.py
```

---

## 命令行参数

支持通过命令行参数直接启动任务，适用于脚本集成与自动化。

**翻译任务示例：**
```bash
uv run ainiee_cli.py translate input.txt -o output_dir -p MyProfile -s Japanese -t Chinese --resume --yes
```

**队列任务示例：**
```bash
uv run ainiee_cli.py queue --queue-file my_queue.json --yes
```

**MCP 服务示例：**
```bash
uv run ainiee_cli.py mcp --mcp-transport stdio
```

**主要参数：**
- `translate` / `polish` / `export` / `queue` / `mcp`: 任务类型
- `-o, --output`: 输出路径
- `-p, --profile`: 配置 Profile 名称
- `-s, --source`: 源语言
- `-t, --target`: 目标语言
- `--type`: 项目类型 (Txt, Epub, MTool, RenPy 等)
- `--resume`: 自动恢复缓存任务
- `--yes`: 非交互模式
- `--threads`: 并发线程数
- `--platform`: 目标平台
- `--model`: 模型名称
- `--api-url`: API 地址
- `--api-key`: API 密钥
- `--mcp-transport`: MCP 传输模式，可选 `stdio` / `streamable-http` / `sse`

---

## Web 控制面板

本项目集成基于 React 构建的 Web 控制面板，已进入稳定阶段。

**启动方式：**
1. 运行 `uv run ainiee_cli.py` 进入主菜单
2. 选择 **15. Start Web Server**
3. 程序将自动启动服务（默认端口 8000）并打开浏览器

**功能：**
- 可视化看板：实时图表展示 RPM、TPM 及任务进度
- 网络访问：支持局域网远程监控
- 配置管理：网页端创建、切换配置 Profile
- 队列管理：拖拽排序、实时编辑任务参数
- 插件中心：启用/禁用 RAG 等高级功能

> **开发说明**：Web 控制面板已稳定运行，但功能相对 TUI 模式较少。本项目以 CLI/TUI 交互为核心开发方向，Web 端功能更新将在后续版本中逐步跟进。

---

## MCP 服务

本项目提供可选的 MCP 服务模块，复用现有 WebServer 后端能力，并尽量覆盖全部 Web API 路由，以便在 MCP 客户端中获得接近 Web 面板的操作体验。
任何支持 MCP `stdio` 或 `streamable-http` 的 LLM 客户端，都可以直接接入本项目，不需要额外读取项目源码或手动拼接 Web API。

**启动方式：**
1. 命令行直启：`uv run ainiee_cli.py mcp --mcp-transport stdio`
2. 主菜单启动：进入主菜单后选择 **16. 启动 MCP 服务**

**说明：**
- MCP 服务是可选组件，缺失时不会影响主程序其他功能
- 每次启动 MCP 前都会检查必要组件与依赖
- 若缺少依赖，程序会提示当前系统可直接执行的完整安装命令
- 菜单启动默认使用后台 `streamable-http` 模式，等待 3 秒后返回菜单
- 如果修改了 `mcp_server_port`，请同步更新 MCP 客户端中的连接路由

**直接接入 LLM 客户端：**
1. 支持 `stdio` 的 MCP 客户端，可以直接把 AiNiee CLI 作为本地 MCP Server 接入。
如果客户端使用 `command + args` 配置格式，可参考下面这个通用模板：

```json
{
  "mcpServers": {
    "ainiee-cli": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "H:\\小说\\AiNiee-CLI",
        "--isolated",
        "--no-project",
        "--quiet",
        "--with",
        "mcp",
        "--with",
        "fastapi",
        "--with",
        "uvicorn[standard]",
        "--with",
        "requests",
        "python",
        "Tools/MCPServer/server.py",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

不同客户端的配置文件字段名可能略有差异，但核心信息通常就是 `command=uv` 加上上面的 `args`。
上面的路径请替换成你自己的项目目录。Linux / macOS 可把 `H:\\小说\\AiNiee-CLI` 替换成 `/path/to/AiNiee-CLI`。

2. 如果客户端只接受“原始命令”，可直接使用：

```bash
uv run --directory /path/to/AiNiee-CLI --isolated --no-project --quiet --with mcp --with fastapi --with uvicorn[standard] --with requests python Tools/MCPServer/server.py --transport stdio
```

3. Codex 通过 `stdio` 直连时，推荐直接使用项目内置 launcher：

```bash
codex mcp add ainiee-cli -- /path/to/AiNiee-CLI/Tools/MCPServer/codex_stdio_launcher.sh
```

首次启动如果依赖尚未缓存，建议在 `~/.codex/config.toml` 中给该 MCP 增加较大的超时，例如：

```toml
[mcp_servers.ainiee-cli]
startup_timeout_sec = 90
```

4. 支持 `streamable-http` 的 MCP 客户端，可以直接连接 AiNiee CLI 暴露出来的 MCP HTTP 路由。
先启动：

```bash
uv run ainiee_cli.py mcp --mcp-transport streamable-http
```

或者在主菜单选择 **16. 启动 MCP 服务**。

客户端侧如果使用 URL 配置格式，可参考：

```json
{
  "mcpServers": {
    "ainiee-cli": {
      "transport": "streamable-http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

连接地址：

```text
本机地址: http://127.0.0.1:8765/mcp
局域网地址: http://<你的局域网IP>:8765/mcp
```

5. 如果启动 MCP 时提示缺少依赖，可以在项目根目录执行：

```bash
set "UV_PROJECT_ENVIRONMENT=%CD%\.venv-win" && uv --directory "%CD%" add "mcp" "fastapi" "uvicorn[standard]" "requests"
```

Linux / macOS 可使用：

```bash
UV_PROJECT_ENVIRONMENT="$(pwd)/.venv" uv --directory "$(pwd)" add 'mcp' 'fastapi' 'uvicorn[standard]' 'requests'
```

如果你把 `mcp_server_port` 改成了其他值，上面的 `8765` 也要同步替换。
如果项目目录里的 `.venv` 曾经在另一套系统下创建过，例如 WSL 生成后又在 Windows 下执行 `uv add`，建议先重建 `.venv`，否则容易出现 `lib64` / 符号链接相关报错。

**LLM 客户端建议首轮调用：**
- `get_mcp_usage_manual`
- `get_mcp_security_policy`
- `get_mcp_tool_categories`
- `get_mcp_tool_catalog(category="需要的分类")`
- `get_mcp_validation_checklist`

这些工具会直接告诉 LLM 当前 MCP 暴露了哪些能力、参数如何组织、哪些接口受限，以及为什么不能绕过 MCP 直连 WebUI。端点目录默认按分类读取，避免一次性把全部 Web API 端点注入上下文。

**MCP 安全要求：**
- LLM 严禁绕过 MCP，直接向 WebUI / localhost / 局域网端口发 HTTP 请求取数
- LLM 只能通过 MCP 工具访问项目能力
- MCP 读取到的 `api_key` / `access_key` / `secret_key` 会被脱敏
- MCP 读取敏感配置时会额外返回 `_mcp_security_notice`，明确说明这是权限限制，并禁止通过其他渠道绕过获取
- 脱敏占位符不是可用密钥，也不能当真实值写回配置或队列
- 敏感 Web API 路由要求有效的 Web UI 会话 cookie 或 MCP bridge token，裸 HTTP 直连会被拒绝

完整的客户端说明文档见：
- `Tools/MCPServer/MCP_CLIENT_GUIDE.md`

---

## 架构说明

本项目采用 Wrapper / Adapter 模式：

- **Core**: 保持原版 AiNiee 的核心业务逻辑
- **Adapter Layer**: `ainiee_cli.py` 作为防腐层，负责环境隔离与异常拦截
- **Runtime**: 由 uv 托管，确保依赖环境一致性

---

## 漫画处理参考

本项目的 MangaCore 漫画子系统采用“自动跑批”和“人工精修”分层设计，不把整册自动翻译任务与页级编辑工作台混成同一个入口。

**全自动漫画翻译工作流** 主要参考 `manga-translator-ui-main` 所代表的工作流，以及其上游 **hgmzhn / manga-translator-ui**：

- GitHub: https://github.com/hgmzhn/manga-translator-ui
- Gitee 备份: https://gitee.com/hgmzhn/manga-translator-ui

该部分主要参考其“导入图片/压缩包 -> 文本检测 -> OCR -> 翻译 -> 修补 -> 嵌字渲染 -> 导出”的阶段拆分、运行时资产组织和整册自动处理思路。AiNiee-Next 侧会以 `translate ... --manga`、Web 任务页 Manga Mode 和 `MangaCore` 批处理管线承载这一类少交互、可挂机的自动任务。

**人工精修与漫画编辑器逻辑** 主要参考 **mayocream / Koharu**：

- GitHub: https://github.com/mayocream/koharu

该部分主要参考 Koharu 的人工精修思路，包括工程/页面/文本块、图层化页面状态、当前页局部重跑、文本块位置与样式微调、修补结果检查、可编辑成品导出等精修链路。

后续若参考、接入或复用相关核心模块，本项目会持续保留来源说明与鸣谢信息，并遵守对应开源协议。

---

## 免责声明

- 本项目是 AiNiee 的非官方优化分支，侧重于运行体验与工程稳定性
- 核心翻译算法与原版保持一致，请遵守原版使用协议
- 本工具仅供个人学习与合法用途使用

---

## 支持 AiNiee-Next

如果这个项目对你有帮助喵，欢迎通过爱发电或赞赏码支持 AiNiee-Next 版本的持续开发与维护。支持人会被加入到项目鸣谢列表；无论金额多少，都是一份暖暖的心意喵，也是加速项目开发的动力。

- 爱发电: https://ifdian.net/a/Next_ZhiXie

<div align="center">
  <img src="README_IMG/赞赏码.png" alt="赞赏码" width="320">
</div>

---

<div align="center">
  Made by ShadowLoveElysia
  <br>
  Based on the original work by NEKOparapa
</div>

# AiNiee-Next

<div align="center">
  <img src="https://img.shields.io/badge/Interface-CLI%20%2F%20TUI-0078D4?style=for-the-badge&logo=windows-terminal&logoColor=white" alt="CLI">
  <img src="https://img.shields.io/badge/Runtime-uv-purple?style=for-the-badge&logo=python&logoColor=white" alt="uv">
  <img src="https://img.shields.io/badge/Status-Stable-success?style=for-the-badge" alt="Status">
</div>

<br/>

[簡體中文](../README.md) | [English](../README_EN.md) | [繁體中文](README_zh_CNTW.md) | [日本語](README_JA.md) | [한국어](README_KO.md) | [Русский](README_RU.md) | [Español](README_ES.md)

**AiNiee-Next** 是以 [AiNiee](https://github.com/NEKOparapa/AiNiee) 核心翻譯邏輯為基礎開發的命令列版本。專案使用 **uv** 管理 Python 環境，並針對長時間執行、批次任務、伺服器部署與自動化使用做了許多改進。

專案以 CLI/TUI 為主要操作介面，同時提供 Web 控制面板、任務佇列、外掛系統與 MCP 服務，適合個人翻譯、長篇內容處理及需要長時間執行的批次任務。

---

## 主要特色

- **穩定執行與錯誤復原**：過濾底層 I/O 輸出，減少多餘日誌對 TUI 的干擾，並支援例外攔截、自動重試與斷點續傳；可在 Windows、Linux、macOS、Android（Termux）及 Headless 伺服器環境執行。
- **智慧診斷**：收集 traceback、執行環境、API 平台、模型與最近操作，搭配內建規則及可選的 LLM 分析判斷問題來源，並可整理成便於回報的 GitHub Issue。
- **多格式翻譯**：支援 Epub、Docx、Txt、Srt、Ass、Vtt、Lrc、Json、Po、Paratranz 等 20 多種格式，也可搭配 Calibre 處理 `.mobi`、`.azw3`、`.kepub`、`.fb2` 等電子書。
- **任務與設定管理**：支援執行中調整併發數、切換 API Key、開啟 Web 監看、查看任務狀態以及費用與完成時間預估，並提供多 Profile、設定熱重載和可調整順序的批次任務佇列；佇列執行時也能修改待處理任務，並會依序自動執行。
- **外掛與快取**：可透過集中管理的外掛擴充功能，並提供 RAG 歷史譯文參考與翻譯檢查；Anthropic、Google、Amazon Bedrock 可快取系統提示詞與術語表，API 不相容時會自動停用並提示。
- **模型與 API 支援**：相容主流線上 API、第三方 API 轉接服務及本機模型，會依介面類型提供相應的參數提示，支援 DeepSeek R1、Claude 3.5 等推理模型，也可設定多組 API 自動故障轉移及觸發門檻。
- **高併發處理**：aiohttp 非同步模式可同時處理超過 100 個請求，區分不應重試的錯誤與暫時性錯誤、記錄 API 服務商相容性，並保護檔案描述元與連接埠等系統資源；併發數達到 15 時會提示啟用非同步模式。
- **Web、MCP 與 MangaCore**：Web 控制面板可管理任務、設定、佇列與外掛；MCP 讓 LLM 客戶端透過受控工具操作專案；MangaCore 提供漫畫自動批次處理與 Web 編輯流程。

---

## 快速開始

新使用者建議先閱讀：

- [圖文快速上手教程](README_QUICK_START.md)
- [DeepSeek API Key 申請教程](DEEPSEEK_API_KEY.md)
- [提示詞、術語表、潤色與軟體設定教程](TRANSLATION_WORKFLOW_GUIDE.md)

### 方式一：一鍵啟動

**1. 取得程式碼**

```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
```

**2. 首次準備環境**

Windows：

```batch
雙擊 prepare.bat
```

Linux / macOS：

```bash
chmod +x prepare.sh && ./prepare.sh
```

**3. 啟動**

Windows：

```batch
雙擊 Launch.bat
```

Linux / macOS：

```bash
./Launch.sh
```

### 方式二：手動啟動

安裝 uv：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell 可使用：

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

取得程式碼並啟動：

```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
uv run ainiee_cli.py
```

---

## 命令列範例

翻譯任務：

```bash
uv run ainiee_cli.py translate input.txt -o output_dir -p MyProfile -s Japanese -t Chinese --resume --yes
```

佇列任務：

```bash
uv run ainiee_cli.py queue --queue-file my_queue.json --yes
```

MCP 服務：

```bash
uv run ainiee_cli.py mcp --mcp-transport stdio
```

常用參數：

- `translate` / `polish` / `export` / `queue` / `mcp`：任務類型
- `-o, --output`：輸出路徑
- `-p, --profile`：設定檔名稱
- `-s, --source`：來源語言
- `-t, --target`：目標語言
- `--type`：專案類型，例如 Txt、Epub、MTool、RenPy
- `--resume`：自動恢復快取任務
- `--yes`：非互動模式
- `--threads`：併發執行緒數
- `--platform`：API 平台
- `--model`：模型名稱
- `--api-url`：API 位址
- `--api-key`：API 金鑰
- `--mcp-transport`：MCP 傳輸模式，可選 `stdio` / `streamable-http` / `sse`

---

## Web 控制面板

啟動方式：

1. 執行 `uv run ainiee_cli.py` 進入主選單
2. 選擇 **15. Start Web Server**
3. 程式會啟動服務，預設連接埠為 `8000`，並自動開啟瀏覽器

Web 控制面板可用於查看任務進度、管理 Profile、編輯術語表、管理佇列、控制外掛，以及操作部分 MangaCore 功能。

---

## MCP 服務

AiNiee-Next 提供可選 MCP 服務，讓支援 MCP 的 LLM 客戶端透過受控工具操作專案能力。

啟動範例：

```bash
uv run ainiee_cli.py mcp --mcp-transport streamable-http
```

連線位址：

```text
本機位址: http://127.0.0.1:8765/mcp
區域網路位址: http://<你的區域網路 IP>:8765/mcp
```

完整說明請參考：

- [MCP 客戶端指南](../Tools/MCPServer/MCP_CLIENT_GUIDE.md)

---

## 漫畫處理參考

MangaCore 的自動漫畫翻譯流程主要參考：

- [hgmzhn / manga-translator-ui](https://github.com/hgmzhn/manga-translator-ui)

人工精修與漫畫編輯器思路主要參考：

- [mayocream / Koharu](https://github.com/mayocream/koharu)

後續若接入或復用相關核心模組，專案會持續保留來源說明與致謝資訊，並遵守對應開源協議。

---

## 免責聲明

- 本專案是 AiNiee 的非官方最佳化分支，重點在執行體驗與工程穩定性。
- 核心翻譯邏輯與原版保持一致，請遵守原版使用協議。
- 本工具僅供個人學習與合法用途使用。

---

<div align="center">
  Made by ShadowLoveElysia
  <br>
  Based on the original work by NEKOparapa
</div>

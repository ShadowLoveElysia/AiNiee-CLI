# AiNiee-Next

<div align="center">
  <img src="https://img.shields.io/badge/Interface-CLI%20%2F%20TUI-0078D4?style=for-the-badge&logo=windows-terminal&logoColor=white" alt="CLI">
  <img src="https://img.shields.io/badge/Runtime-uv-purple?style=for-the-badge&logo=python&logoColor=white" alt="uv">
  <img src="https://img.shields.io/badge/Status-Stable-success?style=for-the-badge" alt="Status">
</div>

<br/>

[簡体中文](../README.md) | [English](../README_EN.md) | [繁體中文](README_zh_CNTW.md) | [日本語](README_JA.md) | [한국어](README_KO.md) | [Русский](README_RU.md) | [Español](README_ES.md)

**AiNiee-Next** は、[AiNiee](https://github.com/NEKOparapa/AiNiee) の中核となる翻訳処理をもとに開発したコマンドライン版です。Python 環境は **uv** で管理し、長時間実行、バッチ処理、サーバー運用、自動化に向けた改善を加えています。

操作は CLI/TUI が中心で、Web ダッシュボード、タスクキュー、プラグイン、MCP サービスも利用できます。個人での翻訳、長文コンテンツの処理、長時間のバッチ実行に適しています。

---

## 主な特徴

- **安定した実行と復旧**：低レベルの I/O 出力を整理して不要なログによる TUI の乱れを抑え、例外処理、自動再試行、途中からの再開に対応します。Windows、Linux、macOS、Android（Termux）、Headless サーバーで利用できます。
- **エラー診断**：traceback、実行環境、API、モデル、直近の操作を収集し、内蔵ルールと任意の LLM 分析で原因の切り分けを支援します。疑わしいコード上の問題は GitHub Issue 用に整理できます。
- **多形式対応**：Epub、Docx、Txt、Srt、Ass、Vtt、Lrc、Json、Po、Paratranz など 20 種類以上に対応し、Calibre と連携して `.mobi`、`.azw3`、`.kepub`、`.fb2` などの電子書籍も処理できます。
- **タスクと設定の管理**：実行中の同時実行数調整、API Key の切り替え、Web 監視、タスク状況、費用と完了時間の見積もりに加え、複数の Profile、設定のホットリロード、並べ替え可能なバッチキューを利用できます。キュー実行中も待機中のタスクを編集でき、設定した順番で自動実行されます。
- **プラグインとキャッシュ**：一元管理できるプラグインで機能を拡張し、RAG による過去訳の参照と翻訳チェックを利用できます。Anthropic、Google、Amazon Bedrock ではシステムプロンプトや用語集をキャッシュでき、非対応 API では自動的に無効化して通知します。
- **モデルと API**：主要なオンライン API、サードパーティーの API 中継サービス、ローカルモデルに対応し、インターフェースに応じたパラメーター設定の案内を表示します。DeepSeek R1 や Claude 3.5 などの推論モデル、複数 API の自動フェイルオーバー、切り替え条件の設定にも対応します。
- **高い同時処理性能**：aiohttp を使った非同期モードは 100 件を超える同時リクエストに対応し、再試行できないエラーと一時的なエラーを分けて処理します。API プロバイダーごとの互換性を記録し、ファイル記述子やポートなどのシステム資源も保護します。同時実行数が 15 以上になると、非同期モードの利用を案内します。
- **Web、MCP、MangaCore**：Web ダッシュボードではタスク、設定、キュー、プラグインを管理でき、MCP は LLM クライアントに制御された操作手段を提供します。MangaCore は漫画の自動バッチ処理と Web 編集に対応します。

---

## クイックスタート

新規ユーザーは、まず次の英語または中国語ドキュメントを参照してください。

- [Quick Start Guide](README_QUICK_START_EN.md)
- [DeepSeek API Key Guide](DEEPSEEK_API_KEY_EN.md)
- [Prompt, Glossary, Polishing, and Advanced Settings Guide](TRANSLATION_WORKFLOW_GUIDE_EN.md)

### 方法 1：ワンクリック起動

**1. コードを取得**

```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
```

**2. 初回環境セットアップ**

Windows：

```batch
prepare.bat をダブルクリック
```

Linux / macOS：

```bash
chmod +x prepare.sh && ./prepare.sh
```

**3. 起動**

Windows：

```batch
Launch.bat をダブルクリック
```

Linux / macOS：

```bash
./Launch.sh
```

### 方法 2：手動起動

uv をインストール：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell：

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

起動：

```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
uv run ainiee_cli.py
```

---

## コマンドライン例

翻訳タスク：

```bash
uv run ainiee_cli.py translate input.txt -o output_dir -p MyProfile -s Japanese -t Chinese --resume --yes
```

キュータスク：

```bash
uv run ainiee_cli.py queue --queue-file my_queue.json --yes
```

MCP サーバー：

```bash
uv run ainiee_cli.py mcp --mcp-transport stdio
```

主な引数：

- `translate` / `polish` / `export` / `queue` / `mcp`：タスク種別
- `-o, --output`：出力先
- `-p, --profile`：Profile 名
- `-s, --source`：翻訳元言語
- `-t, --target`：翻訳先言語
- `--type`：Txt、Epub、MTool、RenPy などのプロジェクト種別
- `--resume`：キャッシュから自動再開
- `--yes`：非対話モード
- `--threads`：並行スレッド数
- `--platform`：API プラットフォーム
- `--model`：モデル名
- `--api-url`：API URL
- `--api-key`：API Key
- `--mcp-transport`：`stdio` / `streamable-http` / `sse`

---

## Web ダッシュボード

起動手順：

1. `uv run ainiee_cli.py` を実行してメインメニューを開く
2. **15. Start Web Server** を選択
3. 既定ではポート `8000` でサービスが起動し、ブラウザが開きます

Web ダッシュボードでは、タスク進捗、Profile、用語集、キュー、プラグイン、一部の MangaCore 機能を管理できます。

---

## MCP サービス

AiNiee-Next は任意機能として MCP サーバーを提供します。MCP 対応 LLM クライアントから、プロジェクト機能を安全に操作できます。

起動例：

```bash
uv run ainiee_cli.py mcp --mcp-transport streamable-http
```

接続先：

```text
ローカル: http://127.0.0.1:8765/mcp
LAN: http://<your-lan-ip>:8765/mcp
```

詳細：

- [MCP Client Guide](../Tools/MCPServer/MCP_CLIENT_GUIDE.md)

---

## 漫画処理の参考元

MangaCore の自動漫画翻訳フローは、主に次のプロジェクトを参考にしています。

- [hgmzhn / manga-translator-ui](https://github.com/hgmzhn/manga-translator-ui)

手動修正と漫画エディタの設計は、主に次のプロジェクトを参考にしています。

- [mayocream / Koharu](https://github.com/mayocream/koharu)

今後関連モジュールを参照、統合、再利用する場合も、出典と謝辞を保持し、対応するオープンソースライセンスを遵守します。

---

## 免責事項

- 本プロジェクトは AiNiee の非公式最適化ブランチです。
- コア翻訳ロジックは原版と同じ方針を保っています。原版の利用規約も確認してください。
- 本ツールは個人学習および合法的な用途のために提供されています。

---

<div align="center">
  Made by ShadowLoveElysia
  <br>
  Based on the original work by NEKOparapa
</div>

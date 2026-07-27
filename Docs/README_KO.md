# AiNiee-Next

<div align="center">
  <img src="https://img.shields.io/badge/Interface-CLI%20%2F%20TUI-0078D4?style=for-the-badge&logo=windows-terminal&logoColor=white" alt="CLI">
  <img src="https://img.shields.io/badge/Runtime-uv-purple?style=for-the-badge&logo=python&logoColor=white" alt="uv">
  <img src="https://img.shields.io/badge/Status-Stable-success?style=for-the-badge" alt="Status">
</div>

<br/>

[简体中文](../README.md) | [English](../README_EN.md) | [繁體中文](README_zh_CNTW.md) | [日本語](README_JA.md) | [한국어](README_KO.md) | [Русский](README_RU.md) | [Español](README_ES.md)

**AiNiee-Next**는 [AiNiee](https://github.com/NEKOparapa/AiNiee)의 핵심 번역 기능을 바탕으로 개발한 명령줄 버전입니다. **uv**로 Python 환경을 관리하며 장시간 실행, 일괄 작업, 서버 배포, 자동화 사용을 위한 여러 개선 사항을 포함합니다.

CLI/TUI를 기본 조작 환경으로 사용하면서 Web 대시보드, 작업 큐, 플러그인, MCP 서비스도 제공합니다. 개인 번역, 장문 콘텐츠 처리, 장시간 일괄 작업에 적합합니다.

> 매우 죄송합니다. 개발자는 한국어를 이해하지 못하므로 일부 시스템 프롬프트는 사용자가 직접 작성해야 할 수 있습니다. 개발자가 여러 언어의 프롬프트를 지속적으로 유지보수할 여력이 아직 없습니다. 도움을 주실 의향이 있다면 프로젝트에 PR을 보내 주시면 환영합니다.

---

## 주요 기능

- **안정적인 실행과 복구**: 하위 계층의 I/O 출력을 정리해 불필요한 로그가 TUI를 방해하지 않도록 하며, 예외 처리, 자동 재시도, 중단 지점부터 이어하기를 지원합니다. Windows, Linux, macOS, Android(Termux), Headless 서버에서 실행할 수 있습니다.
- **오류 진단**: traceback, 실행 환경, API, 모델, 최근 작업을 수집하고 내장 규칙과 선택적으로 사용할 수 있는 LLM 분석으로 원인 파악을 돕습니다. 코드 문제로 의심되는 내용은 GitHub Issue 형식으로 정리할 수 있습니다.
- **다양한 형식 지원**: Epub, Docx, Txt, Srt, Ass, Vtt, Lrc, Json, Po, Paratranz 등 20개 이상의 형식을 지원하며, Calibre와 연동해 `.mobi`, `.azw3`, `.kepub`, `.fb2` 같은 전자책도 처리합니다.
- **작업 및 설정 관리**: 실행 중 동시 작업 수 조절, API Key 전환, Web 모니터링, 작업 상태, 비용과 완료 시간 예상치 확인뿐 아니라 여러 Profile, 설정 핫 리로드, 순서를 바꿀 수 있는 일괄 작업 큐를 지원합니다. 큐 실행 중에도 대기 작업을 수정할 수 있으며 설정한 순서대로 자동 실행됩니다.
- **플러그인과 캐시**: 한곳에서 관리하는 플러그인으로 기능을 확장하고, RAG 기반 이전 번역 참고와 번역 검사를 사용할 수 있습니다. Anthropic·Google·Amazon Bedrock에서는 시스템 프롬프트와 용어집을 컨텍스트 캐시로 저장하며, 호환되지 않는 API에서는 자동으로 비활성화하고 안내합니다.
- **모델 및 API 지원**: 주요 온라인 API, 타사 중계 서비스, 로컬 모델과 호환되며 인터페이스 유형에 맞는 매개변수 안내를 제공합니다. DeepSeek R1, Claude 3.5 같은 추론 모델, 여러 API 사이의 자동 장애 전환과 전환 기준 설정도 지원합니다.
- **높은 동시 처리 성능**: aiohttp 비동기 모드는 100개 이상의 동시 요청을 처리하며, 재시도할 수 없는 오류와 일시적 오류를 구분하고 API 제공자별 호환성을 기록합니다. 파일 디스크립터와 포트 같은 시스템 자원을 보호하며, 동시 작업 수가 15 이상이면 비동기 모드 사용을 안내합니다.
- **Web, MCP, MangaCore**: Web 대시보드에서 작업, 설정, 큐, 플러그인을 관리할 수 있고 MCP는 LLM 클라이언트에 제어된 조작 수단을 제공합니다. MangaCore는 만화 자동 일괄 처리와 Web 편집을 지원합니다.

---

## 빠른 시작

새 사용자는 먼저 영어 또는 중국어 문서를 참고하는 것을 권장합니다.

- [Quick Start Guide](README_QUICK_START_EN.md)
- [DeepSeek API Key Guide](DEEPSEEK_API_KEY_EN.md)
- [Prompt, Glossary, Polishing, and Advanced Settings Guide](TRANSLATION_WORKFLOW_GUIDE_EN.md)

### 방법 1: 원클릭 실행

**1. 코드 받기**

```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
```

**2. 최초 환경 준비**

Windows:

```batch
prepare.bat 더블 클릭
```

Linux / macOS:

```bash
chmod +x prepare.sh && ./prepare.sh
```

**3. 실행**

Windows:

```batch
Launch.bat 더블 클릭
```

Linux / macOS:

```bash
./Launch.sh
```

### 방법 2: 수동 실행

uv 설치:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell:

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

코드 받기 및 실행:

```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
uv run ainiee_cli.py
```

---

## 명령줄 예시

번역 작업:

```bash
uv run ainiee_cli.py translate input.txt -o output_dir -p MyProfile -s Japanese -t Chinese --resume --yes
```

큐 작업:

```bash
uv run ainiee_cli.py queue --queue-file my_queue.json --yes
```

MCP 서버:

```bash
uv run ainiee_cli.py mcp --mcp-transport stdio
```

주요 인자:

- `translate` / `polish` / `export` / `queue` / `mcp`: 작업 유형
- `-o, --output`: 출력 경로
- `-p, --profile`: Profile 이름
- `-s, --source`: 원문 언어
- `-t, --target`: 대상 언어
- `--type`: Txt, Epub, MTool, RenPy 등 프로젝트 유형
- `--resume`: 캐시된 작업 자동 재개
- `--yes`: 비대화형 모드
- `--threads`: 동시 스레드 수
- `--platform`: API 플랫폼
- `--model`: 모델 이름
- `--api-url`: API URL
- `--api-key`: API Key
- `--mcp-transport`: `stdio` / `streamable-http` / `sse`

---

## Web 대시보드

실행 방법:

1. `uv run ainiee_cli.py`로 메인 메뉴를 엽니다
2. **15. Start Web Server**를 선택합니다
3. 기본 포트 `8000`에서 서비스가 시작되고 브라우저가 열립니다

Web 대시보드에서는 작업 진행률, Profile, 용어집, 큐, 플러그인, 일부 MangaCore 기능을 관리할 수 있습니다.

---

## MCP 서비스

AiNiee-Next는 선택 기능으로 MCP 서버를 제공합니다. MCP 지원 LLM 클라이언트가 프로젝트 기능을 안전하게 조작할 수 있습니다.

실행 예시:

```bash
uv run ainiee_cli.py mcp --mcp-transport streamable-http
```

연결 주소:

```text
로컬: http://127.0.0.1:8765/mcp
LAN: http://<your-lan-ip>:8765/mcp
```

자세한 문서:

- [MCP Client Guide](../Tools/MCPServer/MCP_CLIENT_GUIDE.md)

---

## 만화 처리 참고

MangaCore의 자동 만화 번역 흐름은 주로 다음 프로젝트를 참고합니다.

- [hgmzhn / manga-translator-ui](https://github.com/hgmzhn/manga-translator-ui)

수동 보정 및 만화 편집기 설계는 주로 다음 프로젝트를 참고합니다.

- [mayocream / Koharu](https://github.com/mayocream/koharu)

향후 관련 모듈을 참조, 통합 또는 재사용하는 경우에도 출처와 감사 표시를 유지하고 해당 오픈소스 라이선스를 준수합니다.

---

## 면책 조항

- 이 프로젝트는 AiNiee의 비공식 최적화 브랜치입니다.
- 핵심 번역 로직은 원본 프로젝트와 같은 방향을 유지합니다. 원본 프로젝트의 사용 조건도 확인해 주세요.
- 이 도구는 개인 학습 및 합법적인 용도로만 제공됩니다.

---

<div align="center">
  Made by ShadowLoveElysia
  <br>
  Based on the original work by NEKOparapa
</div>

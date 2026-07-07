# AI Chat — 멀티모달 채팅 서비스

ChatGPT / Claude 스타일의 완성도 높은 채팅 웹앱입니다. **Gemini · Claude · 로컬(Ollama)**
멀티 프로바이더로 자연어 + 이미지 대화를 지원하며, FastAPI 서버 + 자체 프론트엔드로 구성됩니다.
키가 설정되거나 서버가 켜진 프로바이더만 모델 드롭다운에 자동 노출됩니다.

## 주요 기능

- 💬 **스트리밍 응답** — 토큰 단위 실시간 타이핑
- 🗂 **여러 대화 관리 + 영속 저장** — 사이드바 대화 목록, 이름 변경/삭제, SQLite 저장
- 🖼 **이미지 멀티모달** — 첨부 버튼 · 드래그앤드롭 · 붙여넣기(최대 8장)
- ✍️ **마크다운 · 코드 하이라이트 · 복사** — 표/목록/코드블록, 언어별 하이라이트, 코드/메시지 복사
- 🔁 **응답 재생성 · 중지**, 🏷 **대화 자동 제목**, 🌗 **다크/라이트 테마**, 📱 **반응형**

## 실행 방법

### A. Docker (권장 — 어느 컴퓨터에서든 동일하게 실행)

Docker만 설치돼 있으면 아래 3줄로 끝납니다.

```bash
cp .env.example .env      # 그리고 .env 에 GEMINI_API_KEY (선택: ANTHROPIC_API_KEY) 입력
docker compose up -d      # 빌드 + 실행 (백그라운드)
docker compose logs -f    # 로그 확인 (Ctrl+C 로 로그만 빠져나옴)
```

브라우저에서 **http://localhost:8000** 접속. 대화 DB·업로드 이미지는
`./data`·`./uploads` 볼륨에 저장되어 컨테이너를 지워도 유지됩니다.
중지: `docker compose down` · 코드 수정 후 재배포: `docker compose up -d --build`.

> 로컬 Ollama는 호스트에서 켜두면 컨테이너가 `host.docker.internal` 로 자동 연결합니다(설정 불필요).

### B. 로컬 파이썬 (Docker 없이)

```bash
pip install -r requirements.txt
cp .env.example .env      # .env 에 키 입력
python main.py            # 또는 개발용: uvicorn main:app --reload --port 8000
```

브라우저에서 **http://localhost:8000** 접속.

## 구조

```
main.py              FastAPI 앱 (라우트 · 스트리밍 · 정적 서빙)
backend/
  db.py              SQLite 영속 계층 (대화/메시지)
  storage.py         업로드 이미지 저장/로드
  llm.py             멀티 프로바이더 스트리밍(Gemini/Claude/Ollama) · 제목 생성
static/
  index.html         SPA 마크업
  styles.css         디자인 시스템 (다크/라이트)
  app.js             프론트 로직 (스트리밍·대화관리·마크다운)
  vendor/            marked · DOMPurify · highlight.js · KaTeX(수식) (로컬 번들, 오프라인 동작)
Dockerfile           컨테이너 이미지 빌드 정의
docker-compose.yml   실행 정의 (포트·볼륨·env·Ollama 연결)
.env.example         환경변수 템플릿 (.env 로 복사해 사용)
data/                SQLite DB (자동 생성, git 무시)
uploads/             업로드 이미지 (자동 생성, git 무시)
app.py               레거시 Gradio 데모 (참고용)
```

## API 개요

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/models` | 사용 가능한 모델 목록 |
| GET | `/api/conversations` | 대화 목록 |
| POST | `/api/conversations` | 새 대화 생성 |
| GET | `/api/conversations/{id}` | 대화 + 메시지 조회 |
| PATCH | `/api/conversations/{id}` | 대화 이름 변경 |
| DELETE | `/api/conversations/{id}` | 대화 삭제 |
| POST | `/api/chat` | 메시지 전송 → NDJSON 스트리밍 |
| POST | `/api/conversations/{id}/regenerate` | 마지막 응답 재생성 |

"""ChatGPT/Claude 스타일 멀티모달 채팅 서비스 — FastAPI 백엔드.

- Gemini 멀티모달 스트리밍 응답 (NDJSON)
- 여러 대화 관리 + SQLite 영속 저장
- 이미지 업로드(멀티모달), 대화 자동 제목, 응답 재생성

실행:  uvicorn main:app --reload  (또는  python main.py)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import db, llm, storage

load_dotenv()

logger = logging.getLogger("aichat")
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_BODY_BYTES = 40 * 1024 * 1024  # 요청 바디 상한 (이미지 포함)


async def _db(fn: Callable, *args, **kwargs):
    """동기 SQLite/디스크 호출을 스레드로 오프로드해 이벤트 루프를 막지 않는다."""
    return await asyncio.to_thread(fn, *args, **kwargs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    storage.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="AI Chat", lifespan=lifespan)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "요청이 너무 큽니다."})
    return await call_next(request)


# ------------------------------------------------------------------ 요청 스키마
class NewConversation(BaseModel):
    model: Optional[str] = None


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str = Field("", max_length=32_000)
    images: list[str] = Field(default_factory=list, max_length=8)  # data URL 목록
    model: Optional[str] = None


def _img_urls(filenames: list[str]) -> list[str]:
    return [f"/uploads/{name}" for name in filenames]


def _sse(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------- 메타 API
@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "has_key": bool(os.getenv("GEMINI_API_KEY")),
        "providers": {
            "google": bool(os.getenv("GEMINI_API_KEY")),
            "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        },
    }


@app.get("/api/models")
async def models() -> dict:
    available = await llm.list_models()
    default = available[0]["id"] if available else llm.DEFAULT_MODEL
    return {"models": available, "default": default}


# --------------------------------------------------------------- 대화 CRUD API
@app.get("/api/conversations")
async def get_conversations() -> dict:
    return {"conversations": await _db(db.list_conversations)}


@app.post("/api/conversations")
async def post_conversation(req: NewConversation) -> dict:
    return await _db(db.create_conversation, model=llm.resolve_model(req.model))


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str) -> dict:
    conv = await _db(db.get_conversation, cid)
    if conv is None:
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    for m in conv["messages"]:
        m["images"] = _img_urls(m["images"])
    return conv


@app.patch("/api/conversations/{cid}")
async def patch_conversation(cid: str, req: RenameRequest) -> dict:
    title = req.title.strip()
    if not await _db(db.rename_conversation, cid, title):
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    return {"ok": True, "title": title}


@app.delete("/api/conversations/{cid}")
async def del_conversation(cid: str) -> dict:
    if not await _db(db.conversation_exists, cid):
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    files = await _db(db.delete_conversation, cid)
    await _db(storage.delete_files, files)
    return {"ok": True}


# --------------------------------------------------------------- 스트리밍 채팅
async def _run_completion(
    cid: str, model: str, generate_title: bool
) -> AsyncIterator[str]:
    """현재 대화 히스토리로 응답을 생성/저장하며 NDJSON 이벤트를 흘려보낸다."""
    history = await _db(db.get_messages, cid)
    assistant = await _db(db.add_message, cid, "assistant", "")
    yield _sse({"type": "meta", "assistant_message_id": assistant["id"]})

    acc = ""
    try:
        async for delta in llm.stream_reply(model, history):
            acc += delta
            yield _sse({"type": "delta", "text": delta})
    except Exception:  # noqa: BLE001
        logger.exception("스트리밍 생성 중 오류")
        yield _sse(
            {"type": "error", "message": "생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."}
        )
        return
    finally:
        # 정상/비정상(연결 끊김) 종료 모두 지금까지의 내용을 저장 (동기: 종료 중에도 보장)
        db.update_message_content(assistant["id"], acc)

    # 첫 교환이면 제목 자동 생성
    if generate_title:
        user_msgs = [m for m in history if m["role"] == "user"]
        first_text = user_msgs[0]["content"] if user_msgs else ""
        title = await llm.generate_title(model, first_text)
        await _db(db.rename_conversation, cid, title)
        yield _sse({"type": "title", "title": title})

    yield _sse({"type": "done", "content": acc})


@app.post("/api/chat")
async def chat(req: ChatRequest):
    # 대화 준비 (없으면 생성). 이어가는 대화는 저장된 모델을 유지한다.
    cid = req.conversation_id
    if not cid or not await _db(db.conversation_exists, cid):
        model = llm.resolve_model(req.model)
        conv = await _db(db.create_conversation, model=model)
        cid = conv["id"]
    else:
        stored = await _db(db.get_conversation_model, cid)
        model = llm.resolve_model(req.model or stored)

    message = (req.message or "").strip()
    if not message and not req.images:
        raise HTTPException(400, "메시지나 이미지를 입력하세요.")

    # 이미지 저장 (CPU 디코드는 스레드로 오프로드)
    filenames: list[str] = []
    for data_url in req.images[:8]:  # 최대 8장
        try:
            filenames.append(await _db(storage.save_data_url, data_url))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    was_empty = await _db(db.message_count, cid) == 0
    await _db(db.add_message, cid, "user", message, filenames)

    async def gen() -> AsyncIterator[str]:
        # 프론트가 새 대화 id / 저장된 이미지 URL 을 알 수 있게 먼저 알림
        yield _sse(
            {
                "type": "start",
                "conversation_id": cid,
                "model": model,
                "user_images": _img_urls(filenames),
            }
        )
        async for line in _run_completion(cid, model, generate_title=was_empty):
            yield line

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/conversations/{cid}/regenerate")
async def regenerate(cid: str, req: NewConversation):
    if not await _db(db.conversation_exists, cid):
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    stored = await _db(db.get_conversation_model, cid)
    model = llm.resolve_model(req.model or stored)
    # 마지막 assistant 응답 제거 후 다시 생성
    await _db(db.delete_last_assistant_message, cid)

    async def gen() -> AsyncIterator[str]:
        yield _sse({"type": "start", "conversation_id": cid, "model": model})
        async for line in _run_completion(cid, model, generate_title=False):
            yield line

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# --------------------------------------------------------------- 정적 파일/루트
app.mount("/uploads", StaticFiles(directory=str(storage.UPLOADS_DIR), check_dir=False), name="uploads")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_request, exc: RuntimeError):
    logger.error("RuntimeError: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

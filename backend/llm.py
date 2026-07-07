"""멀티 프로바이더 LLM 연동 — Gemini / Claude(Anthropic) / 로컬 Ollama.

provider 별로 스트리밍 응답과 대화 제목 생성을 분기한다.
- google    : GEMINI_API_KEY, google.genai SDK
- anthropic : ANTHROPIC_API_KEY, anthropic SDK (Claude)
- ollama    : 로컬 Ollama 서버(OLLAMA_HOST). 키 불필요, 내 CPU/GPU 사용
"""

from __future__ import annotations

import base64
import json
import os
from typing import AsyncIterator

import httpx
from google import genai
from google.genai import types

from . import storage

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# UI 에 노출할 클라우드 모델 목록 (첫 항목이 기본값).
# 로컬 Ollama 모델은 실행 중인 서버에서 동적으로 추가된다(list_models 참고).
AVAILABLE_MODELS: list[dict[str, str]] = [
    {"provider": "google", "id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "desc": "빠르고 균형 잡힌 기본 모델"},
    {"provider": "google", "id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "desc": "가장 강력한 추론 (느림)"},
    {"provider": "google", "id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash", "desc": "가볍고 빠른 이전 세대"},
    {"provider": "anthropic", "id": "claude-opus-4-8", "label": "Claude Opus 4.8", "desc": "Anthropic 최고 성능 모델"},
    {"provider": "anthropic", "id": "claude-sonnet-5", "label": "Claude Sonnet 5", "desc": "빠르고 균형 잡힌 Claude"},
]
DEFAULT_MODEL = AVAILABLE_MODELS[0]["id"]
_STATIC_PROVIDER = {m["id"]: m["provider"] for m in AVAILABLE_MODELS}

SYSTEM_PROMPT = (
    "당신은 한국어를 기본으로 사용하는 유능하고 친절한 AI 어시스턴트입니다. "
    "정확하고 명료하게 답하고, 코드는 언어를 명시한 마크다운 코드블록으로 제공하세요. "
    "수식이나 표가 필요하면 마크다운으로 깔끔하게 정리하세요. "
    "사용자가 다른 언어로 물으면 그 언어로 답하세요."
)

_gemini: genai.Client | None = None
_anthropic = None  # anthropic.AsyncAnthropic (지연 로드)


# ----------------------------------------------------------------- 프로바이더 판별
def provider_for(model: str) -> str:
    """모델 id → provider. 정적 목록에 없으면 로컬(Ollama) 모델로 간주한다."""
    return _STATIC_PROVIDER.get(model, "ollama")


def resolve_model(model: str | None) -> str:
    return model or DEFAULT_MODEL


# ----------------------------------------------------------------- 클라이언트
def get_client() -> genai.Client:
    global _gemini
    if _gemini is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 가 설정되지 않았습니다. .env 파일을 확인하세요.")
        _gemini = genai.Client(api_key=api_key)
    return _gemini


def _anthropic_client():
    global _anthropic
    if _anthropic is None:
        from anthropic import AsyncAnthropic  # 선택적 의존성 — 필요할 때만 로드

        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY 가 설정되지 않았습니다. .env 파일을 확인하세요.")
        _anthropic = AsyncAnthropic()
    return _anthropic


# ----------------------------------------------------------------- 사용 가능 모델 목록
async def _ollama_models() -> list[dict[str, str]]:
    """로컬 Ollama 서버에 받아둔 모델을 조회한다. 미구동 시 조용히 빈 목록."""
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{OLLAMA_HOST}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception:  # noqa: BLE001 — 서버 미구동/연결 실패는 정상 상황
        return []
    out: list[dict[str, str]] = []
    for m in data.get("models", []):
        name = m.get("name", "")
        if name:
            out.append(
                {"provider": "ollama", "id": name, "label": f"{name} (로컬)", "desc": "내 PC에서 실행 (Ollama)"}
            )
    return out


async def list_models() -> list[dict[str, str]]:
    """실제로 사용 가능한(키가 설정됐거나 서버가 켜진) 모델만 노출한다."""
    models: list[dict[str, str]] = []
    for m in AVAILABLE_MODELS:
        if m["provider"] == "google" and not os.getenv("GEMINI_API_KEY"):
            continue
        if m["provider"] == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
            continue
        models.append(m)
    models.extend(await _ollama_models())
    return models


# ----------------------------------------------------------------- 메시지 변환
def _load_image_b64(filename: str) -> str | None:
    blob = storage.load_bytes(filename)
    return base64.standard_b64encode(blob).decode() if blob is not None else None


def _build_contents_google(messages: list[dict]) -> list[types.Content]:
    """DB 메시지 목록 → Gemini contents (이미지는 디스크에서 로드)."""
    contents: list[types.Content] = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        parts: list[types.Part] = []
        for filename in msg.get("images", []):
            blob = storage.load_bytes(filename)
            if blob is not None:
                parts.append(types.Part.from_bytes(data=blob, mime_type="image/png"))
        text = msg.get("content") or ""
        if text.strip():
            parts.append(types.Part.from_text(text=text))
        elif not parts:
            continue
        if not parts:
            parts.append(types.Part.from_text(text="(빈 메시지)"))
        contents.append(types.Content(role=role, parts=parts))
    return contents


def _messages_for_anthropic(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "assistant"
        blocks: list[dict] = []
        for filename in msg.get("images", []):
            b64 = _load_image_b64(filename)
            if b64 is not None:
                blocks.append(
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}
                )
        text = (msg.get("content") or "").strip()
        if text:
            blocks.append({"type": "text", "text": text})
        if not blocks:
            blocks.append({"type": "text", "text": "(빈 메시지)"})
        out.append({"role": role, "content": blocks})
    return out


def _messages_for_ollama(messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in messages:
        role = "user" if msg["role"] == "user" else "assistant"
        entry: dict = {"role": role, "content": msg.get("content") or ""}
        imgs = [b64 for f in msg.get("images", []) if (b64 := _load_image_b64(f)) is not None]
        if imgs:
            entry["images"] = imgs
        out.append(entry)
    return out


# ----------------------------------------------------------------- 스트리밍 응답
async def _stream_google(model: str, messages: list[dict]) -> AsyncIterator[str]:
    client = get_client()
    stream = await client.aio.models.generate_content_stream(
        model=model,
        contents=_build_contents_google(messages),
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    async for chunk in stream:
        text = getattr(chunk, "text", None)
        if text:
            yield text


async def _stream_anthropic(model: str, messages: list[dict]) -> AsyncIterator[str]:
    client = _anthropic_client()
    async with client.messages.stream(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=_messages_for_anthropic(messages),
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def _stream_ollama(model: str, messages: list[dict]) -> AsyncIterator[str]:
    payload = {"model": model, "messages": _messages_for_ollama(messages), "stream": True}
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", f"{OLLAMA_HOST}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
                if data.get("done"):
                    break


async def stream_reply(model: str, messages: list[dict]) -> AsyncIterator[str]:
    """대화 히스토리를 받아 응답을 토큰 단위로 스트리밍한다 (provider 별 분기)."""
    provider = provider_for(model)
    if provider == "anthropic":
        gen = _stream_anthropic(model, messages)
    elif provider == "ollama":
        gen = _stream_ollama(model, messages)
    else:
        gen = _stream_google(model, messages)
    async for chunk in gen:
        yield chunk


# ----------------------------------------------------------------- 대화 제목 생성
async def _complete(model: str, prompt: str, max_tokens: int = 40) -> str:
    """짧은 단발 응답 (제목 생성용). provider 별 분기."""
    provider = provider_for(model)
    if provider == "anthropic":
        client = _anthropic_client()
        resp = await client.messages.create(
            model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}]
        )
        return "".join(b.text for b in resp.content if b.type == "text")
    if provider == "ollama":
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OLLAMA_HOST}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [{"role": "user", "content": prompt}],
                    "options": {"num_predict": max_tokens},
                },
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")
    # google — 2.5 의 내부 thinking 이 짧은 출력 토큰을 소진하지 않도록 비활성화
    client = get_client()
    resp = await client.aio.models.generate_content(
        model=DEFAULT_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return resp.text or ""


async def generate_title(model: str, first_user_text: str) -> str:
    """첫 사용자 메시지로 짧은 대화 제목을 만든다. 실패 시 폴백."""
    text = (first_user_text or "").strip()
    if not text:
        return "새 대화"
    lines = text.splitlines()
    fallback = (lines[0][:40] if lines else "") or "새 대화"
    prompt = (
        "다음 대화 첫 메시지를 3~6단어의 아주 짧은 제목으로 요약해줘. "
        "따옴표·마침표·접두어 없이 제목만 출력해.\n\n"
        f"메시지: {text[:500]}"
    )
    try:
        raw = (await _complete(model, prompt, max_tokens=40)).strip().strip('"').strip("'")
        title = raw.splitlines()[0].replace("제목:", "").strip() if raw else ""
        return title[:60] if title else fallback
    except Exception:  # noqa: BLE001
        return fallback

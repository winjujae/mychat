"""업로드 이미지 저장/로드 — data URL 을 디스크에 안전하게 보관한다."""

from __future__ import annotations

import base64
import io
import re
import uuid
import warnings
from pathlib import Path

from PIL import Image

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"
MAX_SIDE = 2048  # 긴 변 최대 픽셀 (토큰/메모리 절약)
MAX_BYTES = 8 * 1024 * 1024  # 디코드 전 base64 원본 상한 (~6MB 이미지)
MAX_PIXELS = 24_000_000  # 디코드 폭탄 방지: 총 픽셀 상한 (24MP)

# 압축 폭탄 심층 방어: 상한 이상 픽셀은 Pillow 가 경고→예외로 거부
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
warnings.simplefilter("error", Image.DecompressionBombWarning)

_DATA_URL_RE = re.compile(r"^data:(?P<mime>image/[\w.+-]+);base64,(?P<data>.+)$", re.DOTALL)


def _ensure_dir() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def save_data_url(data_url: str) -> str:
    """data:image/...;base64,... 를 저장하고 파일명을 반환한다.

    Pillow 로 다시 인코딩해 손상/악성 파일을 걸러내고 크기를 정규화한다.
    """
    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        raise ValueError("지원하지 않는 이미지 형식입니다.")
    raw = m.group("data")
    if len(raw) > MAX_BYTES:
        raise ValueError("이미지가 너무 큽니다 (최대 약 9MB).")
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("이미지 디코딩에 실패했습니다.") from exc

    try:
        image = Image.open(io.BytesIO(blob))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("이미지 해상도가 너무 큽니다 (최대 24MP).") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError("올바른 이미지 파일이 아닙니다.") from exc

    w, h = image.size
    if w * h > MAX_PIXELS:
        raise ValueError("이미지 해상도가 너무 큽니다 (최대 24MP).")

    try:
        image.load()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("이미지를 처리할 수 없습니다.") from exc

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

    _ensure_dir()
    filename = f"{uuid.uuid4().hex}.png"
    image.save(UPLOADS_DIR / filename, format="PNG")
    return filename


def load_bytes(filename: str) -> bytes | None:
    """파일명(경로 없음)으로 이미지 바이트를 읽는다. 경로 이탈은 차단."""
    safe = Path(filename).name  # basename only → path traversal 방지
    path = UPLOADS_DIR / safe
    if not path.is_file():
        return None
    return path.read_bytes()


def delete_files(filenames: list[str]) -> None:
    for name in filenames:
        safe = Path(name).name
        path = UPLOADS_DIR / safe
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass

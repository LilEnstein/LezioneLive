"""Ảnh gửi kèm câu hỏi trong chat: giải mã data URL, chuẩn hoá (như ảnh bảng), lưu file, dựng block ảnh cho Claude."""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db.models import ChatAttachment
from app.llm.board_ocr import BoardError, normalize_image

MAX_IMAGES = 4  # mỗi câu hỏi
MAX_IMAGE_BYTES = 10 * 1024 * 1024
HISTORY_IMAGES = 6  # ảnh cũ tối đa gửi lại trong lịch sử cuộc trò chuyện (~1.6k token/ảnh)
MIN_EDGE = 16  # ảnh chụp một dòng công thức có thể rất thấp


class ChatImageError(ValueError):
    pass


@dataclass
class SavedImage:
    image_path: str
    thumb_path: str
    width: int
    height: int


def chat_folder(data_dir: Path, session_id: int) -> Path:
    return data_dir / "chat" / str(session_id)


def decode_image(data_url: str) -> bytes:
    """Nhận data URL (``data:image/...;base64,...``) hoặc base64 trần."""
    payload = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
    if len(payload) > MAX_IMAGE_BYTES * 4 // 3 + 4:
        raise ChatImageError("Ảnh quá lớn (tối đa 10 MB).")
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ChatImageError("Dữ liệu ảnh không hợp lệ.") from e


def save_images(data_dir: Path, session_id: int, raws: list[bytes]) -> list[SavedImage]:
    folder = chat_folder(data_dir, session_id)
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[SavedImage] = []
    try:
        for raw in raws:
            try:
                image, thumb, w, h = normalize_image(raw, min_edge=MIN_EDGE)
            except BoardError as e:
                raise ChatImageError(str(e)) from e
            stem = uuid.uuid4().hex
            image_path, thumb_path = folder / f"{stem}.jpg", folder / f"{stem}_thumb.jpg"
            image_path.write_bytes(image)
            thumb_path.write_bytes(thumb)
            saved.append(SavedImage(str(image_path), str(thumb_path), w, h))
    except ChatImageError:
        delete_files([p for s in saved for p in (s.image_path, s.thumb_path)])
        raise
    return saved


def delete_files(paths: list[str]) -> None:
    for p in paths:
        Path(p).unlink(missing_ok=True)


def image_block(path: str) -> dict[str, Any] | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(data).decode("ascii")},
    }


def attachment_out(a: ChatAttachment) -> dict[str, Any]:
    return {
        "id": a.id,
        "url": f"/api/chat/attachments/{a.id}",
        "thumb_url": f"/api/chat/attachments/{a.id}?thumb=1",
        "width": a.width,
        "height": a.height,
    }

"""Ảnh gửi kèm câu hỏi trong chat: lưu/chuẩn hoá, gửi cho Claude (API và Claude Code), lịch sử, xoá file."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from sqlmodel import select

from app.db.models import ChatAttachment, ChatThread
from app.db.session import db_session
from app.llm import claude_code as cc_mod
from app.llm.claude_code import ClaudeCodeClient, _user_content

from .test_api import add_segments, new_session
from .test_claude_code import FakePopen, _lines


def data_url(w: int = 300, h: int = 40, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format=fmt)
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def ask(client, sid: int, message: str, thread_id: int | None = None, images: list[str] | None = None):
    body: dict = {"message": message, "images": images or []}
    if thread_id is not None:
        body["thread_id"] = thread_id
    with client.stream("POST", f"/api/sessions/{sid}/chat", json=body) as r:
        text = "".join(r.iter_text())
        return r.status_code, text


def image_blocks(call) -> list[list[dict]]:
    """Block ảnh theo từng tin nhắn của lượt gọi LLM."""
    return [[b for b in m["content"] if b.get("type") == "image"] for m in call.messages]


def test_image_sent_with_question_and_kept_in_history(client, harness):
    sid = new_session(client)
    add_segments(client, sid, [("Oggi parliamo di derivate.", "Hôm nay nói về đạo hàm.")])

    status, body = ask(client, sid, "Giải bài trong ảnh", images=[data_url(), data_url(64, 64, "JPEG")])
    assert status == 200 and "event: done" in body
    start = json.loads(body.split("\n\n")[0].split("\n")[1][6:])
    tid = start["thread_id"]
    final = image_blocks(harness.llm.calls[-1])[-1]
    assert len(final) == 2 and final[0]["source"]["media_type"] == "image/jpeg"

    hist = client.get(f"/api/sessions/{sid}/chat", params={"thread_id": tid}).json()
    imgs = hist[0]["images"]
    assert hist[0]["role"] == "user" and len(imgs) == 2 and hist[1]["images"] == []
    r = client.get(imgs[0]["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert client.get(imgs[0]["thumb_url"]).status_code == 200

    # Câu hỏi tiếp theo trong cùng cuộc trò chuyện: ảnh cũ vẫn nằm trong lịch sử gửi Claude.
    ask(client, sid, "Còn câu 2 thì sao?", tid)
    blocks = image_blocks(harness.llm.calls[-1])
    assert sum(len(b) for b in blocks) == 2 and blocks[-1] == []

    # Cuộc trò chuyện khác không mang ảnh theo.
    ask(client, sid, "Câu hỏi mới")
    assert sum(len(b) for b in image_blocks(harness.llm.calls[-1])) == 0

    with db_session(client.ctx.engine) as s:
        paths = [a.image_path for a in s.exec(select(ChatAttachment))]
    assert all(Path(p).exists() for p in paths)
    client.delete(f"/api/chat/threads/{tid}")
    assert not any(Path(p).exists() for p in paths)
    with db_session(client.ctx.engine) as s:
        assert not list(s.exec(select(ChatAttachment)))


def test_bad_image_rejected_without_creating_thread(client):
    sid = new_session(client)
    r = client.post(f"/api/sessions/{sid}/chat", json={"message": "x", "images": ["data:image/png;base64,bm90IGFuIGltYWdl"]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "BAD_IMAGE"
    with db_session(client.ctx.engine) as s:
        assert not list(s.exec(select(ChatThread).where(ChatThread.session_id == sid)))

    r = client.post(f"/api/sessions/{sid}/chat", json={"message": "x", "mode": "compose_it", "images": [data_url()]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "IMAGES_NOT_SUPPORTED"
    r = client.post(f"/api/sessions/{sid}/chat", json={"message": "x", "images": [data_url()] * 5})
    assert r.status_code == 422


def test_transparent_png_gets_white_background(tmp_path):
    from app.services.chat_images import save_images

    buf = io.BytesIO()
    Image.new("RGBA", (200, 30), (0, 0, 0, 0)).save(buf, format="PNG")  # nền trong suốt, pixel "đen"
    [saved] = save_images(tmp_path, 1, [buf.getvalue()])
    with Image.open(saved.image_path) as im:
        assert im.convert("RGB").getpixel((5, 5)) >= (250, 250, 250)
    assert (saved.width, saved.height) == (200, 30)


def test_user_content_labels_images():
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}
    assert _user_content([{"role": "user", "content": "chỉ chữ"}]) == "chỉ chữ"
    blocks = _user_content(
        [
            {"role": "user", "content": [img, {"type": "text", "text": "Câu 1?"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Đáp 1"}]},
            {"role": "user", "content": [img, {"type": "text", "text": "Câu 2?", "cache_control": {"type": "ephemeral"}}]},
        ]
    )
    assert isinstance(blocks, list)
    assert [b.get("text") for b in blocks[:4]] == ["[Ảnh 1]", None, "[Ảnh 2]", None]
    assert blocks[1] == {"type": "image", "source": img["source"]}
    text = blocks[-1]["text"]
    assert "[Ảnh 1]" in text and "[Ảnh 2]" in text and text.rstrip().endswith("Câu 2?")


async def test_claude_code_sends_images_as_stream_json(tmp_path, monkeypatch):
    sent: dict[str, str] = {}

    class CapturingPopen(FakePopen):
        def __init__(self, args, **kwargs):
            super().__init__(args, **kwargs)

            class Stdin(io.StringIO):
                def close(self_inner):
                    sent["stdin"] = self_inner.getvalue()

            self.stdin = Stdin()

    (tmp_path / "claude.exe").write_text("")
    monkeypatch.setattr(cc_mod.subprocess, "Popen", CapturingPopen)
    FakePopen.stdout_text = _lines({"type": "result", "subtype": "success", "is_error": False, "result": "x^2"})
    client = ClaudeCodeClient(tmp_path / "cc", exe_finder=lambda: str(tmp_path / "claude.exe"))
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}
    res = await client.stream(
        purpose="chat",
        session_id=1,
        model="haiku",
        max_tokens=100,
        messages=[{"role": "user", "content": [img, {"type": "text", "text": "Đọc ảnh"}]}],
    )
    assert res.text == "x^2"
    args = CapturingPopen.last_args
    assert args[args.index("--input-format") + 1] == "stream-json"
    msg = json.loads(sent["stdin"])
    assert msg["type"] == "user" and msg["message"]["content"][1] == img

    # Không có ảnh: vẫn gửi text thuần như trước.
    await client.stream(purpose="t", session_id=None, model="m", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
    assert "--input-format" not in CapturingPopen.last_args and sent["stdin"] == "hi"


@pytest.fixture(autouse=True)
def _reset_fake_popen():
    yield
    FakePopen.stdout_text = ""

"""Nhiều cuộc trò chuyện (thread) hỏi đáp trong một buổi học: API, lịch sử tách biệt, migration, export."""

from __future__ import annotations

import json

from sqlmodel import select

from app.db.migrations import m0003_chat_threads
from app.db.models import ChatMessage, ChatThread
from app.db.session import db_session
from app.services.chat_threads import TITLE_MAX, thread_title

from .test_api import add_segments, new_session


def ask(client, sid: int, message: str, thread_id: int | None = None) -> tuple[int, dict]:
    body: dict = {"message": message}
    if thread_id is not None:
        body["thread_id"] = thread_id
    with client.stream("POST", f"/api/sessions/{sid}/chat", json=body) as r:
        assert r.status_code == 200, r.read()
        text = "".join(r.iter_text())
    events = [(blk.split("\n")[0][7:], json.loads(blk.split("\n")[1][6:])) for blk in text.strip().split("\n\n")]
    assert events[0][0] == "start" and events[-1][0] == "done"
    return events[0][1]["thread_id"], events[-1][1]["message"]


def test_thread_title():
    assert thread_title("  Overfitting   là gì?\nthêm dòng") == "Overfitting là gì?"
    long = "Giải thích " + "rất " * 40 + "kỹ"
    title = thread_title(long)
    assert len(title) <= TITLE_MAX and title.endswith("…")
    assert thread_title("\n\n") == ""


def test_threads_have_separate_history(client, harness):
    sid = new_session(client)
    add_segments(client, sid, [("Oggi parliamo di overfitting.", "Hôm nay nói về overfitting.")])

    t1, msg = ask(client, sid, "Overfitting là gì?")
    assert t1 and msg["thread_id"] == t1
    ask(client, sid, "Cho ví dụ", t1)

    # Cuộc trò chuyện mới: không mang lịch sử của thread 1 vào ngữ cảnh.
    created = client.post(f"/api/sessions/{sid}/chat/threads", json={}).json()
    t2, _ = ask(client, sid, "Regularization là gì?", created["id"])
    assert t2 == created["id"]
    texts = [b.get("text", "") for m in harness.llm.calls[-1].messages for b in m["content"]]
    assert not any("Overfitting là gì?" in t or "Cho ví dụ" in t for t in texts)

    threads = client.get(f"/api/sessions/{sid}/chat/threads").json()
    assert [t["id"] for t in threads] == [t2, t1]  # mới cập nhật trước
    assert threads[0]["title"] == "Regularization là gì?" and threads[0]["message_count"] == 2
    assert threads[1]["title"] == "Overfitting là gì?" and threads[1]["message_count"] == 4

    hist = client.get(f"/api/sessions/{sid}/chat", params={"thread_id": t1}).json()
    assert [m["content"] for m in hist if m["role"] == "user"] == ["Overfitting là gì?", "Cho ví dụ"]

    r = client.patch(f"/api/chat/threads/{t1}", json={"title": "  Overfitting  "})
    assert r.status_code == 200 and r.json()["title"] == "Overfitting"

    assert client.delete(f"/api/chat/threads/{t1}").json() == {"deleted": True}
    assert [t["id"] for t in client.get(f"/api/sessions/{sid}/chat/threads").json()] == [t2]
    with db_session(client.ctx.engine) as s:
        assert not list(s.exec(select(ChatMessage).where(ChatMessage.thread_id == t1)))


def test_thread_must_belong_to_session(client):
    sid = new_session(client)
    other = new_session(client)
    thread = client.post(f"/api/sessions/{other}/chat/threads", json={}).json()
    r = client.post(f"/api/sessions/{sid}/chat", json={"message": "x", "thread_id": thread["id"]})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "THREAD_NOT_FOUND"
    assert client.get(f"/api/sessions/{sid}/chat", params={"thread_id": thread["id"]}).status_code == 404
    assert client.delete("/api/chat/threads/99999").status_code == 404


def test_migration_backfills_legacy_messages(client):
    sid = new_session(client)
    engine = client.ctx.engine
    with db_session(engine) as s:
        s.add(ChatMessage(session_id=sid, role="user", content="Câu hỏi cũ", mode="qa"))
        s.add(ChatMessage(session_id=sid, role="assistant", content="Trả lời cũ", mode="qa"))
        s.add(ChatMessage(session_id=sid, role="user", content="Soạn", mode="compose_it"))
        s.commit()
    for _ in range(2):  # idempotent
        with engine.begin() as conn:
            m0003_chat_threads.upgrade(conn)
    with db_session(engine) as s:
        threads = list(s.exec(select(ChatThread).where(ChatThread.session_id == sid)))
        msgs = list(s.exec(select(ChatMessage).where(ChatMessage.session_id == sid)))
    assert len(threads) == 1 and threads[0].title == "Câu hỏi cũ"
    assert [m.thread_id for m in msgs] == [threads[0].id, threads[0].id, None]


def test_export_groups_threads(client):
    sid = new_session(client)
    add_segments(client, sid, [("Oggi parliamo di overfitting.", "Hôm nay nói về overfitting.")])
    ask(client, sid, "Overfitting là gì?")
    ask(client, sid, "Dropout là gì?")
    md = client.get(f"/api/sessions/{sid}/export", params={"format": "md"}).text
    assert "### 💬 Overfitting là gì?" in md and "### 💬 Dropout là gì?" in md

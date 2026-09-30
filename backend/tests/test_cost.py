"""Tính chi phí theo bảng giá (FR-COST-01), cảnh báo ngưỡng đúng 1 lần (AC-11)."""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.config import DEFAULT_PRICING, ModelPricing
from app.db.models import LectureSession, LlmUsage
from app.db.session import db_session
from app.llm.client import TokenUsage
from app.services.cost import compute_cost, price_for


def test_price_lookup_uses_longest_prefix():
    assert price_for("claude-haiku-4-5-20251001", DEFAULT_PRICING) == DEFAULT_PRICING["claude-haiku-4-5"]
    assert price_for("claude-opus-5-5", DEFAULT_PRICING) == DEFAULT_PRICING["claude-opus-5-5"]
    assert price_for("claude-opus-5", DEFAULT_PRICING) == DEFAULT_PRICING["claude-opus-5"]
    assert price_for("gpt-x", DEFAULT_PRICING) is None


def test_compute_cost_with_cache_tiers():
    price = ModelPricing(input=2.0, output=10.0, cache_write_5m=2.5, cache_write_1h=4.0, cache_read=0.2)
    cost = compute_cost(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=500_000,
        cache_write_tokens=300_000,
        cache_write_1h_tokens=100_000,
        price=price,
    )
    expected = 2.0 + 1.0 + 0.2 * 2.5 + 0.1 * 4.0 + 0.5 * 0.2
    assert cost == pytest.approx(expected)


async def test_session_cost_matches_usage_and_threshold_warns_once(actx):
    ctx = actx
    ctx.settings.update({"cost_threshold_usd": 0.01})
    with db_session(ctx.engine) as s:
        sess = LectureSession(title="t")
        s.add(sess)
        s.commit()
        s.refresh(sess)
    queue = ctx.hub.subscribe(sess.id)
    usage = TokenUsage(input_tokens=2000, output_tokens=500)
    for _ in range(5):
        ctx.usage.record(session_id=sess.id, purpose="translate", model="claude-haiku-4-5-20251001", usage=usage)

    with db_session(ctx.engine) as s:
        rows = list(s.exec(select(LlmUsage).where(LlmUsage.session_id == sess.id)))
        stored = s.get(LectureSession, sess.id)
    manual = sum((r.input_tokens * 1.0 + r.output_tokens * 5.0) / 1e6 for r in rows)
    assert stored.cost_usd_est == pytest.approx(manual, rel=1e-3)
    assert ctx.usage.session_cost(sess.id) == pytest.approx(manual, rel=1e-3)
    breakdown = ctx.usage.breakdown(sess.id)
    assert breakdown["by_purpose"][0]["purpose"] == "translate"
    assert breakdown["by_purpose"][0]["requests"] == 5

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    warnings = [e for e in events if e["type"] == "warning" and e["code"] == "COST_THRESHOLD"]
    assert len(warnings) == 1
    assert any(e["type"] == "cost.update" for e in events)

    # Đổi ngưỡng → cho phép cảnh báo lại khi vượt ngưỡng mới
    ctx.settings.update({"cost_threshold_usd": 0.02})
    for _ in range(5):
        ctx.usage.record(session_id=sess.id, purpose="chat", model="claude-sonnet-5", usage=usage)
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert len([e for e in events if e.get("code") == "COST_THRESHOLD"]) == 1


async def test_pricing_change_recomputes_session_cost(actx):
    ctx = actx
    with db_session(ctx.engine) as s:
        sess = LectureSession(title="t")
        s.add(sess)
        s.commit()
        s.refresh(sess)
    ctx.usage.record(session_id=sess.id, purpose="chat", model="claude-sonnet-5",
                     usage=TokenUsage(input_tokens=1_000_000))
    assert ctx.usage.session_cost(sess.id) == pytest.approx(2.0)
    pricing = {k: v.model_dump() for k, v in ctx.settings.get().pricing.items()}
    pricing["claude-sonnet-5"]["input"] = 3.0
    ctx.settings.update({"pricing": pricing})
    assert ctx.usage.session_cost(sess.id) == pytest.approx(3.0)

"""Đếm token và ước tính chi phí (FR-COST-01/02).

Chi phí phiên = tổng ``llm_usage`` × bảng giá cấu hình hiện tại (AC-11: sai số ≤ 1%), nên luôn
tính lại từ số token chứ không cộng dồn giá trị cũ.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, func
from sqlmodel import select

from app.config import ModelPricing
from app.db.models import LectureSession, LlmUsage
from app.db.session import db_session

if TYPE_CHECKING:
    from app.llm.client import TokenUsage
    from app.pipeline.events import EventHub
    from app.services.settings_store import SettingsStore

log = logging.getLogger(__name__)


def price_for(model: str, pricing: dict[str, ModelPricing]) -> ModelPricing | None:
    """Khớp tiền tố dài nhất: ``claude-haiku-4-5-20251001`` -> ``claude-haiku-4-5``."""
    m = model.lower()
    best: tuple[int, ModelPricing] | None = None
    for key, price in pricing.items():
        k = key.lower()
        if m == k or m.startswith(k):
            if best is None or len(k) > best[0]:
                best = (len(k), price)
    return best[1] if best else None


def compute_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    cache_write_1h_tokens: int,
    price: ModelPricing | None,
) -> float:
    if price is None:
        return 0.0
    cw_1h = min(cache_write_1h_tokens, cache_write_tokens)
    cw_5m = max(0, cache_write_tokens - cw_1h)
    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cw_5m * price.cache_write_5m
        + cw_1h * price.cache_write_1h
        + cache_read_tokens * price.cache_read
    )
    return total / 1_000_000


def row_cost(row: LlmUsage, pricing: dict[str, ModelPricing]) -> float:
    return compute_cost(
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cache_read_tokens=row.cache_read_tokens,
        cache_write_tokens=row.cache_write_tokens,
        cache_write_1h_tokens=row.cache_write_1h_tokens,
        price=price_for(row.model, pricing),
    )


class UsageRecorder:
    def __init__(self, engine: Engine, settings: SettingsStore, hub: EventHub | None = None) -> None:
        self._engine = engine
        self._settings = settings
        self._hub = hub

    def record(self, *, session_id: int | None, purpose: str, model: str, usage: TokenUsage) -> LlmUsage:
        pricing = self._settings.get().pricing
        price = price_for(model, pricing)
        if price is None:
            log.warning("Chưa có giá cho model %s trong cài đặt; chi phí tính là 0", model)
        row = LlmUsage(
            session_id=session_id,
            purpose=purpose,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_write_1h_tokens=usage.cache_write_1h_tokens,
            cost_usd_est=compute_cost(price=price, **usage.__dict__),
        )
        with db_session(self._engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        if session_id is not None:
            self.refresh_session(session_id)
        return row

    def session_rows(self, session_id: int) -> list[LlmUsage]:
        with db_session(self._engine) as s:
            return list(s.exec(select(LlmUsage).where(LlmUsage.session_id == session_id)))

    def session_cost(self, session_id: int) -> float:
        pricing = self._settings.get().pricing
        with db_session(self._engine) as s:
            grouped = s.exec(
                select(
                    LlmUsage.model,
                    func.sum(LlmUsage.input_tokens),
                    func.sum(LlmUsage.output_tokens),
                    func.sum(LlmUsage.cache_read_tokens),
                    func.sum(LlmUsage.cache_write_tokens),
                    func.sum(LlmUsage.cache_write_1h_tokens),
                )
                .where(LlmUsage.session_id == session_id)
                .group_by(LlmUsage.model)
            ).all()
        total = 0.0
        for model, inp, out, cr, cw, cw1h in grouped:
            total += compute_cost(
                input_tokens=inp or 0,
                output_tokens=out or 0,
                cache_read_tokens=cr or 0,
                cache_write_tokens=cw or 0,
                cache_write_1h_tokens=cw1h or 0,
                price=price_for(model, pricing),
            )
        return total

    def breakdown(self, session_id: int) -> dict[str, Any]:
        pricing = self._settings.get().pricing
        by_purpose: dict[str, dict[str, Any]] = {}
        for row in self.session_rows(session_id):
            item = by_purpose.setdefault(
                row.purpose,
                {
                    "purpose": row.purpose,
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cost_usd": 0.0,
                    "models": set(),
                },
            )
            item["requests"] += 1
            item["input_tokens"] += row.input_tokens
            item["output_tokens"] += row.output_tokens
            item["cache_read_tokens"] += row.cache_read_tokens
            item["cache_write_tokens"] += row.cache_write_tokens
            item["cost_usd"] += row_cost(row, pricing)
            item["models"].add(row.model)
        items = []
        for item in by_purpose.values():
            item["models"] = sorted(item["models"])
            items.append(item)
        items.sort(key=lambda x: -x["cost_usd"])
        total = sum(i["cost_usd"] for i in items)
        return {
            "session_id": session_id,
            "total_usd": total,
            "threshold_usd": self._settings.get().cost_threshold_usd,
            "by_purpose": items,
        }

    def refresh_session(self, session_id: int) -> float:
        """Cập nhật ``cost_usd_est`` của phiên, phát ``cost.update`` và cảnh báo ngưỡng (đúng 1 lần)."""
        total = self.session_cost(session_id)
        threshold = self._settings.get().cost_threshold_usd
        crossed = False
        with db_session(self._engine) as s:
            sess = s.get(LectureSession, session_id)
            if sess is None:
                return total
            sess.cost_usd_est = total
            if threshold is not None and total >= threshold and not sess.cost_warned:
                sess.cost_warned = True
                crossed = True
            s.add(sess)
            s.commit()
        if self._hub is not None:
            self._hub.publish(
                session_id, {"type": "cost.update", "session_cost_usd": round(total, 6), "threshold_usd": threshold}
            )
            if crossed:
                self._hub.publish(
                    session_id,
                    {
                        "type": "warning",
                        "code": "COST_THRESHOLD",
                        "message": f"Chi phí phiên đã vượt ngưỡng ${threshold:.2f} (hiện tại ${total:.2f}).",
                    },
                )
        return total

    def reset_warnings(self) -> None:
        """Khi người dùng đổi ngưỡng: cho phép cảnh báo lại với các phiên chưa kết thúc."""
        with db_session(self._engine) as s:
            for sess in s.exec(select(LectureSession).where(LectureSession.status != "ended")):
                sess.cost_warned = False
                s.add(sess)
            s.commit()

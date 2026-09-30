"""Đọc/ghi ``AppSettings`` trong bảng ``app_setting`` (một bản ghi JSON)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine

from app.config import AppSettings
from app.db.models import AppSettingRow, utcnow
from app.db.session import db_session

log = logging.getLogger(__name__)

Listener = Callable[[AppSettings, AppSettings], None]


class SettingsStore:
    KEY = "app"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._cached: AppSettings | None = None
        self._listeners: list[Listener] = []

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def get(self) -> AppSettings:
        if self._cached is None:
            with db_session(self._engine) as s:
                row = s.get(AppSettingRow, self.KEY)
            if row is None:
                self._cached = AppSettings()
            else:
                try:
                    self._cached = AppSettings.model_validate(json.loads(row.value))
                except (ValueError, ValidationError):
                    log.warning("Cài đặt trong DB không hợp lệ, dùng mặc định")
                    self._cached = AppSettings()
        return self._cached

    def update(self, patch: dict[str, Any]) -> AppSettings:
        unknown = set(patch) - set(AppSettings.model_fields)
        if unknown:
            raise ValueError(f"Khoá cài đặt không hợp lệ: {', '.join(sorted(unknown))}")
        old = self.get()
        merged = old.model_dump() | patch
        new = AppSettings.model_validate(merged)
        with db_session(self._engine) as s:
            row = s.get(AppSettingRow, self.KEY)
            payload = new.model_dump_json()
            if row is None:
                row = AppSettingRow(key=self.KEY, value=payload)
            else:
                row.value = payload
                row.updated_at = utcnow()
            s.add(row)
            s.commit()
        self._cached = new
        for listener in self._listeners:
            try:
                listener(old, new)
            except Exception:  # pragma: no cover - listener không được làm hỏng việc lưu
                log.exception("Listener cài đặt lỗi")
        return new

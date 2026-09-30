"""Nạp và điền prompt template ``prompts/*.md`` (tên file có version, ví dụ ``translate_v1``).

Chỉ thay các biến ``{ten_bien}`` được truyền vào, nên template có thể chứa JSON/ngoặc nhọn tuỳ ý.
Dòng ``<<<VOLATILE>>>`` tách phần ổn định (đặt cache_control) và phần thay đổi theo từng request.
"""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"
VOLATILE_MARKER = "<<<VOLATILE>>>"
_VAR = re.compile(r"\{([a-z_]+)\}")


class PromptLibrary:
    def __init__(self, directory: Path = PROMPTS_DIR) -> None:
        self._dir = directory
        self._cache: dict[str, str] = {}

    def load(self, name: str) -> str:
        if name not in self._cache:
            self._cache[name] = (self._dir / f"{name}.md").read_text(encoding="utf-8")
        return self._cache[name]

    @staticmethod
    def fill(template: str, **values: object) -> str:
        return _VAR.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)

    def render(self, name: str, **values: object) -> str:
        return self.fill(self.load(name).replace(VOLATILE_MARKER + "\n", ""), **values).strip()

    def render_parts(self, name: str, **values: object) -> tuple[str, str]:
        template = self.load(name)
        stable, _, volatile = template.partition(VOLATILE_MARKER)
        return self.fill(stable, **values).strip(), self.fill(volatile, **values).strip()

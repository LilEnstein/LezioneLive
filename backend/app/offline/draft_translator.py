"""Dịch Ý → Việt bằng model cục bộ NLLB-200 (CTranslate2 int8, chạy CPU, không cần mạng).

Hai vai trò:
- Dịch nháp (IC-FR-51): khi mất mạng hoặc Claude không dùng được (hết hạn mức gói, thiếu/sai key), bản nháp được thay
  bằng bản dịch Claude sau đó.
- Nguồn dịch chính (``translate_provider = "local"``): phương án dự phòng miễn phí, không phụ thuộc Claude.

``ctranslate2`` và ``tokenizers`` đã có sẵn nhờ faster-whisper, không cần cài thêm. Chất lượng thấp hơn Claude, nhất là
thuật ngữ chuyên ngành; để bù, thuật ngữ trong bảng thuật ngữ được thay bằng mã giữ chỗ ``T1``, ``T2``… trước khi dịch
(NLLB giữ nguyên mã và đặt đúng vị trí trong câu), rồi thay mã bằng bản tiếng Việt. Câu nào model làm mất mã thì dịch lại
với thuật ngữ tiếng Việt chèn thẳng trong ngoặc kép. Đoạn dài nhiều câu được tách theo dấu kết câu, dịch chung một lượt.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from app.config import LOCAL_MODEL_CHOICES

log = logging.getLogger(__name__)

SRC_LANG = "ita_Latn"
TGT_LANG = "vie_Latn"
EN_LANG = "eng_Latn"
# model.bin cuối cùng: file lớn nhất, theo dõi tiến độ tải.
REQUIRED_FILES = ("config.json", "tokenizer.json", "model.bin")
VOCAB_FILES = ("shared_vocabulary.txt", "shared_vocabulary.json")  # tuỳ bản chuyển đổi CTranslate2
OPTIONAL_FILES = ("special_tokens_map.json",)
BEAM_SIZE = 3
MAX_SOURCE_TOKENS = 400
# Chia CPU với Whisper (cũng chạy CPU): tối đa 4 luồng.
THREADS = max(2, min(4, (os.cpu_count() or 4) // 3))

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=\S)")
_LEADING_DASH = re.compile(r"^\s*[-–—]\s+")
_QUOTES = ("“", "”", '"')
_PLACEHOLDER_LIKE = re.compile(r"(?<!\w)T\s?\d+(?!\d)", re.IGNORECASE)

State = Literal["idle", "loading", "ready", "error"]


def split_sentences(text: str) -> list[str]:
    """Tách đoạn theo dấu kết câu (NLLB dịch từng câu tốt hơn một khối dài nhiều ý); mẩu 1 từ gộp vào câu trước."""
    merged: list[str] = []
    for part in _SENTENCE_END.split(text.strip()):
        part = part.strip()
        if not part:
            continue
        if merged and len(part.split()) < 2:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


class GlossaryInjector:
    """Thay thuật ngữ tiếng Ý trong câu nguồn (cụm dài trước, không phân biệt hoa thường)."""

    def __init__(self, pairs: Iterable[tuple[str, str]]) -> None:
        self._map: dict[str, str] = {}
        for term_it, term_vi in pairs:
            key = " ".join(term_it.split()).lower()
            if key and term_vi.strip() and key not in self._map:
                self._map[key] = term_vi.strip()
        terms = sorted(self._map, key=len, reverse=True)
        alternation = "|".join(r"\s+".join(re.escape(w) for w in t.split()) for t in terms)
        self._re = re.compile(rf"(?<!\w)({alternation})(?!\w)", re.IGNORECASE) if terms else None

    def _term(self, m: re.Match[str]) -> str:
        return self._map[" ".join(m.group(1).split()).lower()]

    def __call__(self, text: str) -> str:
        """Chèn thẳng bản tiếng Việt trong ngoặc kép (dự phòng khi model làm mất mã giữ chỗ)."""
        if self._re is None:
            return text
        return self._re.sub(lambda m: f"“{self._term(m)}”", text)

    def placeholders(self, text: str) -> tuple[str, list[str]]:
        """Thay thuật ngữ bằng mã ``T1``, ``T2``…; trả về (câu đã thay, thuật ngữ tiếng Việt theo thứ tự mã)."""
        if self._re is None or _PLACEHOLDER_LIKE.search(text):  # câu gốc có sẵn chữ dạng "T1": không dùng mã
            return self(text), []
        terms: list[str] = []

        def sub(m: re.Match[str]) -> str:
            terms.append(self._term(m))
            return f"T{len(terms)}"

        return self._re.sub(sub, text), terms


def restore_placeholders(text: str, terms: Sequence[str]) -> str | None:
    """Thay mã ``Tk`` trong câu dịch bằng thuật ngữ; None nếu model làm mất mã nào."""
    for k, term in enumerate(terms, 1):
        pattern = re.compile(rf"(?<!\w)T\s?{k}(?!\d)", re.IGNORECASE)
        if not pattern.search(text):
            return None
        text = pattern.sub(lambda _m, term=term: term, text)
    return text


def clean_output(source: str, text: str) -> str:
    """Bỏ ngoặc kép do bước chèn thuật ngữ thêm vào và gạch đầu dòng NLLB hay tự sinh."""
    for q in _QUOTES:
        if q not in source:
            text = text.replace(q, "")
    if not _LEADING_DASH.match(source):
        text = _LEADING_DASH.sub("", text)
    text = " ".join(text.split())
    if source[:1].isupper() and text[:1].islower():  # thuật ngữ thay vào đứng đầu câu
        text = text[0].upper() + text[1:]
    return text


def _choice(repo: str) -> dict[str, Any]:
    for c in LOCAL_MODEL_CHOICES:
        if c["repo"] == repo:
            return dict(c)
    return {"repo": repo, "label": repo.rsplit("/", 1)[-1], "size_mb": None, "tier": "custom"}


def _cached_file(repo: str, filename: str) -> Path | None:
    try:
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(repo, filename, local_files_only=True))
    except Exception:  # noqa: BLE001 - chưa có trong cache
        return None


def _progress_class(report: Callable[[int, int | None], None]) -> type:
    """Lớp tqdm nhận tiến độ tải từ huggingface_hub mà không in ra console."""
    from tqdm import tqdm

    class Progress(tqdm):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._done = int(kwargs.get("initial") or 0)
            self._expected = kwargs.get("total")
            kwargs["disable"] = True
            super().__init__(*args, **kwargs)

        def update(self, n: float | None = 1) -> bool | None:
            self._done += int(n or 0)
            report(self._done, self._expected or getattr(self, "total", None))
            return super().update(n)

    return Progress


class DraftTranslator:
    """Model NLLB cục bộ: tải về (có tiến độ), nạp vào RAM, dịch theo lô."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.state: State = "idle"
        self.error: str | None = None
        self._translator: Any = None
        self._tokenizer: Any = None
        self._loaded_repo: str | None = None
        self._lock = threading.Lock()  # đổi/giải phóng model
        self._run_lock = threading.Lock()  # một lượt dịch mỗi lúc (không tranh CPU với chính mình)
        self._task: asyncio.Task | None = None
        self._task_key: tuple[str, bool] | None = None
        self._dirs: dict[str, Path] = {}
        self._checked: dict[str, float] = {}
        self._downloads: dict[str, dict[str, Any]] = {}
        self._dl_tasks: dict[str, asyncio.Task] = {}
        self._ready_listeners: list[Callable[[], None]] = []

    # ---------------------------------------------------------------- model
    def _local_dir(self, repo: str) -> Path | None:
        folder = self._dirs.get(repo)
        if folder is not None and (folder / "model.bin").exists():
            return folder
        now = time.monotonic()
        if now - self._checked.get(repo, float("-inf")) < 30:  # /health gọi thường xuyên: không dò cache liên tục
            return None
        self._checked[repo] = now
        paths = [_cached_file(repo, f) for f in REQUIRED_FILES]
        if any(p is None for p in paths) or not any(_cached_file(repo, v) for v in VOCAB_FILES):
            return None
        folder = paths[-1].parent  # type: ignore[union-attr]
        self._dirs[repo] = folder
        return folder

    def cached(self, repo: str | None = None) -> bool:
        return self._local_dir(repo or self.repo) is not None

    @property
    def ready(self) -> bool:
        return self._translator is not None and self._loaded_repo == self.repo

    def add_ready_listener(self, fn: Callable[[], None]) -> None:
        self._ready_listeners.append(fn)

    def set_repo(self, repo: str) -> None:
        """Đổi model đang dùng: giải phóng model cũ khỏi RAM; model mới nạp khi cần."""
        if repo == self.repo:
            return
        with self._lock:
            self.repo = repo
            self._translator = self._tokenizer = self._loaded_repo = None
        self.state, self.error = "idle", None

    def _download_info(self, repo: str) -> dict[str, Any]:
        info = self._downloads.get(repo) or {}
        total = info.get("total")
        progress = round(min(1.0, info.get("done", 0) / total), 3) if total else None
        return {
            "downloading": info.get("state") == "downloading",
            "progress": progress,
            "download_error": info.get("error") if info.get("state") == "error" else None,
        }

    def status(self) -> dict[str, Any]:
        dl = self._download_info(self.repo)
        state = "ready" if self.ready else "downloading" if dl["downloading"] else self.state
        return {
            "model": self.repo,
            "label": _choice(self.repo)["label"],
            "cached": self.cached(),
            "state": state,
            "error": self.error or dl["download_error"],
            "size_mb": _choice(self.repo)["size_mb"] or 0,
            "progress": dl["progress"],
        }

    def catalog(self) -> list[dict[str, Any]]:
        """Các model chọn được (kèm model đang đặt nếu là tên tuỳ chỉnh) và trạng thái tải."""
        repos = [str(c["repo"]) for c in LOCAL_MODEL_CHOICES]
        if self.repo not in repos:
            repos.append(self.repo)
        return [
            {**_choice(repo), **self._download_info(repo), "cached": self.cached(repo), "active": repo == self.repo}
            for repo in repos
        ]

    def _download(self, repo: str) -> Path:
        from huggingface_hub import hf_hub_download

        info = self._downloads[repo]

        def fetch(filename: str, *, track: bool = False) -> Path:
            kwargs: dict[str, Any] = {}
            if track:
                kwargs["tqdm_class"] = _progress_class(lambda done, total: info.update(done=done, total=total))
            return Path(hf_hub_download(repo, filename, **kwargs))

        for f in REQUIRED_FILES[:-1]:
            fetch(f)
        last_err: Exception | None = None
        for vocab in VOCAB_FILES:
            try:
                fetch(vocab)
                break
            except Exception as e:  # noqa: BLE001 - thử tên file từ vựng còn lại
                last_err = e
        else:
            raise last_err or RuntimeError("Thiếu file từ vựng của model")
        for f in OPTIONAL_FILES:
            try:
                fetch(f)
            except Exception:  # noqa: BLE001
                pass
        return fetch(REQUIRED_FILES[-1], track=True).parent

    async def _download_task(self, repo: str) -> Path | None:
        info = self._downloads[repo]
        try:
            folder = await asyncio.to_thread(self._download, repo)
        except Exception as e:  # noqa: BLE001
            info.update(state="error", error=f"Không tải được model {repo}: {type(e).__name__}: {e}")
            log.warning("Tải model dịch cục bộ %s lỗi: %s", repo, e)
            return None
        info.update(state="done", error=None)
        self._dirs[repo] = folder
        self._checked.pop(repo, None)
        return folder

    def start_download(self, repo: str) -> asyncio.Task:
        """Tải model về máy (không nạp) — để chuyển sang model khác mà không làm gián đoạn model đang dùng."""
        task = self._dl_tasks.get(repo)
        if task is None or task.done():
            self._downloads[repo] = {"state": "downloading", "done": 0, "total": None, "error": None}
            task = asyncio.create_task(self._download_task(repo))
            self._dl_tasks[repo] = task
        return task

    def _load(self, folder: Path, repo: str) -> None:
        import ctranslate2
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        translator = ctranslate2.Translator(
            str(folder), device="cpu", compute_type="int8", inter_threads=1, intra_threads=THREADS
        )
        with self._lock:
            if repo == self.repo:  # người dùng có thể đã đổi model trong lúc nạp
                self._tokenizer, self._translator, self._loaded_repo = tokenizer, translator, repo

    async def prepare(self, *, download: bool = True) -> bool:
        """Nạp model đang chọn (tải về trước nếu chưa có và được phép). Trả về True khi sẵn sàng."""
        if self.ready:
            return True
        repo = self.repo
        self._checked.pop(repo, None)
        folder = self._local_dir(repo)
        try:
            if folder is None:
                if not download:
                    return False
                folder = await asyncio.shield(self.start_download(repo))
                if folder is None:
                    if repo == self.repo:
                        self.state, self.error = "error", self._downloads[repo].get("error")
                    return False
            if repo != self.repo:
                return False
            self.state, self.error = "loading", None
            await asyncio.to_thread(self._load, folder, repo)
        except Exception as e:  # noqa: BLE001
            if repo == self.repo:
                self.state, self.error = "error", f"Không nạp được model dịch cục bộ: {type(e).__name__}: {e}"
            log.warning("Model dịch cục bộ lỗi: %s", e)
            return False
        if not self.ready:
            return False
        self.state = "ready"
        for fn in list(self._ready_listeners):
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("Lỗi khi báo model dịch cục bộ sẵn sàng")
        return True

    def start_prepare(self, *, download: bool = True) -> asyncio.Task:
        key = (self.repo, download)
        running = self._task is not None and not self._task.done()
        # Task cũ chỉ dùng lại khi cùng model và đã được phép tải (nếu lần này cần tải).
        if not running or self._task_key is None or self._task_key[0] != self.repo or (download and not self._task_key[1]):
            self._task_key = key
            self._task = asyncio.create_task(self.prepare(download=download))
        return self._task  # type: ignore[return-value]

    # ------------------------------------------------------------ translate
    @staticmethod
    def _decode(tokenizer: Any, tokens: list[str], lang: str) -> str:
        ids = [i for i in (tokenizer.token_to_id(t) for t in tokens if t != lang) if i is not None]
        return tokenizer.decode(ids)

    def _run(self, translator: Any, tokenizer: Any, texts: list[str], langs: list[str]) -> list[str]:
        sources = [
            [SRC_LANG, *tokenizer.encode(t, add_special_tokens=False).tokens[:MAX_SOURCE_TOKENS], "</s>"] for t in texts
        ]
        with self._run_lock:
            results = translator.translate_batch(
                sources,
                target_prefix=[[lang] for lang in langs],
                beam_size=BEAM_SIZE,
                max_decoding_length=min(256, max(len(s) for s in sources) * 3 + 16),
                max_batch_size=16,
            )
        return [self._decode(tokenizer, r.hypotheses[0], lang) for r, lang in zip(results, langs, strict=True)]

    def translate_batch_sync(
        self, texts: Sequence[str], *, glossary: Iterable[tuple[str, str]] = (), with_en: bool = False
    ) -> list[tuple[str, str | None]]:
        """Dịch nhiều đoạn một lượt. Trả về (tiếng Việt, tiếng Anh nếu ``with_en``) cho từng đoạn."""
        with self._lock:
            translator, tokenizer = self._translator, self._tokenizer
        if translator is None or tokenizer is None:
            raise RuntimeError("Model dịch cục bộ chưa sẵn sàng")
        inject = GlossaryInjector(glossary)
        owner: list[int] = []
        originals: list[str] = []
        prepared: list[tuple[str, list[str]]] = []
        for i, text in enumerate(texts):
            for sentence in split_sentences(text):
                owner.append(i)
                originals.append(sentence)
                prepared.append(inject.placeholders(sentence))
        if not originals:
            return [("", None) for _ in texts]
        n = len(originals)
        # Tiếng Anh (khi bật) dịch từ câu gốc: NLLB dịch Ý → Anh thuật ngữ khá tốt, không cần chèn.
        outs = self._run(
            translator,
            tokenizer,
            [src for src, _ in prepared] + (originals if with_en else []),
            [TGT_LANG] * n + ([EN_LANG] * n if with_en else []),
        )
        vi_out = outs[:n]
        retry: list[int] = []
        for k, (_, terms) in enumerate(prepared):
            if terms:
                restored = restore_placeholders(vi_out[k], terms)
                if restored is None:
                    retry.append(k)
                else:
                    vi_out[k] = restored
        if retry:  # model làm mất mã giữ chỗ: dịch lại với thuật ngữ chèn thẳng trong ngoặc kép
            again = self._run(translator, tokenizer, [inject(originals[k]) for k in retry], [TGT_LANG] * len(retry))
            for k, out in zip(retry, again, strict=True):
                vi_out[k] = out
        vi: list[list[str]] = [[] for _ in texts]
        en: list[list[str]] = [[] for _ in texts]
        for k, i in enumerate(owner):
            vi[i].append(clean_output(originals[k], vi_out[k]))
            if with_en:
                en[i].append(clean_output(originals[k], outs[n + k]))
        return [
            (" ".join(filter(None, v)), (" ".join(filter(None, e)) or None) if with_en else None)
            for v, e in zip(vi, en, strict=True)
        ]

    async def translate_many(
        self, texts: Sequence[str], *, glossary: Iterable[tuple[str, str]] = (), with_en: bool = False
    ) -> list[tuple[str, str | None]]:
        return await asyncio.to_thread(self.translate_batch_sync, list(texts), glossary=list(glossary), with_en=with_en)

    async def translate(self, text: str, *, glossary: Iterable[tuple[str, str]] = ()) -> str:
        return (await self.translate_many([text], glossary=glossary))[0][0]

"""Phát file WAV qua WebSocket như một nguồn âm thanh (kiểm thử soak / demo, mục 17).

Ví dụ:
    # tạo phiên mới và phát file mẫu 1 lần theo thời gian thực
    backend/.venv/Scripts/python scripts/soak.py --wav backend/tests/fixtures/audio/speech_en_pauses.wav

    # soak 3,5 giờ: lặp file, ghi CSV thống kê mỗi phút (RAM backend, số segment, độ trễ)
    backend/.venv/Scripts/python scripts/soak.py --wav bai_giang.wav --hours 3.5 --report soak.csv

Yêu cầu backend đang chạy (python -m app). Không cần API key (khi đó chỉ có phụ đề tiếng Ý).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.audio.pcm import float32_to_pcm16, read_wav, resample  # noqa: E402

FRAME_BYTES = 3200  # 100 ms PCM16 mono 16 kHz


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--wav", required=True, type=Path)
    parser.add_argument("--session-id", type=int, help="Dùng phiên có sẵn (mặc định: tạo phiên mới)")
    parser.add_argument("--title", default="Soak test")
    parser.add_argument("--hours", type=float, default=0.0, help="Lặp file trong N giờ (0 = phát 1 lần)")
    parser.add_argument("--speed", type=float, default=1.0, help="Tốc độ phát (1 = thời gian thực)")
    parser.add_argument("--report", type=Path, help="Ghi thống kê mỗi phút ra CSV")
    parser.add_argument("--end", action="store_true", help="Kết thúc phiên khi phát xong")
    args = parser.parse_args()

    from websockets.asyncio.client import connect

    audio, sr = read_wav(args.wav)
    pcm = float32_to_pcm16(resample(audio, sr, 16000))
    frames = [pcm[i : i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)]
    silence = [bytes(FRAME_BYTES)] * 20

    async with httpx.AsyncClient(base_url=args.url, timeout=30) as http:
        if args.session_id is None:
            r = await http.post("/api/sessions", json={"title": args.title, "audio_mode": "tab"})
            r.raise_for_status()
            session_id = r.json()["session_id"]
        else:
            session_id = args.session_id
            await http.post(f"/api/sessions/{session_id}/resume")
        print(f"Phiên {session_id}: {args.url}/live/{session_id}")

        ws_url = args.url.replace("http", "ws", 1) + f"/ws/session/{session_id}"
        counts: Counter[str] = Counter()
        started = time.monotonic()
        deadline = started + args.hours * 3600 if args.hours > 0 else None
        writer = None
        report_file = None
        if args.report:
            report_file = args.report.open("w", newline="", encoding="utf-8")
            writer = csv.writer(report_file)
            writer.writerow(["elapsed_min", "rss_mb", "segments", "translated", "failed", "it_p50_ms", "vi_p50_ms"])

        async with connect(ws_url, origin=args.url, max_size=None) as ws:

            async def receiver() -> None:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    ev = json.loads(raw)
                    counts[ev["type"]] += 1
                    if ev["type"] == "segment.transcribed":
                        print(f"[{ev['t_start_ms'] / 1000:7.1f}s] IT: {ev['text_it']}")
                    elif ev["type"] == "segment.translated":
                        print(f"            VI: {ev['text_vi']}")
                    elif ev["type"] == "warning":
                        print(f"  ⚠ {ev['code']}: {ev['message']}")

            async def reporter() -> None:
                while True:
                    await asyncio.sleep(60)
                    health = (await http.get("/api/health")).json()
                    metrics = (await http.get(f"/api/sessions/{session_id}/metrics")).json()
                    row = [
                        round((time.monotonic() - started) / 60, 1),
                        health.get("process", {}).get("rss_mb"),
                        counts["segment.transcribed"],
                        counts["segment.translated"],
                        counts["segment.failed"],
                        metrics["it_latency_ms"]["p50"],
                        metrics["vi_latency_ms"]["p50"],
                    ]
                    print("📈", dict(zip(["phút", "RAM MB", "segment", "dịch xong", "lỗi", "it_p50", "vi_p50"], row)))
                    if writer is not None:
                        writer.writerow(row)
                        report_file.flush()

            recv_task = asyncio.create_task(receiver())
            report_task = asyncio.create_task(reporter())
            interval = 0.1 / max(0.01, args.speed)
            loops = 0
            try:
                while True:
                    for frame in frames + silence:
                        await ws.send(frame)
                        await asyncio.sleep(interval)
                    loops += 1
                    if deadline is None or time.monotonic() >= deadline:
                        break
                await asyncio.sleep(8)  # chờ STT/dịch nốt
            finally:
                report_task.cancel()
                recv_task.cancel()
                if report_file:
                    report_file.close()
        print(f"Xong {loops} lượt phát. Sự kiện: {dict(counts)}")
        if args.end:
            await http.post(f"/api/sessions/{session_id}/end")
            print("Đã kết thúc phiên (đang tạo tóm tắt nếu có API key).")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    asyncio.run(main())

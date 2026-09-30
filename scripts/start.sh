#!/usr/bin/env bash
# Khởi động LezioneLive tại http://127.0.0.1:8765 (Ctrl+C để dừng).
#   bash scripts/start.sh              # tự mở trình duyệt
#   bash scripts/start.sh --no-browser
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VPY="$ROOT/backend/.venv/bin/python"
URL="http://127.0.0.1:8765"

if [ ! -x "$VPY" ]; then
  echo "Chưa cài đặt. Chạy trước: bash scripts/setup.sh" >&2
  exit 1
fi

OPEN="--open"
ARGS=()
for a in "$@"; do
  if [ "$a" = "--no-browser" ]; then OPEN=""; else ARGS+=("$a"); fi
done

if curl -fsS -m 2 "$URL/api/health" >/dev/null 2>&1; then
  echo "LezioneLive đang chạy sẵn tại $URL"
  if [ -n "$OPEN" ]; then (command -v open >/dev/null && open "$URL") || xdg-open "$URL" || true; fi
  exit 0
fi

if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
  echo "Chưa build giao diện, đang build..."
  (cd "$ROOT/frontend" && npm install --no-fund --no-audit && npm run build)
fi

echo "LezioneLive: $URL   (Ctrl+C để dừng)"
exec "$VPY" -m app $OPEN "${ARGS[@]+"${ARGS[@]}"}"

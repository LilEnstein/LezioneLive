#!/usr/bin/env bash
# Cài đặt LezioneLive trên macOS / Linux (chạy một lần).
#   bash scripts/setup.sh          # cài đặt
#   bash scripts/setup.sh --gpu    # Linux + GPU NVIDIA: thêm thư viện CUDA 12 (~1 GB)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/backend/.venv"
VPY="$VENV/bin/python"

if [ ! -x "$VPY" ]; then
  PY=""
  for c in python3.11 python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      PY="$c"
      break
    fi
  done
  if [ -z "$PY" ]; then
    echo "[LỖI] Cần Python 3.11 trở lên (https://www.python.org/downloads/)." >&2
    exit 1
  fi
  echo "==> Tạo môi trường ảo backend/.venv"
  "$PY" -m venv "$VENV"
fi

echo "==> Cài thư viện Python"
"$VPY" -m pip install --upgrade pip --quiet
"$VPY" -m pip install -e "$ROOT/backend"

echo "==> Cài khử nhiễu RNNoise cho lớp học trực tiếp (tuỳ chọn)"
"$VPY" -m pip install --no-deps "pyrnnoise>=0.4" --quiet \
  || echo "Không cài được RNNoise: chế độ lớp học vẫn chạy với bộ lọc nhiễu tích hợp."

if [ "${1:-}" = "--gpu" ]; then
  echo "==> Cài thư viện CUDA 12"
  "$VPY" -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[LỖI] Cần Node.js 20+ (https://nodejs.org)." >&2
  exit 1
fi

echo "==> Cài và build giao diện"
(cd "$ROOT/frontend" && npm install --no-fund --no-audit && npm run build)

echo
echo "Cài đặt xong. Khởi động: bash scripts/start.sh"

<#
  Cài đặt LezioneLive trên Windows (chạy một lần, chạy lại cũng không sao).

  Từ thư mục gốc của repo:
      powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  Máy có GPU NVIDIA, muốn nhận dạng giọng nói bằng GPU (tải thêm khoảng 1 GB thư viện CUDA):
      powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Gpu
#>
param([switch]$Gpu)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$venvPy = Join-Path $backend ".venv\Scripts\python.exe"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`n[LỖI] $msg" -ForegroundColor Red; exit 1 }

function Find-Python {
    $candidates = @(
        @{ exe = "py"; pre = @("-3.11") },
        @{ exe = "py"; pre = @("-3.12") },
        @{ exe = "python"; pre = @() },
        @{ exe = "python3"; pre = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $ok = & $c.exe @($c.pre + @("-c", "import sys; print(int(sys.version_info >= (3, 11)))"))
            if ("$ok".Trim() -eq "1") { return $c }
        } catch { }
    }
    return $null
}

Step "Kiểm tra Python 3.11+"
if (-not (Test-Path $venvPy)) {
    $py = Find-Python
    if ($null -eq $py) { Fail "Không tìm thấy Python 3.11 trở lên. Cài từ https://www.python.org/downloads/ rồi chạy lại." }
    Step "Tạo môi trường ảo backend\.venv"
    & $py.exe @($py.pre + @("-m", "venv", (Join-Path $backend ".venv")))
    if ($LASTEXITCODE -ne 0) { Fail "Không tạo được môi trường ảo." }
}

Step "Cài thư viện Python (FastAPI, faster-whisper, anthropic...)"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -e "$backend"
if ($LASTEXITCODE -ne 0) { Fail "pip install thất bại (xem lỗi phía trên)." }

Step "Cài khử nhiễu RNNoise cho lớp học trực tiếp (tuỳ chọn)"
& $venvPy -m pip install --no-deps "pyrnnoise>=0.4" --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "Không cài được RNNoise: chế độ lớp học vẫn chạy với bộ lọc nhiễu tích hợp." -ForegroundColor Yellow
}

$hasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($Gpu) {
    Step "Cài thư viện CUDA 12 cho GPU NVIDIA (khoảng 1 GB)"
    & $venvPy -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"
    if ($LASTEXITCODE -ne 0) { Fail "Cài thư viện CUDA thất bại." }
} elseif ($hasNvidia) {
    Write-Host "Phát hiện GPU NVIDIA. Để dùng GPU cho nhận dạng giọng nói, chạy lại với tham số -Gpu." -ForegroundColor Yellow
}

Step "Kiểm tra Node.js"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Fail "Không tìm thấy Node.js/npm. Cài Node.js 20+ từ https://nodejs.org rồi chạy lại."
}

Step "Cài và build giao diện (frontend)"
Push-Location $frontend
try {
    npm install --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) { Fail "npm install thất bại." }
    npm run build
    if ($LASTEXITCODE -ne 0) { Fail "Build giao diện thất bại." }
} finally {
    Pop-Location
}

Write-Host "`nCài đặt xong." -ForegroundColor Green
Write-Host "Khởi động: nháy đúp scripts\start.bat (hoặc: powershell -ExecutionPolicy Bypass -File scripts\start.ps1)"

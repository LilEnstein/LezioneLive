<#
  Khởi động LezioneLive: backend + giao diện tại http://127.0.0.1:8765

      powershell -ExecutionPolicy Bypass -File scripts\start.ps1              # mở trình duyệt mặc định
      powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -NoBrowser   # không tự mở trình duyệt

  Tham số khác được chuyển cho backend, ví dụ: --port 8766
  Dừng: bấm Ctrl+C trong cửa sổ này (dữ liệu đã lưu, lần sau bấm "Tiếp tục").
#>
param([switch]$NoBrowser)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$root = Split-Path -Parent $PSScriptRoot
$venvPy = Join-Path $root "backend\.venv\Scripts\python.exe"
$url = "http://127.0.0.1:8765"

if (-not (Test-Path $venvPy)) {
    Write-Host "Chưa cài đặt. Chạy trước: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1" -ForegroundColor Yellow
    exit 1
}

# Đang chạy rồi thì chỉ mở giao diện.
try {
    $null = Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 2
    Write-Host "LezioneLive đang chạy sẵn tại $url" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $url }
    exit 0
} catch { }

if (-not (Test-Path (Join-Path $root "frontend\dist\index.html"))) {
    Write-Host "Chưa build giao diện, đang build (lần đầu mất khoảng 1 phút)..." -ForegroundColor Yellow
    Push-Location (Join-Path $root "frontend")
    npm install --no-fund --no-audit
    npm run build
    Pop-Location
}

Write-Host "LezioneLive: $url   (đóng cửa sổ này hoặc Ctrl+C để dừng)" -ForegroundColor Green
$backendArgs = @("-m", "app")
if (-not $NoBrowser) { $backendArgs += "--open" }
& $venvPy @($backendArgs + $args)

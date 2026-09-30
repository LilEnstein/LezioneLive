# LezioneLive

> **Created by Turin** · © 2026 Turin, bảo lưu mọi quyền ([LICENSE](LICENSE)). Mọi commit được ký số
> ([chữ ký tác giả](docs/chu-ky-tac-gia.md)). Cần **mã kích hoạt riêng** mới dùng được: gửi email tới
> brolai1204@gmail.com để xin mã (6 chữ số trộn với chữ TURIN), rồi nhập email + mã ở màn hình đầu tiên của app.

Trợ lý dịch trực tiếp và học tập cho các buổi học bằng tiếng Ý — online trên MS Teams hoặc trong lớp học trực tiếp —
tích hợp Claude.

- Phụ đề tiếng Ý + bản dịch tiếng Việt song song, theo thời gian thực.
- Hỏi Claude về nội dung vừa giảng; soạn câu hỏi tiếng Ý trang trọng gửi giảng viên; chụp slide/bảng cho Claude đọc.
- Sau buổi học: tóm tắt (có bài tập/deadline), xuất Markdown/PDF, flashcard ôn từ vựng.
- Dịch bằng **Claude API**, **gói Claude Pro/Max** (qua Claude Code) hoặc **model NLLB chạy trên máy** (miễn phí,
  không cần mạng). Hết hạn mức Claude giữa buổi thì phụ đề tự dịch nháp bằng model trên máy, sau giờ học dịch lại
  bằng Claude chỉ với một nút.
- Xem hình tab Teams ngay trong app, cắt đúng vùng slide, gửi khung hình cho trợ lý.
- Lớp học trực tiếp: thu bằng micro laptop hoặc điện thoại (ghép nối QR, HTTPS nội bộ), lọc nhiễu RNNoise, kiểm tra
  âm thanh theo phòng, phân biệt giảng viên / sinh viên, chạy tiếp khi mất Wi-Fi, chế độ tiết kiệm pin.
- **Dữ liệu nằm trên máy bạn.** Âm thanh được nhận dạng tại chỗ (Whisper); chỉ văn bản (và ảnh bạn chủ động gửi)
  được gửi tới Claude.

Tài liệu chi tiết:

- [Hướng dẫn dùng với MS Teams](docs/huong-dan-su-dung-ms-teams.md)
- [Hướng dẫn lớp học trực tiếp](docs/huong-dan-lop-hoc-truc-tiep.md)
- Đặc tả: [app_spec.md](app_spec.md) · [feature_spec.md](feature_spec.md)

---

## Mục lục

1. [Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
2. [Chuẩn bị tài khoản Claude](#2-chuẩn-bị-tài-khoản-claude)
3. [Cài đặt](#3-cài-đặt)
4. [Khởi động và thiết lập lần đầu](#4-khởi-động-và-thiết-lập-lần-đầu)
5. [Sử dụng](#5-sử-dụng)
6. [Cấu hình nâng cao (.env)](#6-cấu-hình-nâng-cao-env)
7. [Dữ liệu và bảo mật](#7-dữ-liệu-và-bảo-mật)
8. [Xử lý sự cố](#8-xử-lý-sự-cố)
9. [Phát triển](#9-phát-triển)
10. [Trước khi đưa lên Git](#10-trước-khi-đưa-lên-git)

---

## 1. Yêu cầu hệ thống

| Thành phần | Yêu cầu |
|---|---|
| Hệ điều hành | Windows 10/11 (chính), macOS, Linux |
| Python | 3.11 trở lên — <https://www.python.org/downloads/> (Windows: tick **Add python.exe to PATH**) |
| Node.js | 20 trở lên — <https://nodejs.org> |
| Trình duyệt | Chrome hoặc Edge (cần để chia sẻ âm thanh tab Teams và xuất PDF) |
| RAM / ổ đĩa | Tối thiểu 8 GB RAM; ~3 GB trống cho thư viện + model Whisper (thêm ~0,6–1,4 GB nếu dùng NLLB) |
| GPU (tuỳ chọn) | NVIDIA có CUDA 12 để nhận dạng giọng nói nhanh hơn; không có thì chạy CPU |
| Claude | **Một trong hai**: Claude API key có credit, hoặc gói Claude Pro/Max + Claude Code (xem mục 2) |

## 2. Chuẩn bị tài khoản Claude

App hỗ trợ hai cách dùng Claude. Chọn trong **Cài đặt → Dịch bằng** và **Trợ lý dùng**; có thể đổi nhanh nguồn dịch
ngay trên màn hình học.

### Cách A — Claude API key

1. Tạo key tại <https://platform.claude.com> (mục API Keys) và nạp credit.
2. Dán key vào trình hướng dẫn lần đầu (hoặc **Cài đặt → API key**).

Key được lưu trong **kho mật khẩu của hệ điều hành** (Windows Credential Manager / macOS Keychain / Linux Secret
Service, tên dịch vụ `lezionelive`) — không lưu trong file, không nằm trong repo.

### Cách B — Gói Claude Pro/Max qua Claude Code (không tốn credit API)

1. Cài Claude Code: <https://claude.com/claude-code> (hoặc `npm install -g @anthropic-ai/claude-code`).
2. Đăng nhập một lần trong terminal:

   ```bash
   claude auth login
   ```

3. Trong app chọn **Dịch bằng** / **Trợ lý dùng** = "Gói Claude Pro/Max".

App gọi lệnh `claude -p` trên máy bạn, trong một thư mục trống riêng (không đọc CLAUDE.md hay cài đặt của dự án
nào, không bật tool/MCP) và **gỡ `ANTHROPIC_API_KEY` khỏi môi trường** của tiến trình con để luôn dùng hạn mức gói.
Thông tin đăng nhập Claude Code nằm trong thư mục người dùng (`~/.claude`), app không đọc hay sao chép nó.

> Chụp bảng/slide (gửi ảnh) qua Claude Code có giới hạn; nếu gặp lỗi hãy dùng API key cho tính năng này.
> Nếu Claude Code không nằm trong PATH, đặt biến môi trường `LEZIONE_CLAUDE_PATH` trỏ tới file `claude.exe`.

### Cách C — Chỉ dùng model trên máy (không cần Claude)

Chọn **Dịch bằng = Model trên máy (NLLB)** và tải model trong app. Dịch miễn phí, không cần mạng, chất lượng thấp
hơn Claude. Trợ lý hỏi đáp / tóm tắt / flashcard vẫn cần Claude (cách A hoặc B).

## 3. Cài đặt

Tải mã nguồn:

```bash
git clone <địa-chỉ-repo> LezioneLive
```

### Windows

Mở PowerShell tại thư mục gốc repo:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Máy có GPU NVIDIA (tải thêm ~1 GB thư viện CUDA):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Gpu
```

Hoặc nháy đúp `scripts\setup.bat`.

### macOS / Linux

```bash
bash scripts/setup.sh
```

Linux + GPU NVIDIA: `bash scripts/setup.sh --gpu`.

### Script cài đặt làm gì

1. Tìm Python 3.11+, tạo môi trường ảo `backend/.venv`.
2. `pip install -e backend` (FastAPI, faster-whisper, anthropic, keyring...).
3. Cài RNNoise (`pyrnnoise`, tuỳ chọn — lỗi thì app dùng bộ lọc nhiễu tích hợp).
4. `-Gpu` / `--gpu`: cài thư viện CUDA 12.
5. `npm install` + `npm run build` giao diện vào `frontend/dist`.

Chạy lại script bất cứ lúc nào (ví dụ sau khi `git pull`) để cập nhật thư viện và build lại giao diện.

### Cài thủ công (nếu script lỗi)

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # macOS/Linux: .venv/bin/python
cd ../frontend
npm install
npm run build
```

Tuỳ chọn thêm: `pip install -e ".[pdf]"` (xuất PDF bằng WeasyPrint thay Chrome headless),
`pip install --no-deps "pyrnnoise>=0.4"` (khử nhiễu RNNoise).

## 4. Khởi động và thiết lập lần đầu

### Khởi động

Windows — nháy đúp `scripts\start.bat`, hoặc:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

macOS / Linux:

```bash
bash scripts/start.sh
```

Trình duyệt mở <http://127.0.0.1:8765>. Giữ cửa sổ terminal mở trong suốt buổi học; **Ctrl+C** để dừng (dữ liệu đã
lưu, lần sau bấm "Tiếp tục"). Không tự mở trình duyệt: thêm `-NoBrowser` (Windows) / `--no-browser`
(macOS/Linux). Tham số khác được chuyển cho backend, ví dụ `--port 8770`.

### Trình hướng dẫn lần đầu

1. Chọn nguồn Claude: nhập API key **hoặc** chọn gói Pro/Max (mục 2).
2. Tải model nhận dạng giọng nói Whisper (`large-v3-turbo` mặc định; máy yếu chọn `small`/`base`).
3. Tạo môn học đầu tiên (tên môn, giảng viên, bảng thuật ngữ).
4. Kiểm tra âm thanh.
5. Đặt ngưỡng cảnh báo chi phí (khi dùng API key).

Chạy lại trình hướng dẫn bất cứ lúc nào: **Cài đặt → Chạy lại hướng dẫn cài đặt**.

Góc trên bên phải hiện trạng thái Claude và **STT sẵn sàng** màu xanh là đã sẵn sàng.

## 5. Sử dụng

### Buổi học online trên MS Teams

1. Mở Teams trên **Chrome/Edge** (cùng trình duyệt với LezioneLive) và vào lớp.
2. LezioneLive: **Bắt đầu buổi học** → chọn môn → nguồn **Tab trình duyệt (Teams web)** → **Bắt đầu**.
3. **Chọn nguồn âm thanh** → chọn tab Teams → bật **chia sẻ âm thanh của tab** → **Chia sẻ**.
4. Theo dõi phụ đề Ý | Việt; hỏi trợ lý ở khung chat; xem màn hình Teams ngay trong app nếu muốn.
5. **Kết thúc buổi học** → đọc tóm tắt, xuất ghi chú, ôn flashcard.

Teams bản desktop: chọn nguồn **Âm thanh hệ thống (Teams desktop)**. Chi tiết:
[docs/huong-dan-su-dung-ms-teams.md](docs/huong-dan-su-dung-ms-teams.md).

### Lớp học trực tiếp

1. **Xin phép giảng viên** (app bắt buộc xác nhận, mỗi môn một lần).
2. Chọn nguồn **Micro laptop** hoặc **Điện thoại làm micro** (quét mã QR; điện thoại và laptop cùng mạng Wi-Fi, cổng 8766).
3. Kiểm tra âm thanh 30 giây theo phòng học → bắt đầu.

Chi tiết: [docs/huong-dan-lop-hoc-truc-tiep.md](docs/huong-dan-lop-hoc-truc-tiep.md).

## 6. Cấu hình nâng cao (.env)

Mọi biến đều **tuỳ chọn**. Sao chép `.env.example` thành `.env` ở thư mục gốc repo rồi bỏ dấu `#` ở dòng cần dùng:

```powershell
Copy-Item .env.example .env
```

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `LEZIONELIVE_HOST` | `127.0.0.1` | Địa chỉ backend. Giữ loopback — không mở ra mạng |
| `LEZIONELIVE_PORT` | `8765` | Cổng giao diện + API |
| `LEZIONELIVE_DATA_DIR` | xem mục 7 | Thư mục dữ liệu (SQLite, slide, log, model cache) |
| `LEZIONELIVE_LOG_LEVEL` | `INFO` | Mức log |
| `LEZIONELIVE_CHROME_PATH` | tự dò | Chrome/Edge dùng để xuất PDF |
| `LEZIONELIVE_DISABLE_STT_AUTOLOAD` | `false` | Không tự nạp Whisper khi khởi động |
| `LEZIONELIVE_COMPANION_HOST` / `_PORT` | `0.0.0.0` / `8766` | Server HTTPS cho điện thoại đồng hành |
| `LEZIONE_CLAUDE_PATH` | tự dò | Đường dẫn `claude.exe` nếu không có trong PATH |
| `HF_HOME` | mặc định HF | Nơi lưu model tải từ Hugging Face |
| `ANTHROPIC_API_KEY` | — | **Chỉ dùng khi phát triển**: ghi đè key trong kho mật khẩu |

`.env` đã nằm trong `.gitignore`. Ưu tiên lưu API key qua giao diện (kho mật khẩu hệ điều hành) thay vì `.env`.

## 7. Dữ liệu và bảo mật

**Nơi lưu dữ liệu** (ngoài repo):

| Hệ điều hành | Thư mục |
|---|---|
| Windows | `%LOCALAPPDATA%\LezioneLive` |
| macOS | `~/Library/Application Support/LezioneLive` |
| Linux | `~/.local/share/LezioneLive` |

Gồm: `lezionelive.db` (buổi học, phụ đề, chat, cài đặt), slide, log, chứng chỉ TLS cục bộ cho điện thoại, thư mục
làm việc tạm của Claude Code. Sao lưu = sao chép thư mục này. Gỡ cài đặt = xoá thư mục repo + thư mục dữ liệu + mục
`lezionelive` trong kho mật khẩu hệ điều hành.

**Nguyên tắc bảo mật:**

- Backend chỉ nghe trên `127.0.0.1`; server điện thoại chỉ mở khi bạn bấm ghép nối và mọi kết nối cần token
  (QR dùng một lần / token thiết bị).
- API key nằm trong kho mật khẩu hệ điều hành; đăng nhập Claude Pro/Max nằm trong `~/.claude` do Claude Code quản
  lý. Không cái nào nằm trong repo.
- Âm thanh không rời khỏi máy. Chỉ văn bản phụ đề/câu hỏi (và ảnh bạn chủ động chụp) được gửi tới Claude.

## 8. Xử lý sự cố

| Triệu chứng | Cách xử lý |
|---|---|
| `Không tìm thấy Python 3.11` | Cài Python 3.11+, tick "Add to PATH", mở lại PowerShell, chạy lại setup |
| `running scripts is disabled` | Dùng đúng lệnh `powershell -ExecutionPolicy Bypass -File ...` hoặc các file `.bat` |
| `Claude Code chưa đăng nhập` | Chạy `claude auth login` trong terminal rồi bấm thử lại trong app |
| Hết hạn mức gói Claude | Phụ đề tự chuyển sang dịch nháp NLLB; sau giờ học bấm dịch lại bằng Claude |
| `invalid api key` / hết credit | Kiểm tra key và credit tại platform.claude.com, hoặc chuyển sang gói Pro/Max |
| Không có phụ đề | Khi chia sẻ tab phải bật **chia sẻ âm thanh**; kiểm tra STT đã sẵn sàng |
| Nhận dạng chậm | Chọn Whisper nhỏ hơn (`small`), hoặc cài GPU (`setup.ps1 -Gpu`) |
| Cổng 8765 đã bị dùng | Chạy `scripts\start.ps1 --port 8770` |
| Điện thoại không kết nối | Cùng Wi-Fi với laptop; cho phép Python qua Windows Firewall (mạng Private); Wi-Fi trường chặn thiết bị với nhau thì dùng hotspot của laptop |
| Xuất PDF lỗi | Cài Chrome/Edge hoặc đặt `LEZIONELIVE_CHROME_PATH` |

Log nằm trong thư mục dữ liệu, mục `logs/`.

## 9. Phát triển

```powershell
# Backend: test + lint (cài thêm công cụ dev: .venv\Scripts\python -m pip install -e ".[dev]")
cd backend
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check app tests
.venv\Scripts\python -m app --reload          # tự nạp lại khi sửa code

# Frontend: dev server (proxy /api và /ws tới backend 127.0.0.1:8765)
cd frontend
npm run dev        # http://localhost:5173
npm run typecheck
npm run lint
npm run test:e2e   # Playwright
```

Dữ liệu mẫu để thử giao diện (dùng thư mục dữ liệu riêng, không đụng dữ liệu thật):

```powershell
backend\.venv\Scripts\python scripts\demo_seed.py --data-dir D:\lezione-demo
backend\.venv\Scripts\python -m app --data-dir D:\lezione-demo
```

Cấu trúc chính:

| Thư mục | Nội dung |
|---|---|
| `backend/app/` | FastAPI: WebSocket nhận audio, VAD + STT, hàng đợi dịch, trợ lý Claude, lưu trữ SQLite |
| `backend/app/llm/` | Client Claude API, client Claude Code (`claude -p`), router chọn nguồn, prompt |
| `backend/app/audio/` | Nguồn âm thanh (tab, loopback, micro, điện thoại), tiền xử lý, sound check, phân biệt người nói |
| `backend/app/offline/` | Model dịch NLLB trên máy (tải, nạp, dịch theo lô, giữ thuật ngữ) |
| `backend/app/companion/` | Điện thoại đồng hành: chứng chỉ TLS cục bộ, ghép nối QR, server HTTPS/WSS cổng 8766 |
| `backend/tests/` | Test đơn vị, test SDK với HTTP giả lập, test WebSocket end-to-end |
| `frontend/src/` | React 18 + Vite + TypeScript + Tailwind + Zustand, icon Lucide |
| `frontend/companion.html`, `src/pages/companion/` | Trang điện thoại (PWA): Micro / Phụ đề / Cả hai |
| `scripts/` | Cài đặt, khởi động, `soak.py` (kiểm thử dài), `eval_room.py` (WER theo SNR), `demo_seed.py` |

Chuỗi giao diện: tiếng Việt là chính; khi thêm key i18n mới, thêm bản tương ứng vào `frontend/src/i18n/en.ts`.

Dùng Claude Code để phát triển: cấu hình preview (`.claude/launch.json`) và mọi thứ trong `.claude/` là cài đặt riêng
của từng máy và **không được commit** (xem mục 10).

## 10. Trước khi đưa lên Git

`.gitignore` đã chặn: thư mục `.claude/` (cấu hình Claude Code riêng máy, có đường dẫn cá nhân), `.env*` (trừ
`.env.example`), khoá/chứng chỉ (`*.pem`, `*.key`...), file đăng nhập (`.credentials.json`, `.claude.json`), database
và log, bản ghi âm, model tải về, `.venv/`, `node_modules/`, `frontend/dist/`.

Checklist mỗi lần commit lần đầu / trước khi push:

```bash
git init
git add .
git status
```

1. Trong danh sách `git status` **không được có**: `.claude/`, `.env`, `*.db`, `*.pem`, `*.log`, `node_modules/`,
   `.venv/`, `frontend/dist/`.
2. Kiểm tra một file cụ thể có bị chặn chưa: `git check-ignore -v .claude/launch.json`.
3. Quét nhanh bí mật trong những gì sắp commit (không được ra kết quả nào ngoài các key giả trong `backend/tests/`):

   ```bash
   git grep -nIE "sk-ant-[A-Za-z0-9_-]{20,}|PRIVATE KEY|accessToken|refreshToken" --cached
   ```

4. Nếu lỡ commit bí mật: **thu hồi key ngay** (platform.claude.com → API Keys; với Claude Code chạy
   `claude auth logout` rồi đăng nhập lại), sau đó mới xoá khỏi lịch sử git. Xoá file trong commit mới là không đủ.

Các key dạng `sk-ant-api03-...` trong `backend/tests/` là **key giả** dùng cho test, không phải bí mật.

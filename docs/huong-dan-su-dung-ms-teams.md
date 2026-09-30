# Hướng dẫn dùng LezioneLive cùng MS Teams

LezioneLive nghe bài giảng tiếng Ý trên MS Teams ngay trên máy của bạn, hiện **phụ đề tiếng Ý và bản dịch tiếng Việt song song**, cho bạn **hỏi Claude** về nội dung vừa giảng, **soạn câu hỏi tiếng Ý** gửi giảng viên, và sau buổi học tự tạo **tóm tắt, ghi chú, flashcard**.

---

## Tóm tắt nhanh (khi đã cài đặt xong)

1. Nháy đúp **`scripts\start.bat`** → trình duyệt mở LezioneLive tại <http://127.0.0.1:8765>.
2. Mở **Teams trên Chrome hoặc Edge** (cùng trình duyệt với LezioneLive) và vào lớp.
3. Trong LezioneLive: **▶ Bắt đầu buổi học** → chọn môn → nguồn **Tab trình duyệt (Teams web)** → **Bắt đầu**.
4. Bấm **🎧 Chọn nguồn âm thanh** → chọn **tab Teams** → **bật chia sẻ âm thanh của tab** → **Chia sẻ**.
5. Bấm **🖥 Màn hình Teams / tab** để xem hình tab Teams ngay trong LezioneLive (hoặc đặt hai cửa sổ cạnh nhau, hoặc dùng cửa sổ phụ đề nổi **⧉**).
6. Học xong: **⏹ Kết thúc buổi học** → đọc tóm tắt, xuất ghi chú, ôn flashcard.

**Checklist trước mỗi buổi:** cửa sổ backend (màu đen) đang mở · góc phải trên hiện **API key: …xxxx** và **STT sẵn sàng** màu xanh · Teams mở trên Chrome/Edge · đã thêm thuật ngữ/slide của buổi (nếu có).

---

## 1. LezioneLive kết nối với Teams như thế nào

LezioneLive **không phải bot** tham gia cuộc họp và không cần quyền gì trên Teams. Nó chỉ nghe âm thanh mà Teams phát ra trên máy bạn:

```text
 Chrome / Edge
 ┌──────────────────────────────┐   chia sẻ âm thanh của tab   ┌──────────────────────────────┐
 │ Tab 1: MS Teams (lớp học)    │ ───────────────────────────▶ │ Tab 2: LezioneLive           │
 │ bạn vẫn nghe giảng bình thường│                              │ phụ đề Ý | Việt, hỏi Claude  │
 └──────────────────────────────┘                              └──────────────┬───────────────┘
                                                                              │ âm thanh (chỉ trong máy)
                                                               ┌──────────────▼───────────────┐
                                                               │ Backend 127.0.0.1:8765       │
                                                               │ cắt câu → Whisper nhận dạng  │
                                                               │ → lưu SQLite trên máy        │
                                                               └──────────────┬───────────────┘
                                                                              │ chỉ gửi VĂN BẢN
                                                               ┌──────────────▼───────────────┐
                                                               │ Claude API: dịch, trả lời,   │
                                                               │ tóm tắt, flashcard           │
                                                               └──────────────────────────────┘
```

- Âm thanh chỉ đi từ tab Teams sang backend **trên chính máy bạn** để nhận dạng giọng nói bằng Whisper; **không** gửi lên Internet (trừ khi bạn chọn STT cloud Deepgram/Azure).
- Chỉ **văn bản** (lời giảng đã nhận dạng, câu hỏi của bạn) được gửi tới Claude.
- **Giọng nói của bạn** (micro) không bị ghi: app chỉ lấy âm thanh phát ra từ Teams.
- Transcript, ghim, chat, tóm tắt được lưu trên máy bạn.

Có hai cách lấy âm thanh:

| | **Tab trình duyệt** (khuyến nghị) | **Âm thanh hệ thống** |
|---|---|---|
| Dùng khi | Vào lớp bằng **Teams trên Chrome/Edge** | Vào lớp bằng **ứng dụng Teams desktop** |
| Cách hoạt động | Trình duyệt chia sẻ âm thanh của đúng tab Teams | Bắt mọi âm thanh phát ra loa/tai nghe của máy |
| Ưu điểm | Chỉ lấy tiếng lớp học, không cần cài gì thêm | Dùng được với Teams desktop |
| Lưu ý | Teams phải mở trên web, cùng trình duyệt với LezioneLive | Phải tắt nhạc, YouTube, âm báo khác; macOS cần cài BlackHole |

---

## 2. Cài đặt lần đầu (chỉ làm một lần)

### 2.1 Cần chuẩn bị

- Windows 10/11 (hoặc macOS 13+), **Google Chrome** hoặc **Microsoft Edge** bản mới.
- **Python 3.11+** (<https://www.python.org/downloads/>) và **Node.js 20+** (<https://nodejs.org>).
- **Claude API key**: đăng nhập <https://console.anthropic.com> → *API Keys* → *Create Key*. Tài khoản phải còn credit. Key có dạng `sk-ant-…`.
- Khoảng 3 GB dung lượng trống (model Whisper large-v3-turbo khoảng 1,6 GB).

### 2.2 Cài đặt

Nháy đúp **`scripts\setup.bat`**, hoặc chạy trong PowerShell tại thư mục gốc của dự án:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Máy có **GPU NVIDIA** (ví dụ GTX 1650): cài thêm thư viện CUDA (khoảng 1 GB) để nhận dạng giọng nói nhanh hơn nhiều:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Gpu
```

macOS/Linux: `bash scripts/setup.sh` (Linux có GPU: `bash scripts/setup.sh --gpu`).

### 2.3 Khởi động và tắt

- **Khởi động:** nháy đúp **`scripts\start.bat`**. Một cửa sổ màu đen hiện ra — đó là backend, **để nguyên, đừng đóng trong lúc học**. Trình duyệt tự mở <http://127.0.0.1:8765>.
  Nếu trình duyệt mặc định không phải Chrome/Edge, hãy tự mở địa chỉ này bằng **Chrome/Edge — cùng trình duyệt bạn dùng cho Teams**.
- **Tắt:** đóng cửa sổ đen (hoặc bấm Ctrl+C trong đó). Dữ liệu đã được lưu; buổi học đang dở sẽ ở trạng thái **Tạm dừng**, lần sau bấm **▶ Tiếp tục**.

### 2.4 Hướng dẫn cài đặt trong app

Lần mở đầu tiên, app tự mở trang **Cài đặt lần đầu** gồm 6 bước:

1. **Chào mừng**.
2. **API key**: dán key → **Lưu & kiểm tra**. Thành công sẽ hiện **"✓ Đã kết nối (…abcd)"**. Key được lưu trong Windows Credential Manager; giao diện chỉ hiện 4 ký tự cuối.
3. **Nhận dạng giọng nói**:
   - Có GPU NVIDIA và đã cài thư viện CUDA (`setup.ps1 -Gpu`): giữ **Whisper cục bộ** + model **large-v3-turbo**.
   - Không có GPU, hoặc app báo *"GPU chưa dùng được vì thiếu thư viện CUDA 12"*: chọn model **small** (chạy bằng CPU).
   - Bấm **Tải & nạp model** và đợi tới khi hiện **STT sẵn sàng**. Việc tải model chỉ làm một lần.
   - **Deepgram / Azure** (cloud) nhanh, không cần GPU, nhưng âm thanh bài giảng sẽ được gửi lên dịch vụ đó — chỉ dùng khi giảng viên cho phép.
4. **Môn học**: tên môn (tiếng Việt), **tên tiếng Ý** (giúp nhận dạng và dịch đúng ngữ cảnh), **tên giảng viên** (để Claude xưng "Professore" hay "Professoressa" cho đúng).
5. **Test âm thanh 10 giây**: mở một video bài giảng tiếng Ý (ví dụ trên YouTube) ở tab khác → **Chọn tab & test 10 giây** → chọn tab video, bật chia sẻ âm thanh → xem đoạn tiếng Ý nhận dạng được. Đây cũng là dịp tập thao tác chia sẻ âm thanh trước khi vào lớp thật.
6. **Hoàn tất**: đặt ngưỡng cảnh báo chi phí mỗi buổi (gợi ý **3 USD** cho buổi 3 giờ, xem [mục 9](#9-câu-hỏi-thường-gặp)).

Có thể làm lại các bước này bất cứ lúc nào: **Cài đặt → Chạy lại hướng dẫn cài đặt**.

---

## 3. Mỗi buổi học với Teams trên trình duyệt (khuyến nghị)

### 3.1 Trước giờ học khoảng 10 phút

- Khởi động LezioneLive (`start.bat`). Góc phải trên phải có **API key: …xxxx** và **STT sẵn sàng** màu xanh.
- (Nên làm) Thêm thuật ngữ của môn ở trang **Thuật ngữ**, ví dụ `regressione logistica → hồi quy logistic`, hoặc **Import CSV**. Thuật ngữ được dùng cho cả nhận dạng giọng nói lẫn bản dịch, giúp dịch thống nhất.
- (Nếu có) Tải file slide PDF của buổi học về máy (từ tab *Tệp/Files* của nhóm Teams hoặc trang e-learning của trường) để upload ở [bước 3.4](#34-chia-sẻ-âm-thanh-của-tab-teams-cho-lezionelive).

### 3.2 Vào lớp trên Teams web

1. Trong **cùng trình duyệt** với LezioneLive (Chrome hoặc Edge), mở <https://teams.microsoft.com> và đăng nhập bằng tài khoản trường.
2. Vào lớp từ **Lịch (Calendar)** hoặc từ kênh của môn học → **Tham gia (Join)**.
   Nếu bấm link cuộc họp mà trình duyệt đòi mở ứng dụng Teams, chọn **Tiếp tục trên trình duyệt này** (*Continue on this browser*).
3. Chọn micro/camera như bình thường và kiểm tra bạn nghe được giảng viên.

### 3.3 Bắt đầu buổi học trong LezioneLive

1. Chuyển sang tab LezioneLive → **▶ Bắt đầu buổi học**.
2. Chọn **Môn học**, đặt **Tên buổi học** (tuỳ chọn, ví dụ "Lezione 3 – Regressione logistica").
3. **Nguồn âm thanh**: chọn **Tab trình duyệt (Teams web)** → **▶ Bắt đầu**.

### 3.4 Chia sẻ âm thanh của tab Teams cho LezioneLive

1. Trên màn hình học, bấm **🎧 Chọn nguồn âm thanh**.
2. Hộp thoại chia sẻ của Chrome/Edge hiện ra (tên các mục có thể hơi khác tuỳ phiên bản):
   - Chọn mục **Thẻ Chrome / Tab** (*Chrome Tab*), **không** chọn "Cửa sổ" hay "Toàn bộ màn hình".
   - Chọn tab **Microsoft Teams** đang họp.
   - **Bật chia sẻ âm thanh của tab** (công tắc hoặc ô tick ở góc dưới; tiếng Anh: *Also share tab audio*). Thiếu bước này app sẽ báo *"Chưa nhận được âm thanh…"*.
   - Bấm **Chia sẻ** (*Share*).
3. Trình duyệt hiện dải thông báo *đang chia sẻ tab này* kèm nút **Dừng chia sẻ** — **đừng bấm Dừng chia sẻ** trong lúc học. Bạn vẫn nghe giảng viên bình thường. LezioneLive giữ bạn ở lại màn hình học (bản Chrome/Edge cũ có thể tự chuyển sang tab Teams — khi đó chỉ cần quay lại tab LezioneLive).
4. Trên LezioneLive: nút nguồn đổi thành tên tab Teams, **thanh mức âm 🎚** nhảy khi giảng viên nói, và phụ đề bắt đầu hiện sau vài giây. Nếu khung **🖥 Màn hình** đang bật, hình của tab Teams cũng hiện luôn trong khung đó.
5. (Nếu có slide) Bấm **logo trợ lý** (robot tròn ở góc phải dưới) → tab **📄 Slide** → **Tải slide PDF**. Claude dùng slide để dịch và trả lời chính xác hơn, và đề xuất thuật ngữ — bấm **＋** để thêm vào bảng thuật ngữ.

### 3.5 Bố trí màn hình

Nếu tab Teams và tab LezioneLive nằm chung một cửa sổ thì không nhìn được cả hai cùng lúc. Chọn một trong các cách sau:

- **Xem Teams ngay trong LezioneLive (khuyến nghị):** bấm **🖥 Màn hình Teams / tab** trên thanh trên cùng.
  - Bật khung này **trước** khi bấm **🎧 Chọn nguồn âm thanh** thì hình của tab Teams tự hiện trong khung, không phải chọn lần nữa.
  - Hoặc bấm **Chọn cửa sổ / tab** trong khung để xem bất kỳ cửa sổ nào: ứng dụng Teams desktop, tab slide PDF, cả màn hình… (chỉ lấy hình, không ảnh hưởng nguồn âm thanh).
  - **✂️ Cắt vùng:** kéo chuột chọn đúng khung slide (bỏ thanh công cụ, danh sách người học của Teams) — app nhớ vùng cắt cho buổi sau; **↺ Bỏ cắt** để xem lại toàn bộ.
  - **📸 Hỏi trợ lý về khung hình này:** chụp khung hình (đúng vùng đã cắt) gửi kèm câu hỏi cho trợ lý — tiện hỏi về slide, công thức trên bảng.
  - **⇆ / ⇅:** đặt khung cạnh phụ đề hoặc phía trên phụ đề; kéo thanh chia ở giữa để đổi kích thước; **✕** để ẩn khung (hình vẫn được giữ, bật lại là thấy ngay), **⏹** để dừng chia sẻ hình.
- **Hai cửa sổ cạnh nhau:** kéo tab LezioneLive ra ngoài thành cửa sổ riêng, bấm **Win + ←** cho Teams và **Win + →** cho LezioneLive. Trợ lý nằm gọn trong nút logo ở góc phải dưới nên phụ đề có trọn bề ngang; dùng **A- / A+** để chỉnh cỡ chữ.
- **Cửa sổ phụ đề nổi (⧉):** một cửa sổ nhỏ luôn nổi trên cùng, hiện 4 câu gần nhất (Ý + Việt), có thể đặt đè lên video Teams. Cần Chrome/Edge 116 trở lên.
- **Hai màn hình:** Teams một màn hình, LezioneLive một màn hình.

---

## 4. Dùng ứng dụng Teams desktop (chế độ Âm thanh hệ thống)

Dùng khi không thể vào lớp bằng Teams web.

### Windows

1. **▶ Bắt đầu buổi học** → Nguồn âm thanh: **Âm thanh hệ thống (Teams desktop)** → **Bắt đầu**. App bắt đầu nghe ngay, không cần chọn nguồn (thanh trên cùng hiện **🔊 Đang bắt âm thanh hệ thống**).
2. App bắt âm thanh của **thiết bị phát mặc định** của Windows. Trong Teams desktop (**Cài đặt → Thiết bị → Loa**), hãy chọn đúng thiết bị đó (loa hay tai nghe đều được). Nếu Teams phát ra thiết bị khác, vào LezioneLive **Cài đặt → Nhận dạng giọng nói (STT) → Thiết bị bắt âm thanh hệ thống** chọn thiết bị có biểu tượng 🔊 rồi bấm **💾 Lưu cài đặt**.
3. Mọi âm thanh của máy đều được thu: hãy tắt nhạc, YouTube, âm báo Zalo/Messenger… trong giờ học.

### macOS (cần thiết lập một lần)

1. Cài **BlackHole 2ch**: <https://existential.audio/blackhole/>.
2. Mở **Audio MIDI Setup** → nút **+** → **Create Multi-Output Device** → tick **BlackHole 2ch** và loa/tai nghe của bạn.
3. Đặt Multi-Output Device làm đầu ra âm thanh của hệ thống (hoặc chọn nó làm Loa trong Teams).
4. Trong LezioneLive, **Cài đặt → Thiết bị bắt âm thanh hệ thống** → chọn **BlackHole 2ch** (để trống thì app tự tìm BlackHole).

> Mẹo: cửa sổ phụ đề nổi **⧉** vẫn nổi trên ứng dụng Teams desktop.

---

## 5. Trong buổi học

### 5.1 Đọc phụ đề

| Bạn thấy | Nghĩa là |
|---|---|
| Dòng cuối có `▌ đang nói` | Giảng viên đang nói, câu chưa kết thúc |
| Có chữ Ý, cột Việt là `⋯` | Đã nhận dạng, đang chờ dịch |
| Chữ Việt hiện dần kèm con trỏ `▌` | Claude đang dịch |
| Nhãn nhỏ **máy** trước bản dịch | Câu dịch bằng model trên máy (NLLB) — xem mục 5.6 |
| Chữ Việt nghiêng có nhãn **nháp** | Bản dịch nháp tạm thời (mất mạng / Claude hết hạn mức), sẽ được thay khi Claude dịch được |
| Chữ Ý gạch chân chấm màu cam | Nhận dạng không chắc chắn — nên nghe kỹ hoặc sửa lại |
| `⚠ Dịch lỗi` + **Thử lại** | Bản dịch lỗi, bấm **Thử lại** |
| Nền vàng + 📌 | Đoạn đã ghim |
| `✎` sau câu Ý | Câu đã được sửa tay |

- Mốc `[mm:ss]` tính từ đầu buổi học (không tính lúc tạm dừng); số phút có thể lớn hơn 59, ví dụ `[83:05]`.
- Khi bạn cuộn lên đọc lại, app tự tắt **Tự cuộn** và hiện nút **↓ Về mới nhất**.

### 5.2 Phím tắt

Phím tắt chỉ có tác dụng khi **cửa sổ LezioneLive đang được chọn** (đang ở Teams thì bấm vào LezioneLive trước).

| Phím | Tác dụng |
|---|---|
| **Ctrl+B** | Ghim đoạn đang chọn (hoặc đoạn mới nhất), loại "chưa hiểu" |
| **Ctrl+K** | Mở khung trợ lý và chuyển tới ô hỏi Claude |
| **Ctrl+H** | Bật/tắt chế độ luyện nghe: làm mờ bản dịch, di chuột lên dòng để xem |
| **Ctrl+E** | Nhờ Claude giải thích đoạn đang chọn |
| **Space** | Tạm dừng / tiếp tục (khi không gõ chữ) |

### 5.3 Bấm vào một câu

Hiện menu: **📌 Ghim**, **✏️ Sửa text Ý** (sửa lỗi nhận dạng, app tự dịch lại), **💬 Hỏi Claude về đoạn này** (điền sẵn câu hỏi kèm ngữ cảnh ±30 giây), **📋 Copy**.

### 5.4 Hỏi Claude về bài giảng (tab **Hỏi bài**)

- Trợ lý không chiếm chỗ trên màn hình: bấm **logo trợ lý** ở góc phải dưới (hoặc **Ctrl+K**) để mở, bấm lại để thu gọn. Câu trả lời vẫn chạy tiếp khi thu gọn — logo có vòng xoay khi đang trả lời và **chấm đỏ** khi có câu trả lời mới. Nút **⤢** mở rộng khung chat.
- Nút nhanh: **Giải thích đoạn vừa rồi**, **Tóm tắt 5 phút gần nhất**, **Giải thích khái niệm…**.
- Hoặc gõ câu hỏi bằng tiếng Việt, nhấn Enter để gửi.
- Câu trả lời có mốc `[mm:ss]` — bấm vào để nhảy tới đoạn đó trong phụ đề. Công thức được hiển thị dạng toán học.
- Claude tách riêng điều giảng viên nói với kiến thức bổ sung (mục **Kiến thức bổ sung**), và nói rõ khi bài giảng không đề cập tới điều bạn hỏi.

### 5.5 Hỏi giảng viên qua chat Teams (tab **Hỏi GV**)

1. Gõ câu hỏi bằng tiếng Việt → **Soạn câu hỏi tiếng Ý**.
2. Đọc phần **Dịch ngược để kiểm tra** xem ý có đúng không, và phần **Lưu ý** (nếu có).
3. Bấm **📋 Copy bản tiếng Ý** → sang Teams mở **Trò chuyện (Chat)** của cuộc họp → dán → gửi. Hoặc bấm **Giơ tay (Raise hand)** và đọc câu hỏi.

### 5.6 Chọn nguồn dịch: Claude hay model trên máy (nút 🌐)

Nút **🌐** trên thanh trên cùng (hiện tên nguồn đang dùng, chấm xanh = ổn, chấm cam = có vấn đề) cho đổi nguồn dịch ngay trong buổi học, không cần vào Cài đặt:

| Nguồn | Khi nào dùng |
|---|---|
| **✨ Claude Pro/Max** | Chất lượng tốt nhất, dùng hạn mức gói đăng ký |
| **🔑 Claude API** | Có API key còn credit |
| **💻 Model trên máy (NLLB)** | Hết hạn mức Claude, không có mạng, hoặc muốn để dành hạn mức cho trợ lý. Miễn phí, chạy bằng CPU (~1 giây/câu), chất lượng cơ bản |

- **Tự dự phòng:** khi đang dùng Claude mà hết hạn mức/lỗi, phụ đề tự chuyển sang dịch **nháp** bằng model trên máy (chữ nghiêng, nhãn *nháp*). Muốn dịch hẳn bằng máy cho tới hết buổi, bấm **💻 Dùng model máy** trên banner đỏ hoặc chọn trong menu 🌐. Khi hạn mức Claude đã hồi, bấm **↻ Thử lại Claude**.
- Câu dịch bằng model trên máy có nhãn nhỏ **máy** trước bản dịch. Thuật ngữ trong **Bảng thuật ngữ** được giữ nguyên, nên thêm thuật ngữ của môn học sẽ giúp bản dịch máy chính xác hơn nhiều.
- Có hai model: **NLLB 600M** (nhanh, ~620 MB, đã đủ dùng) và **NLLB 1.3B** (dịch tốt hơn, chậm hơn ~2 lần, ~1,4 GB). Bấm **Tải** trong menu 🌐 để tải trước — model đang dùng vẫn chạy trong lúc tải — rồi chọn model khi đã tải xong.
- **Sau giờ học** (hoặc khi đổi lại sang Claude): nút **↻ Dịch lại N câu dịch máy bằng Claude** (trong menu 🌐 và trang Xem lại) dịch lại các câu đó bằng Claude để có bản ôn tập chuẩn.
- Trợ lý hỏi đáp, tóm tắt, flashcard vẫn cần Claude; model trên máy chỉ lo phần dịch phụ đề.

### 5.7 Nghỉ giải lao và gián đoạn

- **Nghỉ giải lao:** bấm **⏸** (hoặc Space). Tiếp tục: **▶**. Nếu chưa dừng chia sẻ, phụ đề chạy lại ngay; nếu đã dừng chia sẻ, app sẽ mời bạn chọn lại tab Teams.
- **Lỡ đóng tab LezioneLive:** mở lại <http://127.0.0.1:8765> → ở danh sách **Các buổi học** bấm **▶ Tiếp tục** → chọn lại nguồn âm thanh. Transcript không bị mất.
- **Lỡ đóng cửa sổ backend:** chạy lại `start.bat`, rồi làm như trên.
- **Mất mạng:** phụ đề tiếng Ý vẫn chạy vì Whisper chạy trên máy; bản dịch sẽ được **dịch bù** khi có mạng trở lại.
- **Vượt ngưỡng chi phí:** hộp thoại cho chọn **Tiếp tục** hoặc **Chuyển sang chỉ phụ đề tiếng Ý** (tắt dịch để không tốn thêm; bật lại bằng nút **Bật lại dịch** trên banner).

---

## 6. Sau buổi học

1. Bấm **⏹** → **Kết thúc buổi học**. App dừng chia sẻ âm thanh và chuyển sang trang **Xem lại**; các câu cuối được dịch nốt, rồi tóm tắt được tạo tự động (thường trong 1–2 phút).
2. **📝 Tóm tắt** gồm 7 mục: tổng quan, ý chính, khái niệm & định nghĩa, công thức, **bài tập / deadline / thông báo**, những đoạn cần xem lại, câu hỏi gợi ý kèm bản tiếng Ý.
3. **Xuất ghi chú:** **⬇ Markdown** (mở bằng VS Code hoặc Obsidian) hoặc **⬇ PDF**.
4. Các tab khác: **🗒 Transcript** (sửa được câu Ý), **📌 Ghim** (mỗi ghim có nút **Hỏi Claude**), **🃏 Flashcard**, **💸 Chi phí & độ trễ**.
5. **Hỏi trợ lý sau giờ học:** bấm nút mascot ở góc phải dưới (hoặc **Ctrl+K**) — khung trợ lý mở thành cột ngay cạnh transcript để vừa đọc vừa hỏi; bấm mốc `[mm:ss]` trong câu trả lời để nhảy tới đoạn đó. Bấm **✕** để thu gọn (app nhớ lựa chọn cho lần sau).
6. Ôn từ vựng hằng ngày ở trang **Flashcard**: lật thẻ bằng Space, tự chấm điểm 0–5.
7. Tìm lại kiến thức cũ ở trang **Tìm kiếm**: gõ tiếng Ý hoặc tiếng Việt, không cần gõ dấu.

---

## 7. Xử lý sự cố

| Hiện tượng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| *"Chưa nhận được âm thanh. Hãy chọn lại tab và tick 'Chia sẻ âm thanh tab'"* | Quên bật chia sẻ âm thanh, hoặc chọn "Cửa sổ"/"Toàn bộ màn hình" | Bấm **🎧 Chọn nguồn âm thanh** lại, chọn **Thẻ Chrome/Tab** và bật chia sẻ âm thanh |
| Không thấy tab Teams trong hộp thoại chia sẻ | Teams mở ở trình duyệt/hồ sơ khác, hoặc bằng app desktop | Mở Teams web trong **cùng** Chrome/Edge (cùng hồ sơ) với LezioneLive, hoặc dùng chế độ Âm thanh hệ thống |
| Link cuộc họp tự mở app Teams desktop | Trình duyệt chuyển sang app | Chọn **Tiếp tục trên trình duyệt này**, hoặc dùng chế độ Âm thanh hệ thống |
| Thanh 🎚 không nhảy dù giảng viên đang nói | Chọn nhầm tab, tab Teams bị tắt tiếng, hoặc giảng viên tắt micro | Kiểm tra bạn có nghe được không, rồi chọn lại đúng tab |
| Cảnh báo *"Không nhận được tiếng nói trong 60 giây"* | Giờ nghỉ, hoặc mất nguồn âm thanh | Nếu đang học: chọn lại nguồn âm thanh |
| *"Đã dừng chia sẻ âm thanh…"* | Đã bấm Dừng chia sẻ hoặc đóng tab Teams | Mở lại Teams, bấm **▶** trong LezioneLive và chọn lại tab |
| Không bắt đầu được buổi học, báo nhận dạng giọng nói chưa sẵn sàng | Model Whisper chưa tải/nạp xong | **Cài đặt → Nhận dạng giọng nói (STT) → Tải & nạp model**, đợi tới **STT sẵn sàng** |
| Chữ Ý hiện chậm (hơn 5 giây) | Whisper chạy bằng CPU với model lớn | Chọn model **small**; hoặc cài CUDA (`setup.ps1 -Gpu`) để dùng GPU; hoặc dùng STT cloud |
| *"GPU chưa dùng được vì thiếu thư viện CUDA 12"* | Chưa cài thư viện CUDA | Chạy `setup.ps1 -Gpu` rồi khởi động lại LezioneLive |
| Banner *"API key không hợp lệ hoặc chưa có"* | Key sai, bị thu hồi, hoặc hết credit | **Cài đặt → Claude API key**, nhập key mới. Phụ đề Ý vẫn chạy; bản dịch tự bù sau khi lưu key |
| Banner *"Chưa dịch được bằng Claude…"* giữa buổi học | Hết hạn mức gói Claude Pro/Max, Claude Code chưa đăng nhập, hoặc API hết credit | Bấm **💻 Dùng model máy** để dịch tiếp bằng NLLB; khi hạn mức hồi bấm **↻ Thử lại Claude**; sau giờ học dùng **↻ Dịch lại … bằng Claude** |
| Banner *"Chưa có model dịch trên máy…"* | Đang chọn nguồn dịch model trên máy nhưng chưa tải model | Bấm **⬇ Tải** trên banner (hoặc trong menu 🌐) và đợi tải xong — các câu đang chờ tự được dịch |
| Khung 🖥 Màn hình màu đen / không có hình | Đã dừng chia sẻ, hoặc cửa sổ được chia sẻ đang thu nhỏ | Mở lại cửa sổ đó (không thu nhỏ), hoặc bấm **🔄 Đổi nguồn** để chọn lại |
| Banner *"Mất kết nối, bản dịch sẽ bù khi có mạng"* | Mất Internet | Chờ có mạng, không cần làm gì |
| Nhãn **🐢 Dịch đang chậm** | Claude API đang giới hạn tốc độ | App tự thử lại và gộp câu, không cần làm gì |
| *"Mất kết nối WebSocket, đang kết nối lại…"* | Backend bận hoặc vừa khởi động lại | App tự kết nối lại và tải bù các câu bị lỡ |
| Trang **Backend chưa chạy** | Chưa mở hoặc đã đóng cửa sổ backend | Nháy đúp `scripts\start.bat` |
| Không xuất được PDF | Máy không có Chrome/Edge ở vị trí mặc định | Xuất **HTML**, mở file bằng trình duyệt, bấm Ctrl+P → Lưu thành PDF |
| Chế độ Âm thanh hệ thống thu cả nhạc/âm báo | Chế độ này thu mọi âm thanh của máy | Tắt các ứng dụng phát âm thanh khác, hoặc chuyển sang Teams web |

---

## 8. Quyền riêng tư và phép lịch sự

- **Xin phép giảng viên** trước khi ghi lại hoặc phiên âm bài giảng (ở Ý áp dụng GDPR). Bạn có thể nhờ tab **Hỏi GV** soạn một câu xin phép bằng tiếng Ý.
- Mặc định app **không lưu file âm thanh gốc** (xem **Cài đặt → Quyền riêng tư**). Transcript chỉ nằm trên máy bạn — đừng chia sẻ lại transcript hay bản ghi bài giảng.
- Chỉ văn bản được gửi tới Claude. Nếu chọn STT cloud (Deepgram/Azure) thì âm thanh sẽ được gửi tới dịch vụ đó.

---

## 9. Câu hỏi thường gặp

**Mỗi buổi học tốn bao nhiêu tiền API?**
Phần lớn là chi phí dịch từng câu bằng Claude Haiku: ước tính khoảng **2–3 USD cho buổi 3 giờ** (tuỳ độ dài bảng thuật ngữ và slide), cộng vài cent cho tóm tắt, flashcard và mỗi câu hỏi Claude. Con số thực tế nằm ở badge chi phí trên màn hình học và tab **💸 Chi phí & độ trễ**; sau buổi đầu tiên, chỉnh lại ngưỡng cảnh báo ở **Cài đặt → Chi phí** rồi bấm **💾 Lưu cài đặt**.

**Có dùng song song phụ đề trực tiếp (live captions) của Teams được không?**
Được. Bật hay tắt phụ đề của Teams không ảnh hưởng tới LezioneLive.

**Giảng viên chiếu video có tiếng thì sao?**
Âm thanh video trong Teams cũng được nhận dạng và dịch như lời giảng.

**Muốn thêm cột tiếng Anh?**
**Cài đặt → Model Claude → Ngôn ngữ phụ → Tiếng Anh**, rồi bấm **💾 Lưu cài đặt**.

**Dữ liệu nằm ở đâu?**
Windows: `%LOCALAPPDATA%\LezioneLive` (file `lezionelive.db`, thư mục `slides`, `logs`); macOS: `~/Library/Application Support/LezioneLive`. Sao lưu thư mục này là đủ.

**Xem phụ đề trên điện thoại hoặc máy khác được không?**
Không. Để bảo vệ dữ liệu, backend chỉ nhận kết nối từ chính máy đang chạy (127.0.0.1).

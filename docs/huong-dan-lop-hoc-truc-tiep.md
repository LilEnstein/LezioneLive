# Hướng dẫn dùng LezioneLive trong lớp học trực tiếp

Khi học trực tiếp tại Ý, LezioneLive thu lời giảng **trong phòng học thật** bằng micro laptop hoặc **điện thoại đặt gần bục giảng**, lọc nhiễu, nhận dạng tiếng Ý, dịch sang tiếng Việt, phân biệt **giảng viên / sinh viên hỏi**, và cho bạn **chụp bảng** để Claude đọc công thức, thuật ngữ. Đặc tả đầy đủ: [feature_spec.md](../feature_spec.md).

> Không phải tư vấn pháp lý. Ghi âm bài giảng ở Ý chịu quy định GDPR và quy chế của trường: **luôn xin phép giảng viên trước**.

---

## Tóm tắt nhanh

1. **Ở nhà (có mạng):** mở LezioneLive → **Cài đặt → Lớp học trực tiếp** → tải model nhận diện giọng (~26 MB); bật **Dịch nháp ngoại tuyến** nếu Wi-Fi trường hay mất và tải model (~620 MB).
2. **▶ Bắt đầu buổi học** → chọn môn → **🎓 Lớp học trực tiếp** → xác nhận **đã xin phép giảng viên** (lần đầu mỗi môn) → **Tiếp: kiểm tra âm thanh**.
3. Chọn nguồn: **💻 Micro laptop** hoặc **📱 Điện thoại làm micro** (quét QR).
4. Chọn/tạo **phòng học** (ví dụ "Aula 5 — Ecotekne") → **Kiểm tra âm thanh 30 giây** → xem đánh giá và gợi ý.
5. **▶ Bắt đầu buổi học.** Trong lúc học: 📷 chụp bảng, 📌 ghim, 💬 hỏi Claude như bình thường.
6. Học xong: **⏹ Kết thúc** → tóm tắt, ghi chú (có ảnh bảng đúng mốc thời gian), flashcard.

---

## 1. Xin phép giảng viên (bắt buộc, mỗi môn một lần)

- Ứng dụng **không cho bắt đầu thu âm** ở môn chưa xác nhận xin phép.
- Bấm **✉️ Soạn tin xin phép (tiếng Ý)** → nhập tên của bạn → **Soạn bằng Claude**: nhận email tiếng Ý trang trọng (nói rõ bạn là sinh viên quốc tế, công cụ chỉ tạo phụ đề cho mục đích học tập cá nhân, **không lưu âm thanh**, **không chia sẻ lại**) kèm bản dịch ngược để kiểm tra. Sửa tuỳ ý rồi **📋 Copy** và gửi cho giảng viên.
- Khi giảng viên đồng ý: tick **"Tôi đã xin phép giảng viên…"** → **Xác nhận**. Thời điểm xác nhận được lưu vào môn học.
- Nếu giảng viên không đồng ý: vẫn có thể dùng **chụp bảng** và **hỏi đáp sau giờ** (không thu âm).

## 2. Chọn nguồn âm thanh

| | 💻 Micro laptop | 📱 Điện thoại làm micro |
|---|---|---|
| Khi nào | Ngồi 3 hàng đầu, phòng không quá ồn | Ngồi xa, phòng vang/ồn |
| Cách đặt | Mở laptop, micro hướng về bục giảng | Đặt điện thoại trên bàn đầu, gần giảng viên |
| Ghi chú | Chọn đúng micro (micro USB/tai nghe tốt hơn micro tích hợp) | Laptop vẫn xử lý và hiển thị; có thể tự chuyển sang micro laptop nếu điện thoại mất kết nối |

Micro rời (micro cài áo không dây, micro USB đa hướng) thường cải thiện chất lượng nhiều hơn mọi xử lý phần mềm.

## 3. Ghép nối điện thoại

1. Điện thoại và laptop phải ở **cùng một mạng**: cùng Wi-Fi, hoặc điện thoại kết nối vào **hotspot của laptop** (Windows: Cài đặt → Mạng → Điểm phát sóng di động).
2. Trên laptop bấm **📱 Ghép nối điện thoại** → quét **mã QR** bằng camera điện thoại. Mã **dùng một lần**, hết hạn sau **5 phút**.
3. Lần đầu, điện thoại cảnh báo **chứng chỉ tự ký** (LezioneLive tự tạo để có HTTPS — trình duyệt chỉ cho dùng micro trên HTTPS):
   - **Android Chrome:** "Nâng cao" → "Tiếp tục truy cập …".
   - **iPhone Safari:** "Hiển thị chi tiết" → "truy cập trang web này" → xác nhận.
   - Muốn hết cảnh báo vĩnh viễn: tải chứng chỉ CA tại địa chỉ `https://<IP laptop>:8766/ca.crt` (hiện trong hộp ghép nối) và cài lên điện thoại một lần (iPhone: Cài đặt → Cài đặt chung → VPN & Quản lý thiết bị → cài hồ sơ, rồi Giới thiệu → Cài đặt tin cậy chứng nhận → bật). Đối chiếu dấu vân tay SHA-256 hiện trong Cài đặt.
4. Chọn chế độ trên điện thoại:
   - **🎤 Micro** — màn hình gần như đen (chỉ chấm đỏ REC, mức âm, SNR), giữ màn hình sáng để tiếp tục thu. Nếu điện thoại không hỗ trợ giữ màn hình sáng, hãy tắt tự khoá màn hình.
   - **💬 Phụ đề** — xem bản dịch chữ lớn trên điện thoại; có 📌 Ghim, 💡 Giải thích, 📷 Bảng, 🙋 Hỏi (soạn câu tiếng Ý ngắn để giơ tay hỏi, kèm cách đọc).
   - **🎤💬 Cả hai** — chỉ mang điện thoại vẫn cần laptop chạy backend ở gần (trong balo cũng được).
5. Lần sau điện thoại **tự kết nối lại** (không cần quét QR) cho tới khi bạn **Thu hồi** thiết bị trong Cài đặt.

**Không kết nối được?**
- Wi-Fi trường thường chặn các thiết bị nói chuyện với nhau (*client isolation*): dùng **hotspot của laptop hoặc điện thoại**, chọn đúng mạng hotspot trong hộp ghép nối rồi tạo mã mới.
- Lần đầu Windows hỏi cho phép **Python** truy cập mạng: chọn **Private networks**. Nếu mạng đang đặt là *Public*, Windows có thể chặn — dùng hotspot hoặc đổi mạng sang *Private*.
- Server điện thoại chỉ mở khi bạn bấm ghép nối (cổng 8766) và có thể tắt bằng **Tắt kết nối điện thoại**.

## 4. Kiểm tra âm thanh (sound check, 30 giây)

1. **Bước 1 (10 giây): giữ yên lặng** để đo tiếng ồn nền.
2. **Bước 2 (20 giây): khi giảng viên bắt đầu nói** (hoặc bạn tự nói từ khoảng cách tương đương).
3. Kết quả: **ồn nền (dBFS)**, **SNR** và đánh giá **🟢 Tốt (≥ 15 dB) / 🟡 Tạm được (10–15 dB) / 🔴 Kém (< 10 dB)** kèm gợi ý cụ thể (ngồi gần hơn, dùng điện thoại đặt bàn đầu, tránh điều hoà/máy chiếu, bật khử nhiễu mạnh…).
4. Kết quả được lưu vào **hồ sơ phòng học**: lần sau chọn lại phòng đó, ngưỡng phát hiện giọng nói, mức khử nhiễu và mức âm tối thiểu được áp dụng tự động (có thể bỏ qua sound check).

## 5. Trong buổi học

- Thanh trên cùng: **● REC** (luôn hiện khi đang thu), nguồn **💻/📱**, **SNR**, mạng **📶/📡**, pin laptop.
- **Khử nhiễu** (dưới cùng): Tắt / Nhẹ (RNNoise) / Mạnh. Nếu "Mạnh" làm máy chậm, app tự hạ xuống "Nhẹ" và báo cho bạn.
- **🍃 Tiết kiệm pin**: Whisper `small` int8 (cần tải model small trước), tắt phân biệt người nói, giao diện cập nhật 1 lần/giây. Pin < 20% → nhắc bật; pin < 10% → tự bật và bảo đảm transcript đã ghi xuống đĩa.
- **Người nói**: sau khoảng 2 phút đầu, app tự lập mẫu giọng giảng viên (người nói nhiều nhất). Câu của sinh viên hiện thụt lề với nhãn 🙋 *Sinh viên hỏi*. Nhãn sai? Bấm vào câu → **🔁 Đổi người nói**; app học lại từ sửa đổi.
- **📷 Chụp bảng**: webcam hoặc chọn/chụp ảnh. Claude đọc chữ, công thức (LaTeX), thuật ngữ và bản dịch; ảnh hiện xen kẽ trong transcript đúng mốc thời gian. Thuật ngữ mới là **đề xuất** — bấm ✓ để thêm vào bảng thuật ngữ. Nội dung bảng được dùng làm ngữ cảnh dịch trong **10 phút** tiếp theo. Ảnh mờ/loá: app báo "Không đọc được ảnh" và không bịa nội dung.
- **Mất Wi-Fi**: phụ đề tiếng Ý vẫn chạy (Whisper cục bộ), thanh trên báo *"Ngoại tuyến — N câu chờ dịch"*; nếu bật dịch nháp, câu có bản dịch **nháp** (NLLB, không dùng bảng thuật ngữ). Có mạng lại: dịch bù theo thứ tự, gộp tối đa 5 câu mỗi lần, câu mới luôn được ưu tiên; bản Claude thay thế bản nháp.
- **Điện thoại mất kết nối**: sau 3 giây, nếu bật tuỳ chọn, app chuyển sang micro laptop và ghi mốc ⚡ trong transcript; điện thoại kết nối lại thì tự quay về.
- **🙋 Hỏi trực tiếp**: tab *Hỏi GV* → **🙋 Hỏi trực tiếp** — gõ tiếng Việt, nhận câu tiếng Ý ≤ 25 từ kèm cách đọc (âm tiết in hoa là trọng âm).

## 6. Quyền riêng tư

- **Mặc định không lưu âm thanh**, chỉ lưu văn bản; âm thanh chỉ nằm trong bộ nhớ lúc xử lý. Bật "Lưu file âm thanh gốc" cần xác nhận thêm, và với lớp học trực tiếp cần xác nhận lại khi bắt đầu buổi.
- **Không lưu lời người khác** (Cài đặt): câu của bạn học chỉ hiển thị trong lúc học; sau khi dịch (và chắc chắn khi kết thúc buổi) nội dung bị xoá khỏi máy, không vào tìm kiếm, tóm tắt hay file ghi chú.
- Mẫu giọng (embedding) chỉ nằm trong bộ nhớ của buổi học, không ghi xuống đĩa.
- File ghi chú xuất ra có dòng nhắc **"Tài liệu học tập cá nhân — không chia sẻ lại"** ở đầu.
- Âm thanh không rời khỏi máy (điện thoại → laptop qua mạng nội bộ, mã hoá TLS). Chỉ văn bản và ảnh bảng bạn chụp được gửi tới Claude. Nếu chọn STT cloud (Deepgram/Azure) thì âm thanh được gửi tới nhà cung cấp đó.
- Nên hỏi trường xem có dịch vụ hỗ trợ sinh viên quốc tế / phụ đề chính thức không.

## 7. Đo chất lượng nhận dạng (tuỳ chọn)

Để chọn cấu hình tốt nhất cho phòng học của bạn (mục 15 của đặc tả), dùng script đo WER với một bản ghi có transcript chuẩn:

```powershell
backend\.venv\Scripts\python scripts\eval_room.py --wav bai_giang.wav --ref bai_giang.txt --snr 20 10 5 --whisper small large-v3-turbo
```

Script trộn tạp âm ở nhiều mức SNR (và tiếng vang tổng hợp), chạy các mức khử nhiễu × model Whisper, in bảng WER và thời gian xử lý.

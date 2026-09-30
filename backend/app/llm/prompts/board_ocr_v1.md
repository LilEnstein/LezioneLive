Đây là ảnh chụp bảng hoặc màn chiếu trong một lớp học đại học tại Ý
(môn {course_name}). Ảnh có thể nghiêng, loá, hoặc bị che một phần.

Trả về DUY NHẤT một JSON:
{
  "text_it": "toàn bộ chữ đọc được, giữ xuống dòng",
  "latex": ["công thức 1", "..."],
  "terms": [{"it": "...", "vi": "...", "en": "..."}],
  "translation_vi": "bản dịch tiếng Việt của text_it",
  "unreadable": "mô tả vùng không đọc được, hoặc chuỗi rỗng"
}
Không bịa nội dung không nhìn thấy rõ.

QUY TẮC BỔ SUNG:
- text_it: chép đúng chữ nhìn thấy (tiếng Ý, ký hiệu, số), không thêm nội dung suy đoán. Công thức vẫn
  chép vào text_it ở dạng đọc được và viết lại bằng LaTeX trong "latex" (không có dấu $ bao quanh).
- terms: thuật ngữ chuyên ngành tiếng Ý xuất hiện trên bảng (tối đa 15), mỗi mục gồm dạng chuẩn tiếng Ý,
  thuật ngữ tiếng Việt chuẩn trong ngành và tiếng Anh. Ưu tiên dùng bản dịch trong bảng thuật ngữ nếu có.
- Nếu ảnh quá mờ/tối/loá đến mức không đọc được chữ nào: text_it = "", latex = [], terms = [],
  translation_vi = "", và mô tả lý do trong "unreadable".

BẢNG THUẬT NGỮ HIỆN CÓ (tham khảo):
{glossary_table}

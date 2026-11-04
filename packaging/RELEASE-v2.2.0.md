# Disk Growth Monitor 2.2.0 — Parquet + DuckDB

Bản thử sửa chênh lệch dung lượng giữa hai mốc:

- Tính hiệu **Mới − Cũ** trên toàn bộ dữ liệu đã ghi nhận, gồm mục chỉ xuất hiện ở một mốc. Cây tính toàn nhánh; lọc và phân trang không làm đổi tổng.
- Gộp Chung/Riêng trong bảng chính và thống kê. Cột ngắn, tooltip giải thích ký hiệu và các loại dung lượng.
- Tách số Windows đã dùng, cấp phát ghi nhận và phần chênh chưa quy được vào file. Thiếu một bản ghi không đồng nghĩa đã xóa file.
- Đọc lại snapshot Parquet cũ; chi tiết phép so sánh được tính lại khi mở, không cần quét lại hay viết lại snapshot.

Tải `installer.exe` và đối chiếu `SHA256SUMS.txt`. Bộ cài tự chứa Python/Qt/DuckDB, không cần môi trường Python chung. Đây là bản thử tải thủ công; không dùng trình cập nhật tự động để nhận bản này.

Đóng app và dừng lượt quét trước khi cài. Có thể cài thử vào thư mục riêng hoặc chọn đúng thư mục của bản cài cũ để nâng cấp. Không chọn thư mục mã nguồn. Bộ cài và GUI vẫn yêu cầu UAC.

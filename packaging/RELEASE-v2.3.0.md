# Disk Growth Monitor 2.3.0 — Parquet + DuckDB

- Ghi mã mốc, ngày giờ và tổng dung lượng Cũ → Mới ngay cạnh tab **Chi tiết**.
- Dung lượng trong GUI dùng GB/MB/KB. Chênh lệch hiển thị một số, ví dụ `↓ −6,68 GB`; byte gốc và cơ số GB Windows không đổi.
- Tab **Tóm tắt** hiển thị tổng quan, tách chênh lệch ghi nhận khỏi chênh lệch ổ Windows, cùng top 10 thư mục mỗi nhóm tăng, giảm, thêm và mất. Các mục vắng một phía chưa xác minh được ghi **Chỉ Cũ** hoặc **Chỉ Mới**.
- Xếp hạng toàn cặp mốc, không dựa vào trang hay bộ lọc Chi tiết. Chỉ tải khi mở tab; không quét lại ổ hoặc tạo thêm chỉ mục lâu dài.

Cập nhật trong app hoặc tải `installer.exe` và kiểm tra bằng `SHA256SUMS.txt`. Bản Windows x64 giữ định dạng Parquet, lịch sử và cấu hình. Đóng các lượt đang chạy trước khi nâng cấp.

# Disk Growth Monitor 2.4.0 — Parquet + DuckDB

- Tab **Chuyển ứng dụng** nhận EXE hoặc shortcut `.lnk` để gợi ý thư mục, và cho nhập đường dẫn thủ công. Chọn từng mục và xem trước nguồn, đích, dung lượng trước khi chuyển.
- Hỗ trợ dữ liệu riêng và app portable được xác nhận giữa hai phân vùng NTFS cục bộ. Sao chép, kiểm SHA-256 rồi tạo junction để đường dẫn cũ vẫn hoạt động.
- Giữ bản nguồn, có nhật ký và **Hoàn tác**. **Dọn bản cũ** là bước riêng; không ghi đè thư mục đích hay tự xóa dữ liệu mới. CLI: `dgm app-move`.
- Không tự dời bộ cài Windows/Store hoặc thay đổi Task Scheduler/Service của app khác. Không duyệt Chrome/Edge.

Cập nhật trực tiếp trong app hoặc tải `installer.exe` cho Windows x64 và kiểm tra bằng `SHA256SUMS.txt`. Đóng tác vụ đang chạy trước khi nâng cấp. Giữ nguyên lịch sử Parquet, cấu hình và log.

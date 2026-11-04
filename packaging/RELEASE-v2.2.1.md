# Disk Growth Monitor 2.2.1 — Parquet + DuckDB

- Quét mới dùng hạn mức export riêng, không bị CSV của lượt cũ chiếm phần. Kiểm tra dung lượng thực trước khi xuất và báo rõ ổ lưu đang thiếu bao nhiêu.
- Thành công: chỉ giữ snapshot Parquet, bỏ CSV tạm. Quét dở: bỏ nguồn chưa hoàn chỉnh, giữ log.
- Xuất đủ nhưng chưa lưu được: mặc định chỉ giữ một điểm tiếp tục nén đã kiểm checksum (64 MiB). Tiếp tục đọc trực tiếp từ bản nén. Nếu không thể nén an toàn và chưa có điểm cũ, giữ một nguồn gốc để tránh mất dữ liệu.
- Nếu đã có điểm lưu, lượt tự động giữ điểm cũ, không tích thêm CSV. Nhật ký → Quét mới có thể chọn thay điểm cũ sau khi bản nén mới đã được xác minh.
- Không tự xóa CSV tồn tại trước khi cập nhật, không đổi schema Parquet. Tải lại cặp mốc đã đổi không còn báo KeyError phiên so sánh.

Cập nhật trong app hoặc dùng `installer.exe` (đối chiếu `SHA256SUMS.txt`). Đóng lượt đang quét trước khi nâng cấp. Bản cài và GUI vẫn yêu cầu UAC, giữ lịch sử/config và không cần Python hệ thống.

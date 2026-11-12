# Disk Growth Monitor 2.2.2 — Parquet + DuckDB

- Ghi nhớ profile quét. Nếu chưa có lựa chọn và chỉ có một profile đang chạy lịch, ưu tiên profile đó. Kết quả quét và nhật ký ghi profile thực tế.
- Danh sách mốc xếp theo thời điểm chụp, không phụ thuộc profile quét. Mốc Mới theo lượt mới nhất hoặc cho ghim mốc cũ. Mốc Cũ chỉ liệt kê lượt trước Mới.
- Không giữ kết quả cặp trước khi đổi mốc. Cây/tổng quan ghi ngày, số mốc và phần MiB/MB để dễ đối chiếu; không thay đổi phép tính byte hay schema Parquet.
- Mỗi lần mở, kiểm tra Release chính thức ở nền và thông báo nếu có bản mới. Mặc định bật; tắt tại **Hệ thống → Cập nhật app**. Không tự tải/cài.

Cập nhật trực tiếp trong app hoặc tải `installer.exe` cùng `SHA256SUMS.txt`. Bộ cài giữ lịch sử, cấu hình và log; đóng lượt quét trước khi nâng cấp và chọn Yes khi UAC hiện.

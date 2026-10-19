# Disk Growth Monitor 2.1.0 — Parquet + DuckDB

Sửa so sánh giữa các mốc quét tay/lịch chạy có MFT bật/tắt khác nhau, đồng thời giữ nguyên GUI đầy đủ, lịch sử và lõi Parquet + DuckDB.

- Quét tay và Chạy ngay trong lịch dùng cấu hình đã lưu. Khi còn thay đổi chưa lưu, chọn **Lưu và chạy / Dùng cấu hình đã lưu / Hủy**. Tab lịch hiển thị công cụ và MFT bật/tắt; quyền Admin không tự bật MFT.
- Tự dùng **Kích thước file** cho cả phép xem nếu một mốc thiếu toàn bộ cấp phát theo file, kèm thông báo rõ. Vẫn có thể chọn xem **Chiếm trên đĩa** với những giá trị chưa xác định. Logical không phải Windows đã dùng.
- Ngưỡng dung lượng không còn âm thầm loại mục chưa rõ số đo như file 0 B. Giữ đúng Chung/Riêng theo hiện diện đường dẫn, không coi thiếu số đo là thêm/xóa file.
- Mốc đầu hiển thị dung lượng Mới; Cũ và hai cột chênh lệch là `—`, trạng thái **Mốc ban đầu**, không tính tăng giả từ 0.
- Cây, bảng, tile và thanh trạng thái dùng cùng chỉ số. Tiêu đề tile phân biệt kích thước file; đổi chỉ số không làm mất bộ đếm trạng thái gốc. Giữ lọc/sắp xếp toàn tập, phân trang và tải thêm 20–1000 mục.
- Không ghi lại snapshot cũ, không lấy số đo hiện tại điền ngược lịch sử. Phần kích thước file còn lưu vẫn so sánh được; cấp phát thiếu tiếp tục ghi chưa rõ.

## Cài / nâng cấp

Tải `installer.exe`, đối chiếu `SHA256SUMS.txt`. GUI và bộ cài yêu cầu UAC; Python/Qt/DuckDB được đóng gói riêng trong `.venv`, không cài pip/Python hệ thống. WizTree không được đóng gói.

Dùng cập nhật trong app hoặc cài đè đúng thư mục bản cũ. Nâng cấp giữ lịch sử Parquet, JSONL cũ, cấu hình và log. Đóng app/dừng quét trước khi nâng cấp; cài thủ công cần tắt/dừng bộ chạy nền liên quan. Không tự chuyển/xóa lịch sử cũ lúc mở app và không tự đăng ký Task/Service.

MFT bật/tắt trong profile khác với **Run with highest privileges** trong Task Scheduler. Để các lần quét mới có cấp phát đáng tin cậy, bật MFT ở profile WizTree rồi **Lưu** và dùng Task có quyền Admin.

## Kiểm chứng

`test-summary.json` đi kèm chỉ chứa kết quả tổng hợp của kiểm thử hồi quy/DPI/bộ cài trên fixture riêng, gắn với đúng SHA-256 của installer. Không phát hành log cá nhân, ảnh mốc thật, dữ liệu quét, venv phát triển hoặc cache kiểm thử lên GitHub.

- 584 bài hồi quy đạt; hai lượt DPI 125% và 150% đều đạt 29/29.
- 9 kiểm tra bộ cài/frozen CLI/GUI/cấp phát thiếu/mốc đầu/cài đè/gỡ và 4 kiểm tra cập nhật đạt trên đúng installer phát hành, gồm nâng cấp thật từ 1.5.0 và 2.0.0.
- Lịch sử/config giữ nguyên checksum khi nâng cấp; snapshot Parquet cũ so sánh được mà không cần nhập lại. Installer sai checksum bị từ chối.
- Fixture của lượt QA thành công đã được dọn sau kiểm tra, chỉ giữ log/kết quả. Không thay bản app hoặc Task/Service thật trên C: trong QA.

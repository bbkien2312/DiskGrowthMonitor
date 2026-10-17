# Disk Growth Monitor 1.5.0

Triển khai phương án đã duyệt: đóng app có xác nhận, checkpoint bền vững, tiếp tục từ dữ liệu đã chụp hoặc quét lại nếu chưa đủ; mặc định cấp phát vật lý và đối soát Windows; màu theo hiện diện và dấu tăng/giảm.

Không phát lại lệnh log hoặc tự tiếp tục xóa. Không sao lưu nội dung file/database nguyên khối. Dữ liệu chụp hoàn chỉnh giữ thời điểm và phạm vi nguồn; cập nhật tiếp tục không biến mốc cũ thành hiện trạng.

GUI/CLI dùng chung hàng đợi; JSONL checksum và kiểm tra tệp xuất trước khi dùng, khóa chống tiếp tục trùng, hạn mức mặc định 512 MB. Quét lại/tiếp tục tạo mốc riêng, giữ lượt cũ. Bỏ checkpoint chỉ bỏ tệp riêng, không bỏ lịch sử hay file đã quét.

Số đo Windows đối chiếu theo thời điểm xuất nếu có. Không gán phần chưa quy vào file cho “file nhỏ chưa quét”, không ép/cắt số khi vượt Windows. Bản cũ thiếu số đo giữ unknown; có thống kê ≤10 MB sau chuẩn hóa metadata khi đủ nguồn.

Mở app chỉ nạp mốc/tóm tắt đã lưu, không tự đọc toàn bộ log hoặc dựng so sánh. Đổi đơn vị/số đo của tóm tắt cũng không nạp chi tiết. Bấm So sánh để dựng chỉ mục tạm khi cần; đóng tiến trình cuối sẽ bỏ chỉ mục để tiết kiệm đĩa.

SQLite nguồn cũ chuyển một lần sang JSONL nén, kiểm chứng trước khi thu hồi. Gọi lại bỏ qua phần đã hoàn thành; nguồn thay đổi, log hỏng hoặc dừng thì giữ nguồn. Có thể tắt tự chuyển bằng history_auto_migrate=false hoặc chọn history_format=sqlite. Bước này không tự xóa CSV hoặc backup cập nhật.

File ≤10 MB vẫn nằm trong tổng; ngưỡng chỉ lọc bảng chi tiết. Lịch sử cũ thiếu số đo giữ Chưa rõ; không cắt số cho khớp Windows hoặc dùng dung lượng hiện tại để sửa mốc cũ.

GUI và installer yêu cầu UAC Admin. Nâng cấp giữ data/log/cấu hình; không cài Python/pip/PATH toàn hệ thống, không đăng ký Task/Service tự động, không đóng gói WizTree hoặc dữ liệu cá nhân.

Windows x64. Bộ cài chưa ký mã; kiểm tra nguồn và SHA-256 trước khi chạy. Các bài thử chỉ dùng fixture riêng trên D:, không thay bản đang dùng hoặc lịch sử trên C:.

Tài sản phát hành: installer.exe, SHA256SUMS.txt và test-summary.json (chỉ tổng hợp kiểm thử, không chứa đường dẫn/lịch sử của máy).

Kiểm chứng bản phát hành: 496 bài kiểm thử; 64 bài GUI tại mỗi DPI 125% và 150%; 6 kiểm tra checkpoint trên CLI đóng gói. Bộ cài qua 13 kiểm tra cài mới/GUI/cập nhật/gỡ và 8 kiểm tra nâng cấp tại chỗ, gồm chuyển từ 1.3.0 SQLite và giữ dữ liệu 1.4.0 JSONL. Các phép thử chỉ dùng dữ liệu giả riêng trên D:.

installer.exe: 34.088.791 byte. SHA-256: `3faf9c4d5c2034dab83150057243ccb66ac2423115ee751b69f089a2e81fbe60`.

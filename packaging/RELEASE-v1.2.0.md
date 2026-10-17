# Disk Growth Monitor 1.2.0

## Tải và cài

Tải `installer.exe` và kiểm tra SHA-256 trong `SHA256SUMS.txt`. Windows 10/11 x64, không cần Python hệ thống. Bộ cài và GUI đều yêu cầu UAC Admin. Cập nhật từ app giữ vị trí bản cài, data/lịch sử, cấu hình và log; không cần chọn thư mục mới.

## Thay đổi chính

- Kiểm tra & chuẩn hóa lịch sử: chọn một/nhiều/các lượt chuẩn cũ, xem trước/sau rồi áp dụng; giữ bản gốc và có thể dùng lại. GUI và CLI dùng chung cơ chế.
- Chuẩn hóa file/thư mục/reparse/hardlink, metadata thiếu và tổng dung lượng; tổng quan, cây và chi tiết đồng bộ. Cache so sánh và con trỏ theo đúng phiên bản dữ liệu.
- Sửa parser WizTree (ngày Windows, CREATEDDATE/MFTRECNO, cột dung lượng ổ tùy chọn), lưu nguồn CSV nén có SHA-256 và giới hạn lưu. Nhật ký nhập tách phần bị loại trừ/reparse/hardlink. Lượt mới dùng chuẩn hóa lúc nhập, không xử lý toàn bộ lần nữa khi so sánh.
- Size theo đường dẫn và Allocated là hai phép đo khác nhau; chọn GB/GiB. Size có thể vượt sức chứa ổ do hardlink/nén/sparse, không tự ép về bằng dung lượng ổ.
- Khi khác bộ loại trừ, thông báo nêu rõ nguyên nhân; Chỉ phần chung tính lại tổng đúng vùng giao nhau và không coi là toàn ổ.
- Khu vực trạng thái chỉnh chiều cao, thanh tiến độ 0–100%, tóm tắt công việc rõ ràng; log `[DEBUG]`/`[INFO]`/`[WARNING]`/`[ERROR]` có màu. Bấm thông báo hiện lại cửa sổ app.
- Giao diện gọn hơn, ưu tiên cây/chi tiết; lịch sử tự nạp; một nút Cập nhật kiểm tra/tải/sao lưu/cài/xác minh và mở lại.
- Môi trường và công cụ riêng của app, không cài package Python toàn hệ thống. Không đóng gói WizTree; cần executable hợp lệ của người dùng.

## Giới hạn lịch sử cũ

Chuẩn hóa không khôi phục dữ liệu chưa từng được quét. Nếu số cấp phát/ngày đã mất và không còn nguồn gốc, app báo thiếu nguồn/chưa xác định, không đọc file hiện tại để giả làm lịch sử. Kiểm chứng phép tính không chứng minh toàn bộ phạm vi của engine ngoài đã được quét. Nguồn CSV có thể hết hạn theo giới hạn lưu.

Không đưa lịch sử/log/dữ liệu cá nhân vào mã nguồn hoặc bộ cài.

## Kiểm chứng

- 279 bài kiểm thử tự động, gồm parser xuất thực tế của bốn công cụ, phân trang/sắp xếp, hardlink, dữ liệu thiếu, chuẩn hóa/hoàn tác, UAC khởi chạy và giao diện.
- Đối soát bản sao lịch sử thực tế gần 3 GB: hai cặp cùng phạm vi và một cặp khác công cụ trong phần phạm vi chung; tổng quan/cây khớp nhau, file lịch sử gốc giữ nguyên SHA-256.
- Bộ cài được kiểm thử riêng trên D:, không quét C:, không sửa bản app đang dùng và không đăng ký/gỡ Task hoặc Service của máy.

Task cũ chạy quyền thấp có thể cần cài lại từ GUI Admin để dùng mức HighestAvailable; bản cập nhật không tự sửa đăng ký Task/Service của người dùng. Bộ cài chưa ký mã, vì vậy hãy kiểm tra nguồn tải và SHA-256 khi Windows hiện Unknown publisher/SmartScreen.

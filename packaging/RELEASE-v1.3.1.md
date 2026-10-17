# Disk Growth Monitor 1.3.1

## Lịch sử metadata JSONL nén, chỉ mục tạm

- Bản cài mới lưu lịch sử dưới dạng **JSONL gzip**: đường dẫn, loại, byte logic/cấp phát, thời gian, trạng thái; **không chứa nội dung file đã quét**. Tóm tắt so sánh/benchmark, cấu hình và lớp chuẩn hóa được giữ ở metadata/log riêng.
- SQLite chỉ dùng làm chỉ mục tạm dùng chung giữa GUI/CLI đang mở. Giữ dữ liệu hàng của các mốc cần thiết; khi tiến trình cuối đóng thì bỏ ba database chỉ mục. Có nút **Xóa chỉ mục tạm**; log vẫn còn để so sánh lại.
- Bản cũ: **Hệ thống → Dung lượng app → Chuyển lịch sử sang JSONL nén**, hoặc `storage migrate-history --yes --retire-old`. Chuyển từng mốc, đọc lại checksum/số bản ghi; bảo vệ nguồn cũ nếu lỗi, bị dừng hoặc có tác vụ khác ghi trong lúc chuyển. Chỉ sau xác minh mới bỏ SQLite cũ, CSV nguồn và backup cập nhật hoàn tất theo xác nhận. Không tự chuyển/xóa lịch sử cũ khi vừa cài đè.
- Lần mở cặp mốc sau khi bỏ chỉ mục cần đọc log và lập chỉ mục toàn bộ cặp để tính tổng/sắp xếp đúng, không chỉ đọc 20 dòng. Sau đó tải trang nhỏ như trước. CLI từng lệnh riêng có thể phải dựng lại nếu không còn GUI/CLI khác giữ phiên; đây là đánh đổi để không lưu cache lớn lâu dài.
- Mặc định không giữ thêm CSV WizTree nguồn. Không có chức năng phục hồi nội dung file đã xóa từ những log này.

## Sửa tăng dung lượng sau cập nhật

- Sửa xung đột nâng cấp schema khi lịch sử và cây thư mục mở cache đồng thời. Kiểm tra log sau cập nhật giữ nguyên file lịch sử; phân trang ổn định sau khi chỉ mục tạm được tạo lại.

- Bản trước giữ nhiều bản sao toàn bộ SQLite sau mỗi cập nhật. Nay mặc định giữ **một backup thành công gần nhất**, dọn backup cũ và installer đã dùng sau khi xác minh cập nhật. Khởi động GUI mới cũng xử lý các phiên cập nhật thành công do bản cũ để lại.
- Bảo vệ lượt đang cập nhật, lỗi/chưa khôi phục xong, metadata không rõ và junction/symlink. Giữ nhật ký phiên; không tự xóa lịch sử đang dùng. Có khóa chống bảo trì/cập nhật đồng thời.
- **Hệ thống → Dung lượng app**: phân loại lịch sử, chuẩn hóa, cache so sánh, backup, installer, log, nguồn CSV và file tạm; hiển thị chỗ trống và trang trống SQLite. Dọn có xem trước/xác nhận; bỏ cả backup gần nhất phải chọn riêng, mặc định không bật.
- **Thu gọn cache SQLite** trả trang trống về ổ mà giữ kết quả và lịch sử, chỉ chạy khi đủ chỗ tạm. Cache mới hỗ trợ thu hồi tăng dần có giới hạn thời gian; cache cũ không tự chạy VACUUM lớn.
- Kiểm tra chỗ trống trước quét, tạo cache/chuẩn hóa và cập nhật; dự phòng mặc định **1.024 MB**, cấu hình được. Cập nhật kiểm tra lại trước sao lưu, gồm database/WAL, bộ cài/runtime và dự phòng. Công việc SQL dài kiểm tra định kỳ, dừng với thông báo thiếu dung lượng thay vì chờ ổ đầy. Ước tính không phải cam kết chính xác dung lượng đỉnh; tác vụ khác vẫn có thể dùng ổ đồng thời.

CLI tương đương: `storage report`, `storage cleanup-updates --preview`, `storage cleanup-updates --yes`, `storage compact-cache --yes`. Mặc định giữ một backup; `--keep 0` chỉ dùng khi chủ động chấp nhận mất backup cập nhật hoàn tất. Chính sách lịch sử đã cấu hình không bị tự đổi.

## Cài / cập nhật

Tải **installer.exe**, đối chiếu **SHA256SUMS.txt**. Windows x64, UAC Admin, runtime riêng; không cài pip/Python hệ thống. Đóng app, cài đúng thư mục bản hiện tại để giữ dữ liệu. Nếu bản cũ báo không đủ chỗ để tự sao lưu cập nhật, dọn cache/backup được duyệt hoặc dùng bộ cài thủ công tại vị trí cũ; không xóa history.sqlite hay các file WAL/SHM bằng tay.

Bộ cài chưa ký số. Windows có thể hiện Unknown publisher/SmartScreen; không cần tắt bảo vệ. Kiểm thử tự động dùng fixture riêng, không quét/dọn ổ thật hay sửa Task/Service đang dùng. Báo cáo cài/cập nhật và checksum kèm Release.

## Kiểm thử

- 417 bài kiểm thử tự động đạt; giao diện kiểm tra ở mức phóng đại 125% và 150%.
- Bộ cài thực tế: cài mới, CLI/GUI, chuẩn hóa, phân trang toàn tập, Chung/Riêng, cập nhật tại chỗ và gỡ giữ dữ liệu đều đạt.
- Nâng cấp từ bộ cài cũ bằng coordinator cũ; chuyển SQLite cũ sang log nén, xác minh rồi thu hồi nguồn; mở lại và so sánh sau khi bỏ chỉ mục tạm đều đạt.
- `release-test-results.json`, `inplace-update-test-results.json`, `frozen-cli-test-results.json` ghi các kiểm tra bằng fixture riêng, không chứa lịch sử cá nhân.

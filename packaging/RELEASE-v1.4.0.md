# Disk Growth Monitor 1.4.0

## Giao diện và dọn dẹp có duyệt

- Icon folder/file chung, cột trạng thái bằng ký hiệu, màu theo cả hàng và viền chọn không che ý nghĩa. Tiêu đề cây/chi tiết đồng bộ và cao hơn; chú giải bật/tắt, tooltip có ý nghĩa cho ký hiệu.
- Dọn dẹp có catalog, giải thích “là gì / khi nào nên dọn / giữ gì / tác động”, vùng được bảo vệ và nguồn tham khảo. Mặc định không tick file nào. Phân biệt xóa file, CLI xóa toàn cache và công cụ Windows; không biến dung lượng chưa ước lượng thành 0 B.
- Đề xuất JSONL gzip chỉ có metadata, checksum, hạn một giờ; chỉ mục tạm và phân trang 20–1.000 mục, sort/filter toàn tập. Mặc định tổng archive 64 MB và chỉ mục 128 MB, tóm tắt tối đa 200 đề xuất/30 ngày; có kiểm tra chỗ trống. Không lưu bản sao nội dung file. Tác vụ phải duyệt đúng và kiểm tra lại rule/đường dẫn/identity/mtime/size; chặn dùng lại và lựa chọn đã thay đổi sau khi hiển thị.
- Thêm uv cache, DirectX shader, WER ReportArchive người dùng. Bảo vệ project/.venv/.git, model AI, SQLite/WAL/SHM, dữ liệu trình duyệt và thành phần Windows. NVIDIA cần xác nhận update xong; NuGet ghi rõ dọn toàn cache. Không thêm registry cleaner hoặc xóa tay DriverStore/WinSxS/Installer/Package Cache.
- Kiểm tra thủ công file lớn/trùng SHA-256/folder rỗng, chỉ folder chọn riêng, không tự xóa. Giới hạn mặc định 60 giây / 100.000 file / 256 MB hash / 1.000 dòng; báo giới hạn, nhận diện hardlink và tham chiếu. Tìm trùng đọc nội dung file được chọn để hash, GUI hỏi trước.

## Các tab còn lại

- Hệ thống: Cập nhật app, Công cụ quét, Dung lượng app, Chẩn đoán Windows. Footprint của app được đo riêng với cây quét; backup nằm trong tổng, không cộng lần nữa.
- Lịch chạy: tên/tạo/sửa, trường lặp theo ngữ cảnh, giờ/thứ dễ đọc, lần cuối/kế tiếp và kết quả; lịch và Task/Service là hai trạng thái độc lập. Lịch chỉ quét/lập đề xuất, không tự xóa.
- Nhật ký: [LEVEL], màu cả hàng, lọc công việc/từ khóa/mức, traceback và xem trước gói hỗ trợ đã che thông tin. Đọc tối đa 500 KB cuối và giữ 4.000 dòng trong RAM. Che tự động không bảo đảm nhận ra mọi bí mật; cần tự kiểm tra trước chia sẻ. Không tự tải lên AI.
- Lịch sử: ưu tiên số thứ tự, chuẩn hóa, độ bao phủ và phương thức. Chuẩn hóa không có nghĩa đọc đủ; không gọi lượt chuẩn ngay khi nhập là chưa chuẩn hóa. Phần chưa rõ/khác phạm vi không gán 0 B hay coi là file đã xóa.
- GUI/CLI dùng chung API; `cleanup plan/show` mặc định tóm tắt, `cleanup page` phân trang và `--full` cho người cần xuất toàn bộ metadata. Thêm `cleanup inspect-folder/protected/prune`, `schedule edit`, `log-events`.

## Cài và cập nhật

Tải **installer.exe** và đối chiếu **SHA256SUMS.txt**. Bộ cài và GUI cần UAC Admin. Cài đúng thư mục phiên bản hiện tại để giữ data/log; có cập nhật tại chỗ trong app. Bản cũ dùng SQLite cần chủ động chuyển và xác minh trong Hệ thống → Dung lượng app; cài đè không âm thầm xóa lịch sử.

Runtime riêng, không cài Python/pip global hoặc đổi PATH. Không đóng gói dữ liệu cá nhân, WizTree thương mại, code/rule BleachBit/Winapp2/BCUninstaller/Czkawka. Không quét, xóa hay đăng ký lịch tự động khi cài. Gỡ giữ lịch sử/log/tool tải riêng. Binary chưa ký số; không cần tắt bảo vệ Windows.

## Kết quả kiểm thử

- 452 bài kiểm thử tự động đạt; thêm 50 bài giao diện ở DPI 125% và 50 bài ở DPI 150% đều đạt.
- Bộ cài cuối đạt 13 kiểm tra cài mới/frozen GUI/CLI và 8 kiểm tra cập nhật tại chỗ, gồm nâng cấp thực tế từ 1.1.1 và 1.3.1, giữ lịch sử và chuyển SQLite sang JSONL có xác minh trên dữ liệu thử.
- Kiểm tra checksum sai bị từ chối, phân trang/sắp xếp toàn tập, khác phạm vi không bị coi là đã xóa, chọn đúng file được duyệt và gỡ app vẫn giữ lịch sử.

Kiểm thử dùng fixture riêng trên D:; chỉ xóa một file cache thử 1.500 byte, không dùng cache/hệ thống C: để thử xóa hoặc sửa Task/Service đang dùng. Báo cáo tổng hợp và kết quả cài/cập nhật đúng checksum bộ cài được đính kèm Release; không đính kèm lịch sử ổ đĩa cá nhân hoặc JUnit gốc.

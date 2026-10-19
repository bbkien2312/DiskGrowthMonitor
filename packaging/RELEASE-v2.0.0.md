# Disk Growth Monitor 2.0.0 — Parquet + DuckDB

Bản chuyển lõi lưu trữ/so sánh sang Parquet ZSTD + DuckDB RAM, giữ nguyên GUI đầy đủ đã duyệt và CLI tương đương.

- Giữ Chung/Riêng chi tiết, bốn tile chia Chung/Riêng, màu theo hàng, icon folder/file, tooltip, chú giải, menu chuột phải, phím tắt, cây hai chế độ và lịch sử chi tiết.
- Snapshot chỉ chứa metadata, không nội dung file. Không dựng lại database hàng triệu file từ JSONL khi mở app. Sort/filter toàn tập trước phân trang 20/50/100/200/500/1000; tải thêm dùng lại phiên RAM.
- Mặc định Allocated / GiB; Logical là số đo phụ, có thể vượt Used do hardlink/nén/sparse. Tổng chỉ cộng file. Đối soát dùng số đo Windows đã lưu cùng mốc; giữ null khi thiếu, không ép số hay gán phần dư cho file ≤10 MB.
- Sửa thanh dưới bị cắt do chiều cao cũ 42px: tính chiều cao theo font/DPI, trạng thái và tiến độ luôn hiện đủ. Nút Chi tiết thời gian / kết quả mở/thu gọn vùng cuộn riêng.
- Loading ghi rõ thu thập, đọc/chuẩn hóa, hardlink, tổng hợp folder, ghi/kiểm chứng Parquet, so sánh và nạp cây/bảng. Thời gian từng công đoạn được cập nhật sau khi xong; kết quả tăng/giảm/mới/mất/không đổi/không quét/chưa xác định lấy trên toàn cặp mốc, không chỉ trang đã tải.
- Luồng Quét ngay tự tiếp tục so sánh/nạp bảng; tiến độ chỉ đạt 100% khi đã nạp kết quả. So sánh thủ công có tiến độ riêng. Không dùng tiến độ ước lượng thời gian để giả vờ số mục đã xử lý.
- Job JSON lưu thời gian và điểm tiếp tục; export đã hoàn tất kiểm checksum trước khi dùng lại, quét dở phải quét lại. Dừng/đóng chờ worker giải phóng an toàn. Session/spill không còn dùng được dọn, không xóa snapshot.
- Mốc thuộc profile nguồn/preview vẫn chọn và mở được, không đổi profile hoặc nguồn gốc dữ liệu.
- Giữ Hệ thống, Dọn dẹp có duyệt, Lịch chạy, Nhật ký, UAC và cập nhật tại chỗ. Không cài tool quét ngoài hoặc Task/Service khi cài app.

## Cài / nâng cấp

Tải `installer.exe` và đối chiếu `SHA256SUMS.txt`. Runtime Python/Qt/DuckDB đóng gói riêng trong `.venv`, không cài pip/Python hệ thống. GUI và bộ cài yêu cầu UAC. Tên hiển thị mang hậu tố **Parquet + DuckDB**; AppId giữ nguyên để cập nhật đúng app cũ.

Kho mới mặc định `data/columnar`; kho preview đã được duyệt trong cùng app-home được dùng tại chỗ, không nhân đôi. Lịch sử/config/log cũ được giữ nguyên khi nâng cấp. Lịch sử JSONL cũ cần bấm **Lịch sử → Nhập mốc JSONL cũ…** một lần; SQLite cũ có công cụ chuyển sang JSONL trước. Không tự xóa hoặc chuyển lịch sử cũ khi khởi động; các ID ở hai kho là độc lập.

WizTree là phần mềm riêng, không đóng gói trong installer. Native/gdu/WinDirStat dùng schema chung nhưng trường nguồn thiếu vẫn ghi chưa rõ; không khẳng định độ tương đương tuyệt đối của mọi engine. Giới hạn DuckDB là ngân sách bộ máy, không phải trần RSS cứng của toàn tiến trình GUI.

Không phát hành dữ liệu quét, log cá nhân, ảnh mốc thật, venv phát triển hoặc cache thử nghiệm cùng source/release.

## Kiểm chứng bộ cài phát hành

- 570 bài hồi quy đạt; 15 bài GUI/lõi tại DPI 125% và 15 bài tại DPI 150% đạt.
- 8 kiểm tra cài mới/frozen CLI/GUI/uninstall và 3 kiểm tra cập nhật đạt trên đúng SHA-256 của installer phát hành; bao gồm nâng cấp thật từ 1.5.0, giữ nguồn JSONL, nhập Parquet một lần, đối chiếu lịch sử và chặn installer sai checksum.
- Các lượt quét/xóa kiểm thử chỉ dùng fixture riêng trên D:, không quét/dọn C: hoặc thay Task/Service thật.
- `test-summary.json` chỉ chứa kết quả tổng hợp, không chứa đường dẫn cá nhân hoặc dữ liệu lịch sử quét.

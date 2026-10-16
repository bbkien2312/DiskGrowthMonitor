# Disk Growth Monitor 1.1.1 — phát hành 16/10/2026

Windows x64, installer và CLI/GUI dùng runtime riêng. Không bao gồm cấu hình cá nhân, lịch sử, log, cache, WizTree hay công cụ tải thêm.

## Nội dung

- Lịch sử giải thích rõ trạng thái hoàn tất nhưng có giới hạn: engine ngoài không chứng minh được độ bao phủ; native báo số vùng không đọc được. Trạng thái thận trọng không khẳng định chắc chắn có dữ liệu bị bỏ sót.
- Mỗi scan, comparison và benchmark lưu kích thước file database/log trước, sau và chênh lệch. Chỉ stat database của app (SQLite cùng WAL/SHM) và app/audit log đã xoay vòng; không quét nội dung các ổ đĩa, và đây không phải số cluster NTFS đã cấp phát.
- Lịch sử GUI hiển thị Δ data/log với tooltip trước/sau; CLI và kết quả benchmark chứa cùng phép đo. Chênh lệch là ước lượng footprint: SQLite có thể tái sử dụng trang, cache có thể trim, log có thể xoay vòng. Phần metadata tự ghi không nằm trong mẫu sau.
- Bao gồm các thay đổi đã duyệt: mở file/thư mục bằng double-click, tránh chuẩn hóa lại snapshot cùng provenance, và xóa một/nhiều/toàn bộ lịch sử cùng cache phụ thuộc.
- Kiểm chứng: 210 test tự động; smoke test bộ cài frozen trên fixture riêng bao gồm cài, scan/compare, cập nhật an toàn và gỡ cài đặt giữ data/log.

Bộ cài chưa ký mã. Tải `installer.exe`, `SHA256SUMS.txt` và báo cáo smoke test từ GitHub Release chính thức.

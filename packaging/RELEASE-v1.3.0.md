# Disk Growth Monitor 1.3.0

## Thay đổi chính

- **Chung = có ở hai mốc; Riêng = chỉ một mốc.** Thêm/mất thật trong Riêng vẫn tính vào tổng. Không quét hoặc chưa rõ không bị coi là đã xóa; trạng thái nêu rõ Cũ/Mới.
- Trạng thái ở cột đầu, có ký hiệu/màu; đường dẫn không còn gạch ngang xám, thêm mới xanh. Cột Cũ/Mới gọn, bộ lọc trạng thái/mốc/lý do áp dụng toàn bộ dữ liệu trước tải trang.
- Đồng bộ dữ liệu đã chuẩn hóa của native/WizTree/WinDirStat/gdu; giữ bản gốc, không đo file hiện tại thay cho lịch sử. Sửa tổng thư mục/nhánh thưa, đổi file↔folder và di chuyển, không cộng trùng byte.
- Chỉ mục Chung/Riêng giúp tải trang nhỏ không phải duyệt nhóm lớn khác. Giữ tải 20/50/100/200/500/1000 và sắp xếp toàn phạm vi/nhánh; cache GUI có giới hạn.
- Cấu hình gọn, cây/chi tiết rộng hơn; dung lượng số nguyên lớn/đậm, ghi chú tận dụng chiều ngang, đủ chỗ giữ một dòng, tối đa hai dòng. Tooltip chỉ hiện chữ bị cắt; chuột phải **Xem dữ liệu hai mốc** để đọc byte gốc/lý do.
- Icon riêng cho taskbar, cửa sổ, khay hệ thống, shortcut và installer. Nút Quét ngay nổi bật, các nút chức năng đậm và các vùng có màu nhấn nhẹ.
- Cập nhật giữ vị trí cài đặt, lịch sử quét/so sánh/benchmark, cấu hình và log; runtime riêng, không cài pip/Python toàn hệ thống. Bộ cài không chứa WizTree hoặc dữ liệu cá nhân.

## Tải và cập nhật

Tải **installer.exe** và đối chiếu **SHA256SUMS.txt** bên dưới. Windows x64, không cần Python hệ thống. Bộ cài và GUI yêu cầu UAC Admin. Đóng app trước khi nâng cấp thủ công, chọn đúng thư mục bản cài hiện có; hoặc dùng nút **Cập nhật** trong app. Lịch sử và cấu hình được giữ lại.

Cache so sánh cũ sẽ được lập lại theo cách chia mới khi mở cặp mốc, từ dữ liệu đã lưu; không cần quét lại ổ. Chuẩn hóa không thể phục hồi số liệu chưa từng được thu thập; vùng thiếu bằng chứng vẫn ghi Chưa rõ/Không quét. Size theo đường dẫn không đồng nghĩa dung lượng Windows đã dùng.

## Kiểm chứng và lưu ý

372 bài kiểm thử; DPI 100/125/150%, bốn công cụ trên fixture riêng ở D:. Báo cáo kèm theo xác nhận cài mới, cập nhật tại chỗ/qua runtime quản lý/từ bộ cài cũ, giữ lịch sử, chặn checksum sai và gỡ giữ dữ liệu. Không thay đổi Task/Service thật hay bản app đang dùng khi kiểm thử.

Bộ cài **chưa ký mã**. Windows có thể hiện Unknown publisher/SmartScreen; không cần tắt bảo vệ, hãy kiểm tra nguồn tải và SHA-256. Đã thử trực tiếp Windows 11 x64; chưa có máy Windows 10 riêng để kiểm thử.

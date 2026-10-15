# Disk Growth Monitor 1.1.0 — phát hành 15/10/2026

## Thay đổi

- Chọn 20/50/100/200/500/1000 mục mỗi lượt ở bảng chi tiết. Đổi lựa chọn không mất dòng đang xem; tải tiếp nối đúng thứ hạng của toàn tập kết quả.
- Sắp xếp tên/dung lượng/ngày/mức thay đổi trên toàn dữ liệu thỏa phạm vi và bộ lọc, không chỉ các dòng đã tải. Đổi cột/chiều giữ số lượng đã mở và lấy lại top/bottom theo thứ tự mới.
- Chọn folder trên cây thì chỉ sắp xếp folder đó, gồm nhánh con nếu bật tùy chọn. Xem toàn bộ để quay lại phạm vi tổng; tổng quan không bị giới hạn theo trang.
- Chỉ mục tên nhánh độc lập; không bắt lập thứ tự tên toàn ổ khi chỉ xem nhánh. Cache RAM 400–2000 dòng; COUNT tái sử dụng khi đổi cột/chiều. Dừng tải và thử lại an toàn.
- Cây đủ ba cột, chiều ngang theo font/DPI; đường dẫn có ellipsis/tooltip, Mức thay đổi ở cột 2. Lọc nhiều trạng thái trước phân trang, giữ tổng quan toàn phạm vi.
- Thẻ dung lượng màu nhẹ, nhãn CHÊNH LỆCH DUNG LƯỢNG QUÉT, tùy chọn quét mở mặc định, icon hướng dẫn chuẩn.
- Realtime chỉ cập nhật trạng thái runner, không tự quét file. Mở Windows management, hiển thị ownership và không sửa runner của bản app khác.
- Kiểm tra/tải/cài bản app chính thức qua GUI/CLI: stable, chống downgrade, SHA-256, hủy tải; tạm dừng/khôi phục runner của bản cài, sao lưu SQLite/config và giữ portable mode. Source không bị ghi đè. Session/log/backup giữ để phục hồi; binary chưa ký mã.

## Kiểm chứng local

189 tests; UI DPI 125%/150% đạt. Mạng lỗi/UAC/runner mutations được kiểm tra bằng mock, không thay đổi runner thật. Coordinator frozen đã thử thay runtime cùng phiên bản (không publish Release giả), giữ backup/history/portable và chặn checksum sai. Benchmark vòng trước 200.000 bản ghi giả lập: thêm 100 ~0,038 s, thêm 1000 ~0,050 s, sắp xếp lại giữ 1120 ~0,081 s. Lập tên nhánh lần đầu ~1,075 s. Peak RSS cả tiến trình thử SQL/GUI ~135,5 MB, không reset OS cache. Không phải cam kết thời gian toàn ổ.

Không quét ổ C hoặc xóa dữ liệu người dùng trong thử nghiệm. Không đăng ký/gỡ Task/Service thật. Dữ liệu/bộ cài 1.0.0 đã phát hành không bị thay thế.

Mã nguồn và bộ cài Windows x64 được phát hành tại GitHub Release `v1.1.0`, kèm SHA-256 và báo cáo kiểm thử. Bộ cài chưa ký mã; WizTree thương mại không được đóng gói. Quy trình cài/giữ data/log và runtime riêng tương thích với 1.0.0.

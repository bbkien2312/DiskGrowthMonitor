# Disk Growth Monitor 1.1.0 — phát hành 15/10/2026

- Dropdown tải 20/50/100/200/500/1000 mục, lưu riêng tùy chọn GUI. Đổi kích thước lượt tải không làm mất dòng; 20 + 100 = 120 và 20 + 1000 = 1020 đúng thứ hạng toàn kết quả.
- Sắp xếp toàn phạm vi/bộ lọc hiện tại; đổi cột/chiều giữ số lượng đã mở, thay bằng top/bottom mới. Tên A–Z trước, số/ngày giảm dần trước. Cây folder giới hạn đúng nhánh và tùy chọn gồm thư mục con; tổng quan toàn snapshot giữ nguyên.
- Tên nhánh lập chỉ mục cục bộ, độc lập với tên toàn phạm vi. COUNT không lặp khi chỉ đổi cách sắp xếp. Hủy lập tên/nhánh rollback an toàn và thử lại được; trang cũ không chen vào kết quả mới sau khi hủy/đổi phạm vi.
- Cache GUI giới hạn 400–2000 dòng, phân trang có kích thước khác nhau và đọc lại trang cũ không làm lệch cursor. Cursor JSON của bản cũ cần lấy lại trang đầu; snapshot không phải quét lại.
- Cây mặc định đủ tên System Volume Information và luôn giữ ba cột trong viewport. Chi tiết đưa Mức thay đổi lên cột 2, đường dẫn rút gọn giữa bằng … nhưng tooltip/thao tác giữ nguyên đường dẫn đầy đủ. Lưu bố cục mới riêng, kéo cột và reset có kiểm thử.
- Bộ lọc trạng thái nhiều lựa chọn, chọn tất cả/bỏ chọn/chỉ có thay đổi. SQL lọc toàn dữ liệu trước sort/paging, tôn trọng phạm vi folder; tổng quan toàn snapshot giữ nguyên.
- Thẻ dung lượng nền nhẹ, chữ số lớn, nhãn CHÊNH LỆCH DUNG LƯỢNG QUÉT và cảnh báo partial. Tùy chọn quét mở mặc định, icon hướng dẫn dùng icon hệ thống thay ký tự dễ lỗi font.
- Realtime trạng thái Task/Service 3 giây (15 giây khi ẩn), không quét lại file, không chồng tác vụ. Mở Task Scheduler/Services; nhận diện runner của bản app khác và chặn sửa nhầm.
- Cập nhật app từ Release chính thức: stable-only, chống downgrade, kiểm tra SHA-256/kích thước, tải có hủy, cài khi xác nhận. Coordinator ngoài runtime tạm dừng/khôi phục runner thuộc bản cài, giữ đăng ký và startup type, SQLite backup, giữ portable mode. Source chỉ cài sang thư mục trống khác; không pip global/git pull tự động. Có journal/log và CLI khôi phục nếu lỗi; chưa ký mã.
- 189 tests, thêm kiểm tra UI DPI 125%/150%. Runner mutations/UAC/mạng lỗi được kiểm tra bằng mock, không thay đổi runner đang dùng của máy. Coordinator frozen đã thử thay runtime cùng phiên bản, backup/giữ dữ liệu/portable và chặn checksum sai; chưa giả mạo/publish một Release mới để thử nâng phiên bản qua mạng. Đo 200.000 bản ghi SQL ở vòng trước: GUI thêm 100 ~0,038 s; thêm 1000 ~0,050 s; sắp xếp lại giữ 1120 ~0,081 s; peak RSS SQL/GUI ~135,5 MB. Không phải cam kết tốc độ dữ liệu thật.
- Đã phát hành mã nguồn public và bộ cài Windows x64 tại GitHub Release `v1.1.0`; installer có kèm checksum SHA-256 và báo cáo smoke test.

## Bản 1.0.0 — 15/10/2026

## Bản phát hành EXE đầu tiên

- Bộ cài Windows x64 `installer.exe`, CLI `dgm.exe` và GUI `DiskGrowthMonitor.exe` dùng chung Python/Qt riêng trong `.venv`. Không cần Python global; không sửa System PATH.
- Cài theo tài khoản, chọn ổ/thư mục; ưu tiên D nếu có, không ghi đè thư mục mã nguồn. Không tự quét/xóa hay đăng ký Task/Service. Giữ data/log khi nâng cấp/gỡ; chặn gỡ khi còn app/quét/nền đang đăng ký.
- Hỗ trợ bản frozen cho UAC, Task Scheduler và service wrapper; không trỏ tới Python của bản nguồn. Task/service không được cài thật trong kiểm thử.
- Không đóng gói cấu hình/lịch sử/log của máy phát triển, WizTree thương mại hay engine đã tải riêng. Có notices/license runtime và SHA256SUMS.txt. Binary chưa ký mã; chỉ tải từ Release chính thức và đối chiếu hash.
- Mã nguồn public: https://github.com/bbkien2312/DiskGrowthMonitor. Release đầu: v1.0.0. Các mốc 0.x dưới đây là lịch sử phát triển nội bộ.
- Kiểm chứng: **130 tests**, GUI/CLI frozen với runtime riêng; cài sạch, hai lượt quét fixture trên D và tăng 2.000.000 byte, so sánh, nâng cấp giữ cấu hình/lịch sử và gỡ chỉ bỏ binary. Kiểm thử portable không tạo shortcut/mục Installed apps; Task bản nguồn hiện có được giữ nguyên. Service/Task mutations và thao tác dọn hệ thống thật vẫn chỉ được kiểm tra bằng mock.
- Chặn DLL ICU trùng tên từ công cụ ngoài môi trường build; Qt sử dụng thư viện ICU hệ điều hành. Bổ sung văn bản LGPL/GPL Qt upstream, DLL dùng chung có thể thay thế. Windows 11 x64 đã thử trực tiếp; Windows 10 là nền tảng nhắm tới, chưa có máy riêng để thử.

## Lịch sử phát triển trước 1.0.0

Các bản 0.x là bản thử nghiệm nội bộ, chưa phát hành installer public:

- 0.2.1: UAC theo tác vụ, bảo toàn lựa chọn chưa lưu, IPC có nonce/hash/ACL, hủy có kiểm soát và log worker về GUI/CLI. Không cưỡng bức tắt DISM.
- 0.2.0: giao diện trắng ngà, lịch sử/mốc Cũ–Mới, cây độc lập, cache diff SQLite, phân trang 20 mục và LRU giới hạn bộ nhớ. Tổng quan vẫn tính toàn phạm vi.
- 0.1.1: dừng/quét lại, điều khiển Task/Service rõ ràng, kéo cột độc lập, kiểm tra phiên bản từ nguồn chính thức, tooltip/hướng dẫn ẩn hiện.
- 0.1.0: CLI/GUI dùng chung core; native/WizTree/WinDirStat/gdu, snapshot/diff, ngưỡng file lớn, Created/Modified, context menu, dọn có duyệt, lịch chạy và câu hỏi AI copy/mở web.

Lịch sử quét, số liệu ổ đĩa và báo cáo nội bộ của máy phát triển không được phân phối trong repo hoặc installer.

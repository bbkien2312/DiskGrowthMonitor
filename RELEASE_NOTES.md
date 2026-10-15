# Disk Growth Monitor 1.0.0 — 15/10/2026

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

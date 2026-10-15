# Disk Growth Monitor 1.0.0

Bản phát hành bộ cài Windows x64 đầu tiên. CLI và GUI tiếng Việt dùng chung dữ liệu và core.

## Tải và cài

1. Tải `installer.exe` và `SHA256SUMS.txt` bên dưới.
2. Đối chiếu SHA-256, chạy bộ cài và chọn thư mục trống (mặc định ưu tiên ổ D nếu có).
3. Mở `DiskGrowthMonitor.exe`; CLI là `dgm.exe --help` trong thư mục cài.

Python/Qt và các thư viện được đóng gói riêng trong `.venv`. Không cần Python/pip global, không sửa System PATH. Không tự quét/xóa, không tự cài lịch hoặc service.

WizTree thương mại **không kèm theo**: chọn executable đang cài trong tab Hệ thống. Engine OSS chỉ tải thêm khi người dùng yêu cầu.

## Chức năng

- Snapshot file/folder, phát hiện tăng/giảm/thêm/mất/đổi tên khi đủ bằng chứng; Created/Modified.
- Tổng quan toàn phạm vi; bảng chi tiết tải 20 mục/lượt, sắp xếp toàn dữ liệu, cây thư mục và lịch sử so sánh.
- Quét/dừng/quét lại; log màu DEBUG/INFO/WARNING/ERROR; đề xuất dọn luôn cần người dùng duyệt.
- UAC theo tác vụ; giữ GUI quyền thường. Lịch giờ/ngày/tuần/tháng/năm, Task/Service đăng ký riêng theo yêu cầu.
- Không xóa tay WinSxS/DriverStore, không tự xóa theo ý kiến AI.

## Kiểm chứng và lưu ý

130 tests đạt. Đã kiểm thử bộ cài thật trên Windows 11 x64: cài mới, cả hai EXE, hai lần quét dữ liệu mẫu trên D, so sánh, nâng cấp giữ dữ liệu và gỡ vẫn giữ lịch sử/log. Windows 10 chưa thử trên máy riêng. Không quét ổ C, dọn dữ liệu người dùng hoặc đăng ký/gỡ Task/Service thật trong vòng kiểm thử phát hành.

Bản cài mới không tự nhập lịch sử của bản nguồn. Dữ liệu cũ vẫn nằm nguyên thư mục cũ. Trước khi gỡ, đóng app và gỡ bộ chạy nền **thuộc bản cài đó** trong app. Uninstaller giữ dữ liệu/log/công cụ tải thêm để người dùng tự duyệt.

**Chưa ký mã**: Windows có thể hiện Unknown publisher/SmartScreen. Kiểm tra nguồn tải và SHA-256; không cần tắt cơ chế bảo vệ. Thông báo giấy phép đi kèm runtime. Mã nguồn public không đồng nghĩa tự động cấp một giấy phép sử dụng mới cho mã ứng dụng.

Portable mode: `installer.exe /PORTABLE=1` (không tạo shortcut hoặc mục Installed apps).

Xem README và THIRD_PARTY.md trong repo để build lại và xem giới hạn hiện tại.

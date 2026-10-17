"""App-owned, reviewed explanations; no external cleaner scripts are loaded."""
GROUPS = ('Cache / chẩn đoán', 'Cần điều kiện', 'Windows')
PROTECTED = [
    ('WinSxS', 'Kho thành phần Windows, có hardlink', 'Không xóa file tay; phân tích DISM rồi bảo trì được Windows hỗ trợ.'),
    ('DriverStore', 'Driver đang dùng và nguồn phục hồi', 'Rà từng gói bằng PnPUtil; không force/uninstall hàng loạt.'),
    ('Windows Installer / Package Cache', 'Nguồn repair, update và uninstall', 'Không coi là cache tạm; dùng bộ cài hoặc trình gỡ tương ứng.'),
    ('$MFT / System Volume Information', 'Metadata NTFS và dữ liệu hệ thống', 'Chỉ dùng tính năng quản lý Windows phù hợp.'),
    ('.venv / .git / project / model AI', 'Môi trường, mã nguồn và dữ liệu công việc', 'Giữ mặc định; chỉ quản lý thủ công trong phạm vi đã chọn.'),
    ('Registry / SQLite WAL, SHM', 'Dữ liệu hoạt động hoặc giao dịch chưa checkpoint', 'Không đề xuất xóa riêng; để app sở hữu quản lý.'),
    ('Chrome/Edge User Data', 'Profile, mật khẩu, phiên đăng nhập', 'Không đưa vào dọn tự động; không quét profile để tìm cache.'),
]
REFERENCES = [
    ('BleachBit / CleanerML', 'Xem trước, mô tả từng nhóm', 'https://github.com/bleachbit/bleachbit', 'GPL-3.0+; không nạp action/script ngoài.'),
    ('Winapp2', 'Catalog theo ứng dụng, điều kiện nhận diện', 'https://github.com/MoscaDotTo/Winapp2', 'CC-BY-SA-4.0; không sao chép cả catalog.'),
    ('BCUninstaller', 'Bằng chứng, mức tin cậy định tính', 'https://github.com/BCUninstaller/Bulk-Crap-Uninstaller', 'Không thêm registry cleaner / gỡ app tự động.'),
    ('Czkawka', 'File trùng, bảo vệ thư mục tham chiếu', 'https://github.com/qarmin/czkawka', 'Tùy chọn ngoài; không tự tải hoặc chạy toàn C:.'),
]
EXPLANATIONS = {
    'user-temp': ('File tạm do ứng dụng tạo.', 'File sửa lần cuối đủ tuổi; app liên quan đã đóng.', 'Giữ file mới/đang dùng; không xóa nguyên thư mục Temp.'),
    'windows-temp': ('File tạm của Windows và bộ cài.', 'Cần Admin; không có cập nhật/cài đặt đang chạy.', 'Giữ file mới, đang dùng và file không đọc được.'),
    'crash-dumps': ('Ảnh bộ nhớ phục vụ chẩn đoán ứng dụng bị lỗi.', 'Bạn không còn cần điều tra các lỗi cũ.', 'Mất bằng chứng lỗi; không xóa khi còn cần gửi hỗ trợ.'),
    'pip-cache': ('Gói tải và wheel cache của pip; không phải pip đã cài.', 'Chấp nhận lần cài sau phải tải/build lại.', 'Giữ Python, pip, package đã cài và .venv.'),
    'npm-cache': ('Nội dung tải và log cache npm.', 'Không có npm install/build đang chạy; chấp nhận tải lại.', 'Giữ node_modules, package-lock và mã nguồn dự án.'),
    'uv-cache': ('Cache tải/build của uv.', 'Dừng uv/build; chấp nhận tải hoặc build lại.', 'Giữ .venv và runtime; không xóa file trong môi trường đang dùng.'),
    'nuget-cache': ('Global packages cache phục vụ build .NET.', 'Đóng dotnet/MSBuild/Visual Studio; có mạng tải lại.', 'Lệnh chính thức xóa TOÀN BỘ global-packages, không lọc theo tuổi hay từng file.'),
    'gradle-cache': ('Dependency cache và log daemon Gradle.', 'Dừng Gradle và build APK; chấp nhận tải lại.', 'Giữ project, SDK nội bộ và cấu hình; không tự dừng daemon.'),
    'vscode-cache': ('Cache giao diện/extension tải sẵn và log VS Code.', 'Đóng VS Code; không còn cần log chẩn đoán.', 'Giữ User/settings, extension đã cài và Backups khôi phục file chưa lưu.'),
    'nvidia-shader': ('Shader cache NVIDIA; được tạo lại khi chạy đồ họa.', 'Đóng game/app đồ họa; chấp nhận khựng ở lần tạo lại đầu.', 'Giữ driver, DriverStore, thiết lập và NVIDIA App.'),
    'directx-shader': ('Shader cache DirectX của người dùng.', 'Đóng game/app đồ họa; chấp nhận tạo lại shader.', 'Không đụng driver hay thư mục hệ thống.'),
    'wer-reports': ('Báo cáo lỗi Windows đã lưu để chẩn đoán.', 'Không còn cần điều tra lỗi; chỉ file đủ tuổi trong ReportArchive.', 'Giữ hàng đợi báo cáo và file mới/đang dùng.'),
    'nvidia-update': ('Gói tải/cài cập nhật NVIDIA App và driver.', 'Xác nhận update hoàn tất, máy ổn, không cần gói cũ.', 'Chỉ thư mục crd/nvapp được phép; giữ DriverStore, app và thiết lập.'),
    'winsxs': ('Kho thành phần Windows; tổng Size có thể đếm hardlink.', 'Phân tích trước trong Hệ thống; chỉ chạy bảo trì khi bạn duyệt.', 'Không xóa tay; DISM StartComponentCleanup, không ResetBase.'),
    'windows-update': ('Tệp tải/cập nhật do Windows quản lý.', 'Kiểm tra trạng thái cập nhật; tự chọn trong công cụ Windows.', 'Mở Disk Cleanup; không xóa SoftwareDistribution bằng unlink.'),
    'recycle-bin': ('File bạn đã đưa vào Thùng rác ổ C:.', 'Bạn xem lại và chắc không cần khôi phục.', 'Xóa vĩnh viễn toàn Thùng rác C:, không lọc từng file.'),
}


def enrich(base):
    for name, rule in base.items():
        what, when, keep = EXPLANATIONS.get(name, (rule['title'], rule['risk'], 'Chỉ các đường dẫn đã cho phép.'))
        rule.update(what=what, when=when, keep=keep,
                    group='Windows' if name in ('winsxs', 'windows-update', 'recycle-bin') else
                    'Cần điều kiện' if name in ('nuget-cache', 'gradle-cache', 'nvidia-update') else 'Cache / chẩn đoán',
                    method='Công cụ Windows • tự chọn' if rule.get('tool') == 'cleanmgr' else
                    'Toàn bộ cache • CLI chính thức' if rule.get('tool') == 'nuget' else
                    'Bảo trì Windows • không xóa file tay' if rule.get('tool') == 'dism' else
                    'Toàn bộ Thùng rác C:' if rule.get('tool') == 'recycle' else
                    'Xóa vĩnh viễn từng file đủ điều kiện')
    return base

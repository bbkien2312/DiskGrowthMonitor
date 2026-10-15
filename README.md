# Disk Growth Monitor

[Tải bộ cài Windows x64 v1.1.0](https://github.com/bbkien2312/DiskGrowthMonitor/releases/tag/v1.1.0) · [Mã nguồn](https://github.com/bbkien2312/DiskGrowthMonitor)

**Bản mới nhất: 1.1.0.** Xem thay đổi và tải `installer.exe` cùng `SHA256SUMS.txt` trong GitHub Release.

App Windows tiếng Việt để biết file/folder nào làm ổ đĩa tăng dung lượng giữa các lần quét. CLI và GUI gọi cùng core và đọc cùng database/config. Môi trường nằm trong thư mục app trên ổ D; không cài package Python global hoặc sửa System PATH.

## Bộ cài EXE — 1.1.0

Tải `installer.exe` và đối chiếu `SHA256SUMS.txt` trong Release. Windows 10/11 x64, không cần cài Python/pip. Bộ cài cho chọn thư mục, mặc định `D:\DiskGrowthMonitor-App` nếu có ổ D, nếu không dùng thư mục Programs của tài khoản hiện tại. Không cài đè thư mục mã nguồn hoặc thư mục không rỗng chưa có dấu nhận diện bản cài.

- GUI: `DiskGrowthMonitor.exe`; CLI tương đương: `dgm.exe --help`. Các ví dụ `launch-cli.cmd` bên dưới có thể thay bằng `dgm.exe` ở bản cài.
- Hai EXE dùng **một runtime riêng trong `.venv`**, không nhân đôi Python/Qt, không sửa PATH hay đăng ký Python hệ thống. Đây là runtime đóng gói, không phải virtualenv để chạy pip; muốn thay thư viện hãy sửa/kiểm thử/build từ nguồn.
- Installer không kèm cấu hình cá nhân, snapshot, database, log, cache, WizTree hoặc engine OSS đã tải trên máy phát triển. Không tự quét/xóa/cài Task/Service. WizTree chọn từ bản người dùng đã cài; engine OSS chỉ tải khi người dùng yêu cầu.
- Cài theo tài khoản, không cần Admin lúc cài. Tác vụ cần quyền sẽ xin UAC riêng. Shortcut Start menu, Desktop tùy chọn; có trình gỡ cài đặt trong Installed apps. Không tự đóng app đang chạy khi nâng cấp.
- Gỡ cài đặt chặn nếu app/quét đang chạy hoặc còn Task/Service đăng ký; gỡ chúng trong app trước. Chỉ gỡ file đã cài, **giữ `data`, `logs`, công cụ tải thêm** để người dùng tự duyệt. Không xóa đệ quy cả thư mục app.
- Dữ liệu bản nguồn tại `D:\DiskGrowthMonitor\data` vẫn được giữ, không tự chuyển sang bản cài. Bản cài mới tạo lịch sử mới tại thư mục cài đặt.
- Chưa ký mã: Windows có thể hiện Unknown publisher/SmartScreen. Không tắt cơ chế bảo vệ; kiểm tra nguồn tải và SHA-256 trước khi chạy.

Chế độ portable không tạo shortcut hoặc mục Installed apps:

```powershell
.\installer.exe /PORTABLE=1 /DIR="D:\Apps\DiskGrowthMonitor"
```

### Build lại từ nguồn

```powershell
.\install-local.ps1 -Dev
.\build-installer.ps1
# Các lần sau, khi runtime/compiler đã có:
.\build-installer.ps1 -Offline
```

Kết quả `dist/installer.exe`, `dist/SHA256SUMS.txt`. Compiler Inno Setup 6.7.3 được tải từ GitHub chính thức, kiểm tra SHA-256 và chạy portable dưới `.runtime/packaging`; không cài compiler toàn hệ thống. PyInstaller tạo GUI/CLI với DLL dùng chung, giữ thông báo giấy phép tại `.venv/licenses`. Không phân phối WizTree. [Tài liệu compiler portable](https://jrsoftware.org/ishelp/topic_technotes.htm), [PyInstaller multipackage](https://pyinstaller.org/en/stable/spec-files.html#multipackaging-with-one-folder-apps).

## Chạy bản mã nguồn

- Nhấp `launch-gui.cmd` để mở GUI.
- Khi cần Admin (WizTree MFT, kiểm tra hệ thống, dọn hệ thống hoặc quản lý chạy nền), app tự hiện UAC cho đúng tác vụ. Chỉ cần bấm Yes; không phải đóng/mở lại GUI.
- `launch-admin.cmd` vẫn là phương án dự phòng để mở toàn app bằng Administrator, không cần dùng thường xuyên.
- Terminal: `D:\DiskGrowthMonitor\launch-cli.cmd --help`.
- Cài/tạo lại môi trường: `install-local.cmd`; gói dev/build: `install-local.cmd -Dev`.
- Python riêng ở `.runtime`, thư viện ở `.venv`; dữ liệu ở `data`, log ở `logs`.
- Đổi vị trí: chạy installer để tạo lại môi trường và đăng ký lại lịch nếu từng bật.

## Quét và so sánh

```powershell
.\launch-cli.cmd profile list
.\launch-cli.cmd scan --profile daily
.\launch-cli.cmd scan-status
.\launch-cli.cmd scan-stop
.\launch-cli.cmd history
.\launch-cli.cmd entries 1 --minimum-mb 10
.\launch-cli.cmd compare 2 --older 1
.\launch-cli.cmd export 2 D:\Reports\scan-2.csv
```

Exit code: 0 complete; 2 partial/lỗi truy cập; 1 failed; 130 cancelled. Snapshot đầu ghi baseline; lỗi truy cập không được coi là đã xóa. Không cộng folder cha và folder con lần nữa. File dưới ngưỡng vẫn tính vào folder.

Cấu hình mới mặc định chọn WizTree. Cấu hình đang có được giữ nguyên, không tự đổi phạm vi/bảo vệ trình duyệt. Nếu bật bảo vệ Chrome/Edge thì phải chọn native rõ ràng; CLI báo không tương thích, GUI hỏi trước khi đổi lựa chọn. Không âm thầm đổi engine. MFT toàn ổ đọc chỉ mục toàn ổ: lọc kết quả không bảo đảm metadata browser chưa được đọc. WizTree MFT dùng worker Admin của app qua UAC; worker giữ quyền điều khiển tiến trình quét nên vẫn có thể Dừng quét.

### Quyền Admin tự động — 0.2.1

- GUI/CLI mặc định giữ quyền thường. Chỉ tác vụ cần quyền mới mở worker Admin một lần; lựa chọn chưa lưu (phạm vi, engine, MFT, loại trừ, bảo vệ browser, ngưỡng) được chuyển nguyên vẹn, không ghi đè cấu hình.
- Yes: tiếp tục tác vụ, chuyển tiến độ/log về cửa sổ hiện tại. No/đóng UAC: hủy trước khi chạy, không thử lại và không tạo lượt quét giả; CLI trả mã 130. Nếu đã Admin thì chạy thẳng, không hỏi lại.
- Khi đang chờ/chạy Admin, các nút khởi động tác vụ xung đột bị khóa; vẫn xem cây, lịch sử và nhật ký được. **Dừng quét** hoặc **Dừng tác vụ Admin** chỉ gửi yêu cầu cho worker của tác vụ đó. Với DISM, app chờ bước Windows hiện tại kết thúc an toàn rồi không xử lý các mục tiếp theo; không cưỡng bức tắt DISM khi hủy hoặc quá thời gian dự kiến.
- Dọn dẹp vẫn phải xem đề xuất/chọn mục/gõ xác nhận **trước UAC**. Worker kiểm tra dấu vân tay đề xuất và kiểm tra lại file; đề xuất đã thay đổi sau duyệt thì không xóa. Hủy giữa các mục giữ nguyên phần chưa xử lý; kết quả báo cả phần đã xử lý, không tự chạy lại.
- Bộ chạy nền không bật UAC mỗi lần đến lịch. Cài/bật Task/Service bằng UAC một lần để có quyền cần thiết. Nếu thiếu quyền trong phiên chạy nền, app ghi lỗi thay vì mở hộp thoại trong Session 0. Không tự cài/bật dịch vụ.
- UAC dùng Python riêng của app tại `.venv\Scripts\pythonw.exe`; Windows có thể hiển thị tên Python thay vì tên app. Kiểm tra đường dẫn trước khi bấm Yes. Không dùng Python/pip global, không sửa System PATH.
- IPC nằm trong `data/uac`, dùng thư mục riêng giới hạn ACL, khóa chống thay thế thư mục, ticket có nonce/hash/hạn dùng và chỉ cho phép các thao tác cố định (không nhận lệnh shell tùy ý). Tác vụ hoàn tất sẽ dọn các file giao tiếp riêng; không xóa dữ liệu quét. Log worker có tiền tố `[Admin]`, giữ DEBUG/INFO/WARNING/ERROR như GUI/CLI.

Thử riêng UAC mà **không quét ổ, không chạy DISM, không xóa**: tab Hệ thống → **Thử UAC / quyền Admin**, hoặc:

```powershell
.\launch-cli.cmd admin-check
```

Windows xử lý hộp thoại cấp quyền bằng [ShellExecuteEx/runas](https://learn.microsoft.com/en-us/windows/win32/shell/launch); app không bỏ qua hoặc tự bấm Yes.

Profile thư mục khác:

```powershell
.\launch-cli.cmd profile set projects --root D:\Projects --exclude D:\Projects\node_modules --engine native
.\launch-cli.cmd scan --profile projects
```

GUI gọi profile là **Cấu hình quét**. Quét ngay/Quét lại dùng lựa chọn hiện tại, không tự ghi đè cấu hình; Lưu cấu hình để lịch dùng lựa chọn mới. Tùy chọn quét thu gọn, hướng dẫn `>` ẩn/hiện và tooltip giúp phân biệt các nút. Mốc Cũ phải trước Mới, cùng phạm vi; chênh lệch Mới − Cũ, giờ Việt Nam UTC+07. Lượt bị hủy/lỗi/đang chạy không đưa vào danh sách so sánh. Snapshot là quan sát trong khoảng quét, không phải backup hay filesystem snapshot nguyên tử.

### Giao diện và phân trang 1.1

- Số **Lần quét 01, 02…** cố định theo cấu hình, tính cả lượt lỗi/hủy; không đánh số lại sau khi xóa/sắp xếp. ID trong JSON/CLI vẫn là khóa nội bộ, không phải số thứ tự hiển thị.
- WizTree/MFT xuất tổng thư mục/root sẵn có. Khi không phải lọc lại phạm vi, app dùng các tổng này, không tính SUM toàn ổ cho từng thư mục. Với native/phạm vi loại trừ, cộng một lượt từ con lên cha. Hardlink chỉ tính physical một lần.
- Tổng quan dùng toàn snapshot, không chỉ những file >10 MB hoặc 20 dòng. Dung lượng file được quét khác dung lượng volume đã dùng; file nhỏ vẫn nằm trong tổng. Partial/coverage chưa đủ được ghi rõ là chênh số liệu quan sát, không bằng chứng đã xóa file.
- Dropdown **20/50/100/200/500/1000 mục mỗi lượt**, mặc định 20, nhớ trong `data/ui-settings.json`. Đổi số lượng giữ dòng đang xem; lần tải tiếp nối đúng thứ hạng. Ví dụ 20 + 100 = top/bottom 120 toàn tập kết quả, không chỉ sắp xếp 20 dòng cũ. Mở phép so sánh mới dùng số đang chọn.
- Nhấp tiêu đề sắp xếp/lọc toàn kết quả **trong phạm vi và bộ lọc đang áp dụng** trước khi phân trang. Tên/đường dẫn bắt đầu A–Z, dung lượng/ngày bắt đầu giảm dần; nhấp lại đảo chiều. Đổi cột/chiều quay về đầu, lấy lại cùng số lượng đã mở theo thứ tự mới. Cột thay đổi sắp theo số có dấu. Ngưỡng file áp dụng cho `max(Cũ, Mới) > ngưỡng`; folder không bị ngưỡng file loại bỏ.
- Bấm chọn folder trên cây giới hạn bảng trong folder; bật Gồm thư mục con để tính cả nhánh, tắt để chỉ hiện con trực tiếp. Chỉ bung mũi tên không đổi phạm vi. Xem toàn bộ bỏ giới hạn folder; tổng quan không đổi. Tên trong nhánh được lập chỉ mục riêng, không ép lập thứ tự tên cả ổ; các nhánh/tên toàn phạm vi không ghi đè thứ tự của nhau. COUNT được tái sử dụng khi đổi cột/chiều.
- Model giữ cache metadata **400–2000 dòng**, không giữ toàn tập trong RAM. Trang có kích thước khác nhau giữ riêng mốc bắt đầu/con trỏ; đọc lại trang bị loại khỏi RAM không làm đổi con trỏ tải tiếp. Dừng tải hủy cả chuẩn bị chỉ mục/cây; So sánh hoặc Lọc để tải lại. Dừng quét riêng hủy scan/export/import/SQL. Chỉ mục ban đầu/nhánh chưa cache vẫn có thể cần chờ.
- Cây bên trái luôn theo **hai lượt hợp lệ gần nhất của cấu hình, cùng phạm vi**. Nó không theo cặp Cũ/Mới bên phải. Bấm cây chỉ lọc chi tiết, không đổi cặp mốc hoặc tổng quan. Xanh ↑ tăng; đỏ ↓ giảm; xám — chưa biết/mốc đầu. Màu xanh không có nghĩa thay đổi có lợi.
- Tab **Lịch sử** lưu mọi trạng thái quét và tóm tắt so sánh toàn phạm vi: dung lượng, số file/folder tăng/giảm/thêm/mất/khác. Mở so sánh cũ bằng nhấp đúp; tóm tắt còn sau khi chi tiết cache được loại bỏ. Không suy đoán file mới/mất trong vùng chưa xác minh.
- Cache ở `data/comparisons.sqlite`: giữ tối đa hai cặp chi tiết và ba nhánh đã mở/cặp; giữ tóm tắt lịch sử. Ngưỡng cache mềm mặc định 768 MB sẽ giảm về một cặp. SQLite tái sử dụng trang trống; kích thước file vật lý không nhất thiết giảm ngay.
- Lần đầu vẫn cần nhập/index toàn dữ liệu. Đo một triệu bản ghi: index so sánh ~25 s; nhánh lớn lần đầu ~8 s; trang kế tiếp ~0,001 s. Đây là dữ liệu giả lập, không phải cam kết thời gian toàn ổ. Số đo/RAM: `data/performance-v2-results.json`.

CLI dùng cùng chỉ mục và tổng quan:

```powershell
.\launch-cli.cmd overview 28
.\launch-cli.cmd compare 28 --limit 20 --sort new --direction desc
.\launch-cli.cmd compare 28 --root 'C:\Users' --kind file --minimum-mb 10
.\launch-cli.cmd tree --profile daily --root 'C:\'
.\launch-cli.cmd comparison-history
.\launch-cli.cmd comparison-delete PAIR_ID
.\launch-cli.cmd logs --level ERROR --follow
```

Lưu JSON `cursor` từ kết quả rồi truyền `--cursor '{...}'` với đúng bộ lọc/sắp xếp để tải tiếp; không chạy lại phép diff. `--baseline` chọn mốc đầu rõ ràng. `--offset` còn để tương thích nhưng nên dùng cursor cho dữ liệu lớn.

Phục hồi một lượt **đã nhập đầy đủ**, chỉ dừng ở bước tổng hợp: `snapshot-rebuild ID --confirmed`, hoặc nút trong Lịch sử. Tạo lượt mới riêng, giữ lượt gốc; thời điểm dữ liệu là lúc quét gốc, không phải lúc phục hồi. Không quét file và không tự bổ sung dung lượng trống lịch sử. Không xác nhận nếu export/import chưa hoàn tất.

Dừng quét hủy tiến trình do app tạo và bước SQL/thống kê; đợi tác vụ/khóa giải phóng rồi Quét lại. Không đóng WizTree mở riêng. CLI `scan-stop` gửi yêu cầu cùng cơ chế cho lượt CLI/GUI/lịch đang chạy; yêu cầu có token nên không hủy nhầm lượt kế tiếp. Ctrl+C cũng hủy lượt CLI. Khóa phiên bản cũ không hỗ trợ yêu cầu mới: đóng cửa sổ cũ trước khi dùng bản mới, không cưỡng bức giết tiến trình.

Kéo cột độc lập, nhấp đúp mép cột để vừa nội dung, có cuộn ngang. Bố cục và trạng thái hướng dẫn lưu tại `data/ui-settings.json`, không dùng registry. Khôi phục bố cục trả chiều rộng mặc định.

## Dọn có xác nhận

```powershell
.\launch-cli.cmd cleanup rules
.\launch-cli.cmd cleanup plan --rule user-temp --rule vscode-cache
.\launch-cli.cmd cleanup list
.\launch-cli.cmd cleanup show PLAN_ID
.\launch-cli.cmd cleanup select PLAN_ID --path "C:\exact\candidate.tmp"
.\launch-cli.cmd cleanup apply PLAN_ID
```

CLI yêu cầu gõ `XOA PLAN_ID`; GUI có cùng bước xác nhận. Không chạy cleanup thật trong test/demo. Cache được xóa vĩnh viễn khi duyệt; file thay đổi/junction/file khóa được bỏ qua. Plan có hạn 1 giờ. NVIDIA update yêu cầu xác nhận riêng `--update-done`. WinSxS chỉ dùng DISM; Windows Update mở Disk Cleanup; không xóa thủ công DriverStore/Installer. App không tự xóa dựa trên ý kiến AI.

Thao tác tay tương đương menu GUI:

```powershell
.\launch-cli.cmd file action D:\file.txt reveal
.\launch-cli.cmd file action D:\file.txt properties
.\launch-cli.cmd file action D:\file.txt rename --name file-new.txt
.\launch-cli.cmd file action D:\file.txt trash
```

Đưa vào Thùng rác trên cùng ổ chưa làm tăng dung lượng trống; dọn Thùng rác sẽ xóa vĩnh viễn.

## Lịch và chạy nền

```powershell
.\launch-cli.cmd schedule add --profile daily --mode daily --times 08:00,14:00,20:00
.\launch-cli.cmd schedule add --profile projects --mode hours --interval 6
.\launch-cli.cmd schedule add --profile projects --mode months --interval 1
.\launch-cli.cmd schedule list
.\launch-cli.cmd schedule preview SCHEDULE_ID
.\launch-cli.cmd schedule run SCHEDULE_ID
.\launch-cli.cmd schedule enable SCHEDULE_ID --no-enabled
.\launch-cli.cmd schedule task install
.\launch-cli.cmd schedule task status
.\launch-cli.cmd schedule task disable
.\launch-cli.cmd schedule task enable
.\launch-cli.cmd schedule task remove
```

`daily` nhiều giờ/ngày; `weekly` dùng `--weekdays 0,2,4` (0=thứ Hai); `hours/days/weeks/months/years` mỗi N đơn vị từ lúc tạo. Monthly/yearly giữ anchor gốc và chốt về cuối tháng khi ngày không tồn tại. Múi giờ Asia/Ho_Chi_Minh. Worker Task Scheduler kiểm tra lịch mỗi 5 phút; quét có thể trễ vài phút. Máy ngủ/tắt sẽ chạy bù một lần khi worker hoạt động lại. Task mặc định theo user đăng nhập.

Nếu cần chạy không có người đăng nhập, dùng Windows Service; app xin UAC khi cài/bật (WinSW tải portable trong `.venv/tools`, không cài runtime global):

```powershell
.\launch-cli.cmd schedule service install
.\launch-cli.cmd schedule service start
.\launch-cli.cmd schedule service status
.\launch-cli.cmd schedule service disable
.\launch-cli.cmd schedule service enable
.\launch-cli.cmd schedule service stop
.\launch-cli.cmd schedule service uninstall
```

GUI có Cài đặt / Bật / Tắt / Gỡ ngay ở Kiểm tra và Lịch chạy. Tắt task là `/Change /DISABLE`, không phải xóa task, không cưỡng bức kết thúc lượt đang chạy. Service disable ngừng chạy và tắt tự khởi động; enable bật tự khởi động và chạy service. Gỡ chỉ bỏ đăng ký, giữ cấu hình/lịch sử. Tắt một lịch riêng không tắt toàn bộ worker. Chỉ đăng ký một trong task/service, có chặn bật song song. Task chứa cấu hình MFT phải được cài với quyền cao nhất (Admin/UAC). Service chỉ quét/tạo đề xuất; xác nhận vẫn trong CLI/GUI. Không có hộp thoại GUI trong Session 0. App không tự đăng ký task/service mặc định.

Các lệnh chung tương đương nút GUI:

```powershell
.\launch-cli.cmd background-status
.\launch-cli.cmd background-control task install
.\launch-cli.cmd background-control task disable
.\launch-cli.cmd background-control service enable
```

## Debug, kiểm tra và benchmark

```powershell
.\launch-cli.cmd --debug scan --profile projects
.\launch-cli.cmd config debug false
.\launch-cli.cmd doctor
.\launch-cli.cmd packages
.\launch-cli.cmd check-updates
.\launch-cli.cmd check-updates --package wiztree --package typer
.\launch-cli.cmd system-check drivers
.\launch-cli.cmd benchmark --profile projects --engine native --engine wiztree --repeats 3
```

Debug ghi subprocess/argv/exit và thao tác metadata; audit xóa vẫn ghi khi tắt debug. GUI ghi rõ DEBUG (xanh da trời), INFO (xám), WARNING (vàng), ERROR/CRITICAL (đỏ), giữ màu traceback, lọc mức, bám dòng mới và tự cập nhật khi mở tab. CLI có `logs --level ... --follow`. Log app 5 MB × 6 file, audit 5 MB × 11 file. Mặc định giữ 90 ngày/90 snapshot mỗi phạm vi, ít nhất hai baseline; database có giới hạn và từ chối scan vượt quota.

Benchmark chạy engine lần lượt, đo thời gian quét/nhập/diff, peak RAM mẫu của cây tiến trình. CPU/I/O của engine trực tiếp được đo bằng Windows process handles và cộng với tiến trình cha. Không reset OS cache. Strict profile bỏ qua external MFT để giữ bảo vệ browser. Kết quả kiểm thử được lưu tại `data/release-check.json`.

Engine đã tích hợp: native, WizTree, WinDirStat và gdu. Cài lại engine mã nguồn mở vào `.venv/tools` bằng `launch-cli.cmd engine-install` hoặc nút ở tab Hệ thống. WizTree dùng executable của người dùng, không đóng gói/phân phối kèm.

Hệ thống hiển thị gói/công cụ có icon, phiên bản và đường dẫn; Kiểm tra riêng hoặc tất cả chỉ tra cứu, không cài/cập nhật. Nguồn là [nhà phát hành WizTree](https://diskanalyzer.com/download), [GitHub Releases API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release), [PyPI JSON API](https://docs.pypi.org/api/json/). Không upload đường dẫn/file lên các nguồn này. Mạng lỗi hoặc hạn mức API được báo rõ. Runtime Python khóa riêng; muốn nâng thư viện cần cập nhật khóa và kiểm thử qua môi trường app, không dùng pip global. Engine-install chỉ cài bản OSS đã pin, không tự nâng lên latest.

## Giao diện và cập nhật app (1.1.0)

Cây mặc định đủ tên `System Volume Information` ở cấp đầu và luôn giữ cả ba cột trong chiều ngang đang có. Thu hẹp cửa sổ thì cắt tên bằng `…`; tooltip giữ đường dẫn đầy đủ. Cột chi tiết: Đường dẫn / Mức thay đổi / Dung lượng cũ / Dung lượng mới / Loại / ... . Cấu hình chiều rộng mới dùng khóa riêng để không áp nhầm bố cục cũ sau khi đổi cột.

Trạng thái lọc chọn nhiều mục cùng lúc, có chọn tất cả/bỏ chọn/chỉ có thay đổi; bấm Lọc để áp dụng. Bỏ chọn hết cho kết quả rỗng. Lọc toàn bộ phạm vi rồi mới sắp xếp và phân trang; tổng quan Cũ/Mới không đổi theo bộ lọc. Các ô dung lượng có nền nhẹ và nhãn/giá trị riêng; lượt partial vẫn ghi cảnh báo. Tùy chọn quét mở mặc định, nhớ trạng thái riêng; icon thông tin không phụ thuộc font ký hiệu.

Realtime **chỉ kiểm tra trạng thái Task/Service**, không theo dõi file và không quét lại ổ: 3 giây khi hiện cửa sổ, 15 giây khi ẩn/thu nhỏ, không chạy kiểm tra chồng nhau. Mở quản lý Windows chỉ mở Task Scheduler/Services, không tự bật. Bộ chạy thuộc bản app khác được ghi rõ và không cho sửa từ bản hiện tại.

Tab Hệ thống có phần **cập nhật chính app**, độc lập với kiểm tra thư viện. Chỉ tra/tải/cài khi bấm nút. Nguồn cố định là Release chính thức của `bbkien2312/DiskGrowthMonitor`; lấy bản ổn định, không downgrade. Tải có thể hủy; xác minh kích thước/SHA-256 trước khi chạy. Hash kiểm tra toàn vẹn, **không phải chữ ký nhà phát hành**; binary hiện chưa ký mã.

Bản cài dùng coordinator ngoài runtime để đóng app an toàn, tắt/dừng đúng runner của bản đó (không gỡ), sao lưu cấu hình/UI/history bằng SQLite backup, cài và khôi phục trạng thái bật/tắt/startup type ban đầu. Không cưỡng bức kết thúc quét/DISM; đang có tác vụ thì chờ. Session, log và backup nằm ở `data/updates/session-*`, được giữ để kiểm tra/khôi phục; không tự xóa backup lớn. Bộ cài giữ portable mode. Bản source chỉ mở bộ cài vào thư mục trống khác, không ghi đè source/`.venv`. Nâng từ bản 1.0.0 chưa có updater cần dùng bộ cài mới thủ công.

```powershell
.\launch-cli.cmd compare 2 --older 1 --changes grown,shrunk,new --limit 100
.\launch-cli.cmd background-status --watch --interval 3
.\launch-cli.cmd background-open task
.\launch-cli.cmd app-update check
.\launch-cli.cmd app-update download
.\launch-cli.cmd app-update install
# Bản source: chọn thư mục trống ngoài source
.\launch-cli.cmd app-update install --destination D:\DiskGrowthMonitor-New
.\launch-cli.cmd app-update status
# Khi journal báo restore_pending, mở CLI Admin nếu runner cần Admin:
.\launch-cli.cmd app-update recover D:\DiskGrowthMonitor-App\data\updates\session-...\session.json
```

Nếu cập nhật/khôi phục lỗi, đọc `coordinator.log`, `installer.log`, `session.json` trong session. Backup không tự ghi đè lịch sử đang dùng; cần người dùng xem trước khi khôi phục dữ liệu. Không bảo đảm rollback toàn bộ binary nếu máy tắt giữa lúc cài; có thể chạy lại bộ cài chính thức sau khi kiểm tra runner đã dừng.

## Hỏi AI

```powershell
.\launch-cli.cmd ai question --scan-id 2
.\launch-cli.cmd ai question --plan-id PLAN_ID --copy --provider Gemini
```

Tạo câu hỏi metadata và che username; GUI xem trước rồi sao chép/mở trang. Chưa hỗ trợ tự gửi hoặc API AI. Website có thể cần đăng nhập. Không tự upload lịch sử hoặc nội dung file.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests dùng dữ liệu riêng trong temporary directory. Gồm baseline/diff, >10 MB, hardlinks, exclusion trước traversal, partial/cancel, lịch và xác nhận cleanup. Không chạy xóa dữ liệu người dùng.

## Giới hạn hiện tại

- External export chưa chứng minh được mọi file đều truy cập được, nên được đánh dấu partial; file mất/mới không đủ bằng chứng được đánh dấu unknown. Native giữ lỗi theo đường dẫn.
- SQLite lưu đường dẫn và revision dùng chung, mỗi snapshot tham chiếu metadata không đổi thay vì sao chép toàn bộ. Kích thước phụ thuộc phạm vi và số lượng entry; các lần sau vẫn cần lưu membership và thay đổi. Quota mặc định 4 GB, chỉnh ở Hệ thống; không mặc định lưu trên C.
- Không tự nhận biết mọi chương trình nào dùng package/cache. Người dùng vẫn xem ảnh hưởng trong preview.
- Không có API AI tự gửi; copy/mở web có ở cả GUI và CLI.
- EXE là tùy chọn qua `build-app.ps1`, không bắt buộc giữ thêm bản EXE bên cạnh môi trường nguồn.
- Task/Service chưa được bật trong quá trình triển khai. Service thực cần thử trên tài khoản/quyền đã chọn khi người dùng bật; thời gian quét toàn ổ với MFT chưa benchmark vì yêu cầu bảo vệ Chrome/Edge.

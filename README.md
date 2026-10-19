# Disk Growth Monitor

## 2.1.0 — Parquet + DuckDB

### Quét tay / lịch chạy và số đo thiếu

Quét tay và **Lịch chạy → Chạy ngay** dùng cấu hình đã lưu như worker Task/Service. Nếu cấu hình đang sửa chưa lưu, GUI hỏi **Lưu và chạy / Dùng cấu hình đã lưu / Hủy**. Tab lịch hiển thị công cụ và MFT bật/tắt của cấu hình đã lưu; quyền Admin của app không tự biến `/admin=0` thành MFT.

GUI ưu tiên số đo **Chiếm trên đĩa**. Nếu một mốc thiếu toàn bộ cấp phát theo file nhưng có kích thước file, cả phép xem tự chuyển sang **Kích thước file**, ghi rõ mốc thiếu và không gọi Logical là Windows đã dùng. Người dùng vẫn có thể chọn xem trên đĩa với giá trị chưa rõ. CLI `compare`, `entries`, `tree` mặc định `--measure auto`, hỗ trợ `--measure allocated` / `--measure logical`. Lọc, sort, thống kê trạng thái, cây và phân trang cùng dùng chỉ số đang hiển thị. Khi xem hai lượt mới nhất độc lập, tooltip cột Mới của cây ghi rõ chỉ số của riêng cặp đó.

Ngưỡng dung lượng chỉ loại mục đã biết số đo nhỏ hơn ngưỡng; mục thiếu số đo vẫn được giữ, có ghi chú, không âm thầm coi là 0 B. Mốc đầu hiển thị dung lượng Mới, Cũ/±/± Đĩa là `—`, trạng thái **Mốc ban đầu**, không tính tăng từ 0 B. Mốc cũ không cần ghi lại để so sánh kích thước file; số đo cấp phát còn thiếu không được lấy từ lần quét hiện tại để điền ngược.

Mở `launch-gui.cmd` với source hoặc `DiskGrowthMonitor.exe` sau khi cài. Bản 2.1.0 có hậu tố **Parquet + DuckDB** trong GUI và bộ cài. Các lệnh chính `scan`, `history`, `compare`, `tree`, `entries`, `export`, `tasks` và worker `schedule run/tick` dùng chung lõi Parquet + DuckDB RAM. Các tab Hệ thống, Dọn dẹp có duyệt, Lịch chạy, Nhật ký, cập nhật và UAC được giữ lại.

Thanh dưới tự tính chiều cao theo font/DPI, không dùng lại chiều cao 42px gây cắt nội dung. **Chi tiết thời gian / kết quả** mở vùng cuộn riêng, mặc định thu gọn. Loading ghi rõ công đoạn: thu thập → đọc/chuẩn hóa/hardlink/tổng hợp → ghi/kiểm chứng Parquet → so sánh → nạp cây/bảng. Luồng quét tự so sánh chỉ đạt 100% khi đã nạp kết quả. Báo cáo ghi thời gian từng bước, tổng luồng và số mục tăng/giảm/mới/mất/không đổi/không quét/chưa xác định **toàn cặp mốc**, không chỉ số dòng đang tải. Timing dùng đồng hồ đơn điệu; phần trăm là mốc công đoạn, không đoán tốc độ hoàn tất.

**GUI chính giữ nguyên bố cục đã duyệt**, không dùng màn hình rút gọn của preview. `main_ui.py` kế thừa cửa sổ cũ, `DiskBrowser` / `HistoryPanel` / `TaskPanel`; `columnar_gui_backend.py` nối dữ liệu Parquet vào model cũ. Còn đầy đủ tab Chung/Riêng, bốn tile Cũ/Mới/Chênh lệch/Toàn phạm vi chia Chung–Riêng, chú giải, tooltip có điều kiện, icon, context menu, phím tắt, cây độc lập và dropdown 20/50/100/200/500/1000. Sắp xếp/lọc áp dụng toàn tập trước khi phân trang; GUI giữ lại phiên RAM khi tải thêm/đổi bộ lọc, đóng phiên không xóa snapshot. Preview chỉ để chẩn đoán, không phải GUI thay thế.

- Ưu tiên **Trên đĩa (Allocated)** và GiB cơ số 1024; tự chuyển sang Logical có thông báo nếu một mốc thiếu toàn bộ cấp phát. Không dùng Logical làm Windows Used. Tổng/Used/Trống lịch sử lấy từ số đo đã lưu, số hiện tại hiển thị riêng. Thiếu số đo để chưa rõ; không gán phần dư cho file nhỏ.
- So sánh giữ phiên DuckDB trong RAM; sort/filter toàn tập rồi mới lấy 20/100/1000 mục. Snapshot Parquet là metadata nén, không có nội dung file; không dựng lại chỉ mục SQLite chứa hàng triệu file khi mở/so sánh. Các chức năng tương thích như lịch chạy/đề xuất vẫn dùng kho metadata nhỏ hiện có; không đưa file records mới vào đó.
- Trên D:, app dùng **nguyên kho preview đã được duyệt** tại `data/parquet-preview/data/columnar-preview`, giữ ID các mốc và không tạo bản sao. App-home chưa có kho thử dùng `data/columnar`. Nút xóa mốc chỉ xóa metadata Parquet; không xóa file thật hay nguồn JSONL cũ.
- Tab Lịch sử giữ bảng mốc, bảng so sánh, tóm tắt và lựa chọn nhiều dòng. Nút **Nhập mốc JSONL cũ…** chuyển theo yêu cầu, chỉ một lần; có thể dừng/tiếp tục, nguồn cũ giữ nguyên. SQLite rất cũ cần chuyển sang JSONL bằng công cụ tương thích trước. Không tự chuyển/xóa SQLite/JSONL lúc khởi động bản chính này.
- Nhật ký → **Tác vụ đang chạy / dang dở → Tiếp tục** hiển thị các journal mới trong khung công việc cũ. Export đã hoàn tất được kiểm tra và dùng lại; quét chưa xong chạy lại từ đầu. Dừng/đóng GUI chờ worker kết thúc an toàn; Task/Service có khóa quét chung. Không tự cài lại hoặc thay đăng ký Task/Service trong bước tích hợp source.
- Lịch sử so sánh chỉ lưu tóm tắt JSON (tối đa 500 cặp), không lưu bản sao toàn bộ bảng diff. **Hệ thống → Dung lượng app → Đóng phiên RAM / dọn tạm** đóng phiên đang mở và dọn đúng các phiên đã chết, giữ snapshot.

CLI tương đương (qua `launch-cli.cmd` hoặc `dgm` trong runtime riêng):

```text
dgm scan --profile daily
dgm scan --engine native --root D:\ThuMucThu --no-mft
dgm history
dgm compare 5 --older 4 --minimum-mb 0 --sort new --limit 20
dgm compare 5 --older 4 --minimum-mb 0 --sort new --limit 100 --offset 20
dgm compare 5 --older 4 --measure logical
dgm tree --newer 5 --older 4
dgm comparison-history
dgm storage migrate-columnar
dgm storage migrate-columnar --all --yes
dgm storage parquet
dgm storage clear-work
dgm tasks list
```

Cursor chi tiết thuộc phiên đang mở, không mang qua hai tiến trình CLI; dùng `--offset` với cùng bộ lọc để lấy trang tiếp theo trong lệnh mới. `scan_backend: "legacy"` chỉ dành cho tương thích/kiểm thử lịch sử cũ. Các phần bên dưới mô tả những release đã phát hành và có thể khác source hiện tại.

## Bản 1.5.0 — tiếp tục tác vụ, đối soát và mở lịch sử nhẹ

Thêm dừng–lưu–tiếp tục, đối soát dung lượng vật lý, màu so sánh và khởi động nhẹ từ metadata. GUI/CLI dùng chung dữ liệu; nâng cấp giữ lịch sử, cấu hình và log.

Khởi động chỉ nạp mốc và tóm tắt đã lưu; không tự chạy so sánh, đọc lại toàn bộ JSONL hoặc dựng cây chi tiết. Bấm **So sánh** khi cần chi tiết; lần này vẫn phải đọc log và dựng chỉ mục tạm. Chỉ mục được bỏ khi thoát để tiết kiệm đĩa, không phải chuyển đổi lịch sử lại.

SQLite lịch sử cũ được chuyển **một lần** sang JSONL nén khi khởi động (có thể dừng), sau khi kiểm chứng mới thu hồi đúng SQLite nguồn. Không xóa backup hay CSV trong bước này. Cấu hình `history_format: "sqlite"` hoặc `history_auto_migrate: false` giữ chế độ cũ; bản JSONL đang dùng không chuyển lại. Có thể thực hiện tương đương bằng `dgm storage migrate-history --yes --retire-old`. Không sửa hoặc xóa SQLite trong `data/tmp/history-index` bằng tay khi app đang dùng.

Trong Nhật ký → **Tác vụ đang chạy / dang dở**, chọn **Tiếp tục** để xử lý mốc đã chụp, hoặc **Quét mới** để lấy hiện trạng. Quét chưa đủ sẽ chạy lại từ đầu; tệp xuất/metadata đã hoàn chỉnh được kiểm tra checksum và dùng tiếp, giữ thời điểm chụp. Bước đang dang dở có thể cần tính lại. Không tự tiếp tục xóa, không cưỡng bức ngắt DISM hay bước cài cập nhật. Task/Service đã bật vẫn hoạt động độc lập khi GUI thoát.

CLI dùng chung dữ liệu:

```text
dgm tasks list
dgm tasks show <task-id>
dgm tasks resume <task-id>
dgm tasks discard <task-id> --yes
dgm tasks limit 512
```

Checkpoint nằm trong `data/tasks/<task-id>/`: JSON tiến độ, metadata `.jsonl.gz`, và tệp xuất WizTree hoàn chỉnh chỉ giữ tạm khi cần nhập tiếp. Không chứa bản sao nội dung file; bỏ tệp trung gian sau khi hoàn tất. Mặc định tổng trung gian 512 MB, giữ tối đa 100 tóm tắt hoàn tất, không tự bỏ công việc dang dở. Hệ thống → Dung lượng app hiển thị riêng nhóm điểm tiếp tục.

**Chiếm trên đĩa** là chế độ mặc định cho Cũ/Mới, thống kê và cây. **Kích thước file** cộng theo đường dẫn nên có thể vượt sức chứa vì hardlink; không phải dung lượng thực chiếm. Hai cột ± / ± Đĩa lần lượt luôn là thay đổi kích thước file / cấp phát. Nút **Đối soát** hiển thị byte gốc, Windows cùng thời điểm, phần đã đo và phần chưa quy được vào file. Không gán phần dư cho file ≤10 MB; ngưỡng “Hiện file >” chỉ lọc bảng, file nhỏ vẫn được tính trong tổng. Mốc cũ thiếu cấp phát/phiên bản vẫn ghi chưa biết; chuẩn hóa không khôi phục được dữ liệu đã mất nếu không có nguồn.

[Tải bộ cài Windows x64 v1.5.0](https://github.com/bbkien2312/DiskGrowthMonitor/releases/tag/v1.5.0) · [Mã nguồn](https://github.com/bbkien2312/DiskGrowthMonitor)

**Release cũ 1.5.0.** Tải `installer.exe` và đối chiếu `SHA256SUMS.txt` trong GitHub Release tương ứng. GUI yêu cầu UAC Admin ngay khi mở, không chỉ khi cài; hủy UAC thì app không mở dữ liệu.

## Bản 1.4.0 — giao diện rõ ràng, dọn dẹp có duyệt

- Bảng chi tiết có icon folder/file chung, ký hiệu TT ngắn và màu theo **cả hàng**. Tăng xanh, giảm cam, mất đi xám/gạch đường dẫn, chưa rõ vàng. Hàng đang chọn giữ màu ý nghĩa với viền xanh. Ký hiệu/tooltip và chú giải bổ sung cho màu. Tiêu đề cây/chi tiết cao, đồng bộ, dropdown không cắt chữ; vẫn phân trang/sắp xếp toàn tập, số liệu gốc không đổi.
- **Dọn dẹp** thay danh sách ID rule khó hiểu bằng catalog và giải thích nhìn thấy ngay: đây là gì, khi nào nên dọn, giữ gì, phương pháp/tác động. Tất cả file ban đầu **không chọn**. Phân biệt xóa từng file, CLI xóa toàn cache và bảo trì Windows; dung lượng chưa ước lượng không gán thành 0 B. Tự động/lịch chỉ quét và nhắc duyệt, không xóa khi bạn vắng mặt.
- Đề xuất lưu metadata JSONL gzip, bảng chi tiết phân trang 20–1.000 và sort/filter toàn đề xuất. Hạn một giờ, mặc định tổng log đề xuất tối đa 64 MB và chỉ mục tạm 128 MB; tóm tắt tối đa 200 đề xuất/30 ngày, không sao lưu nội dung file. Xác minh checksum, đường dẫn/rule, identity, size/mtime trước khi xử lý; khóa chống dùng lại/lựa chọn thay đổi. Kết quả giữ tóm tắt và mẫu giới hạn, audit xoay vòng. Có nút dọn metadata hết hạn.
- Bổ sung uv cache, DirectX shader và WER ReportArchive của người dùng. Giữ project được nhận diện, .git/.venv/node_modules, model AI, SQLite/WAL/SHM, dữ liệu trình duyệt và vùng hệ thống. Không thêm registry cleaner, không xóa tay WinSxS/DriverStore/Installer/Package Cache. NVIDIA cần xác nhận cập nhật xong; NuGet ghi rõ lệnh xóa cả global-packages, không giả vờ lọc theo tuổi.
- **Kiểm tra thủ công** file lớn/trùng SHA-256/folder rỗng trong folder bạn chọn; không xóa. Bảo vệ thư mục tham chiếu, bỏ qua hardlink khi tìm bản trùng, xác minh identity qua handle mở file. Mặc định giới hạn 60 giây/100.000 file/256 MB hash và 1.000 dòng trả về; báo rõ khi chưa đọc hết. Tìm trùng hỏi trước khi đọc nội dung, không mặc định toàn C:.
- **Hệ thống** chia Cập nhật app / Công cụ quét / Dung lượng app / Chẩn đoán Windows. Đo dung lượng app riêng vì vùng này tự loại trừ khi quét; backup là thành phần của tổng, không cộng hai lần. Mỗi thao tác quét/so sánh/chuẩn hóa/benchmark ghi footprint app mới, không hồi tố vào mốc cũ. Giữ log lịch sử nén và quy trình chuyển có kiểm chứng từ 1.3.1.
- **Lịch chạy** có tên, tạo/sửa, giờ/thứ dễ đọc, chỉ hiện trường liên quan, lần cuối/kế tiếp và kết quả. Trạng thái lịch độc lập Task/Service; runner vẫn cần đăng ký/bật. **Nhật ký** có bảng màu theo hàng, [LEVEL], lọc công việc/tìm kiếm, traceback và gói hỗ trợ có xem trước/che thông tin. Chỉ đọc 500 KB cuối, giữ 4.000 dòng; không tự gửi lên AI.
- **Lịch sử** ưu tiên số thứ tự, chuẩn hóa, độ bao phủ và tùy chọn hai mốc. Lượt đã chuẩn hóa nhưng thiếu quyền vẫn ghi có giới hạn; lượt chuẩn ngay khi nhập không bị gọi là chưa chuẩn hóa. Không coi phần khác phạm vi là tăng thật hoặc file đã xóa. Xóa metadata lịch sử và xóa file thật là hai việc riêng.

GUI và CLI dùng cùng API; các lệnh bổ sung:

```powershell
dgm.exe cleanup plan --rule pip-cache
dgm.exe cleanup show PLAN_ID
dgm.exe cleanup page PLAN_ID --limit 100 --sort size --direction desc
dgm.exe cleanup select PLAN_ID --path "D:\Example\approved-cache.bin"
dgm.exe cleanup apply PLAN_ID --confirm "XOA PLAN_ID"
dgm.exe cleanup prune
dgm.exe cleanup protected
dgm.exe cleanup inspect-folder "D:\Example\Downloads" --mode duplicates --minimum-mb 10
dgm.exe schedule add --name "Theo dõi hàng ngày" --times "08:00,14:00,20:00"
dgm.exe schedule edit SCHEDULE_ID --name "Tên mới"
dgm.exe log-events --source app.log --level ERROR --anonymize
```

`cleanup plan/show` mặc định trả tóm tắt, dùng `cleanup page` để đọc từng trang hoặc `--full` để xuất tất cả metadata. Kiểm tra thủ công không mặc nhiên cấp quyền xóa. Tham khảo ý tưởng Preview/catalog/bằng chứng từ BleachBit, Winapp2, BCUninstaller và Czkawka; không đóng gói code/rule bên ngoài hoặc tự nạp script Admin.

## Bản 1.3.1 — giới hạn dung lượng dữ liệu app

Bản cài mới dùng **log metadata JSONL gzip**, không chứa nội dung file đã quét. `data/history/catalog.json` lưu mốc/tóm tắt; `scans/*.jsonl.gz` lưu các chỉ số theo đường dẫn và `normalization/*.jsonl.gz` lưu các sửa chuẩn hóa. SQLite chuyển thành chỉ mục tạm ở `data/tmp/history-index`, dùng chung khi GUI/CLI còn mở và bỏ khi tiến trình cuối đóng. Có thể chủ động bỏ chỉ mục bằng nút **Xóa chỉ mục tạm**, giữ nguyên log. Mở lại cặp sau khi bỏ chỉ mục cần đọc toàn bộ log hai mốc để dựng tổng/sắp xếp; không thể tính toàn ổ chỉ từ 20 dòng. CLI từng lệnh có thể dựng lại nếu không còn tiến trình cùng app giữ phiên.

Lịch sử SQLite cũ không bị xóa ngay khi cài đè. Dùng **Hệ thống → Dung lượng app → Chuyển lịch sử sang JSONL nén** hoặc `dgm.exe storage migrate-history --yes --retire-old`: ghi từng mốc, đọc lại xác minh checksum/số bản ghi, giữ bản gốc nếu dừng/lỗi hoặc nguồn thay đổi. Khi duyệt thu hồi sau chuyển, app dọn đúng SQLite cũ không thay đổi, CSV nguồn và backup cập nhật hoàn tất; bảo vệ lượt lỗi/chưa khôi phục. Không phục hồi được nội dung file từ các log này. Tóm tắt so sánh/benchmark, số thứ tự mốc và lớp chuẩn hóa được giữ.

Từ **1.5.0**, GUI có thể thực hiện chuyển đổi một lần khi mở; chọn giữ SQLite hoặc tắt `history_auto_migrate` để không tự chuyển. Thu hồi chỉ bỏ SQLite nguồn đã kiểm chứng, **không kèm xóa CSV/backup**; các mục này có thao tác dọn riêng. Mở lịch sử đã chuyển không xuất hoặc kiểm toàn bộ log lại; khi yêu cầu so sánh chi tiết vẫn cần dựng chỉ mục tạm.

Mặc định giữ một backup cập nhật thành công gần nhất; dọn backup cũ và installer đã dùng sau kiểm chứng cập nhật hoặc khi khởi động GUI. Phiên lỗi/chờ khôi phục, đường dẫn không rõ và reparse point được bảo vệ. Không xóa lịch sử đang dùng; giữ nhật ký phiên cập nhật. Thay đổi chính sách giữ lịch sử chỉ qua thiết lập sẵn có, không bị tự rút ngắn.

**Hệ thống → Dung lượng app** phân loại dữ liệu/log, ước tính dọn backup/installer, trang SQLite có thể tái sử dụng và chỗ trống. Dọn thủ công có xác nhận; xóa cả backup hoàn tất gần nhất cần chọn riêng. Thu gọn cache giữ tóm tắt/kết quả và lịch sử, yêu cầu đủ chỗ tạm. Cache mới hỗ trợ thu hồi tăng dần, cache cũ không tự VACUUM lớn.

Ngưỡng dự phòng mặc định 1.024 MB, thay đổi được. Quét, chuẩn hóa, lập cache và cập nhật kiểm tra chỗ trống, kể cả phần tạm; SQL dài kiểm tra định kỳ. Số cần là ước tính, không bảo đảm dung lượng đỉnh khi phần mềm khác cùng dùng ổ. Không xóa file WAL/SHM hoặc database lịch sử bằng tay.

```powershell
dgm.exe storage report
dgm.exe storage cleanup-updates --preview
dgm.exe storage cleanup-updates --yes
dgm.exe storage compact-cache --yes
```

`storage cleanup-updates --keep 0 --yes` xóa cả backup cập nhật hoàn tất (mất bản dự phòng); không phải mặc định. Bộ cài thủ công tại thư mục hiện có giữ dữ liệu và không tự tạo thêm backup cập nhật.

## Bản 1.3.0

### So sánh theo mốc và giao diện gọn hơn

- **Chung = có bản ghi ở cả hai mốc; Riêng = chỉ một mốc.** Hiện diện tách độc lập khỏi bằng chứng so sánh: phần Riêng có thể là thêm/mất thật, không quét, hoặc chưa rõ. Bản ghi có ở cả hai vẫn so được khi dữ liệu hợp lệ, kể cả lựa chọn loại trừ khác nhau. Danh tính ổ thực sự khác vẫn bị chặn.
- Bốn thẻ Cũ, Mới, Chênh lệch, Toàn phạm vi chia Chung / Riêng. Thẻ Cũ/Mới ghi số đo từng nhóm; chênh lệch chỉ cộng byte file đủ bằng chứng của **cả hai nhóm**, không lấy hiệu các số đã làm tròn. Phần thiếu bằng chứng có nhãn Chưa rõ/≈, không coi là 0 hoặc đã xóa. Cây ghi tổng quan sát và chênh lệch đã xác định cùng cặp mốc; không cộng chồng cha/con, file↔folder hay đường dẫn di chuyển.
- Chi tiết có trạng thái đầu tiên: ký hiệu +/−/↑/↓/↔/»/?/≈ (» = không quét, dùng ký tự tương thích font Windows), nhãn Chỉ Cũ/Chỉ Mới/Cũ+Mới, màu chữ/nền phân biệt. Chỉ đường dẫn Không còn có gạch ngang xám; Không quét/Chưa rõ không gạch. Cột **Trạng thái / Đường dẫn / Cũ / Mới / ± / Loại / Trên đĩa / Tạo / Sửa**; Cũ xanh lam, Mới xanh ngọc. Lọc theo trạng thái, mốc hiện diện, lý do và thư mục **trước phân trang**. Thứ tự Chung/Riêng được lập chỉ mục một lần khi cần, trang rỗng không duyệt lại nhóm khác.
- Chuẩn hóa **v2** lưu lớp riêng, không ghi đè lịch sử gốc; lớp v1 được nâng cấp khi so sánh hoặc chọn **Lịch sử → Chọn lượt chưa chuẩn hóa → Kiểm tra & chuẩn hóa**. Lượt mới chuẩn hóa khi nhập; cache hợp lệ không lập bản đồ lại. Thiếu riêng Allocated vẫn so sánh được Size; hiển thị cấp phát đã biết và số file chưa đo được. Hardlink giữ chủ sở hữu cấp phát toàn cục, không đếm hai lần giữa Chung/Riêng.
- Nhãn mốc ghi Chrome/Edge và phương thức MFT thực tế; native không được ghi là MFT chỉ vì cấu hình cũ có check MFT. Phạm vi và loại trừ thêm trên cùng hàng, nút thư mục dạng icon, loại trừ có editor nhiều dòng.
- `app.log` giữ INFO/WARNING/ERROR, `debug.log` chỉ ghi DEBUG khi bật Debug; Nhật ký chọn được cả hai. Data/log footprint tính cả file debug mới.
- Bố cục gọn: tiêu đề thẻ nằm giữa, phủ cả hai cột Chung/Riêng; dung lượng 20 px đậm; số file/thư mục chung một hàng. Ghi chú nhỏ hơn, tận dụng hết chiều ngang sẵn có của mỗi bên, ưu tiên một dòng nếu đủ chỗ, tối đa hai dòng; không tăng chiều rộng thẻ vì ghi chú. Tooltip chỉ hiện phần chữ thực sự bị cắt; nút chỉ có icon giữ tên chức năng. Chuột phải **Xem dữ liệu hai mốc** đọc byte gốc, lý do từng mốc và đường dẫn đổi tên từ lịch sử, không đọc file hiện tại. Thẻ chỉ chừa một hàng số liệu để dành chiều cao cho cây/chi tiết. Phần cấu hình có ba hàng, cấu hình/công cụ/phạm vi cùng hàng. Kích thước mở app theo màn hình; cửa sổ nhỏ có cuộn ngang/dọc thay vì mất nút. Ghi chú mở rộng cuộn riêng, cây/chi tiết nhận phần chiều cao còn lại; các tab khác cũng có vùng cuộn dự phòng. Kiểm tra hình học trước/sau so sánh và ở DPI 100/125/150%.
- Số đo hiển thị làm tròn số nguyên (.5 làm tròn lên theo trị tuyệt đối), áp dụng cả dung lượng GB/GiB, chênh lệch, thời gian và báo cáo chẩn đoán trên GUI. Byte gốc, độ chính xác dữ liệu lưu, log kỹ thuật và JSON/CSV xuất ra không bị làm tròn. Ngày giờ và chuỗi phiên bản giữ nguyên.
- Icon ổ đĩa/mũi tên riêng, cùng một mẫu trên taskbar, cửa sổ, khay hệ thống, shortcut và bộ cài; ICO gồm 9 kích thước 16–256 px. ID taskbar cố định qua các phiên bản. Quét ngay xanh dương nổi bật, So sánh xanh ngọc, Dừng/Xóa đỏ nhẹ; nút chức năng đậm, trạng thái vô hiệu hóa xám. Vùng cấu hình/chọn mốc và tiêu đề cây/chi tiết có điểm nhấn màu riêng, không thêm hàng tiêu đề chiếm chiều cao.

CLI dùng cùng mô hình:

```powershell
dgm.exe compare 16 --older 15 --section common
dgm.exe compare 16 --older 15 --section private --reason browser
dgm.exe compare 16 --older 15 --section private --presence old --changes missing
dgm.exe compare 16 --older 15 --section all
dgm.exe snapshot-normalize --all-legacy --yes
```

`--common` cũ vẫn nhận để tương thích, không còn bắt buộc. Không thể khôi phục số liệu chưa từng được lưu; dữ liệu thiếu vẫn có cảnh báo, không được biến thành 0 hoặc coi là đầy đủ.

- **Lịch sử → Kiểm tra & chuẩn hóa**: chọn một/nhiều lượt hoặc chọn các lượt chuẩn cũ, xem trước/sau rồi áp dụng. Lớp chuẩn hóa có phiên bản trong `data/normalization.sqlite`, không sửa snapshot gốc, có nút dùng lại bản gốc. Tổng quan, cây và chi tiết cùng dùng lớp đã chọn; cache phụ thuộc được xây lại khi dữ liệu chuẩn hóa đổi.
- File/thư mục/reparse/hardlink và metadata thiếu dùng cùng quy tắc. Size là tổng logic theo đường dẫn (có thể lớn hơn sức chứa ổ do hardlink/nén/sparse); Allocated đếm hardlink một lần. Có GB/GiB. Nếu bản cũ đã mất số cấp phát, app cảnh báo thiếu nguồn và giữ chưa xác định, không đoán lại bằng file hiện tại.
- Giải thích chính xác khác biệt phạm vi. **Chỉ phần chung** là lựa chọn chủ động khi hai lượt khác bộ loại trừ; tính lại cả tổng thư mục trong phạm vi chung, không gọi đây là tổng toàn ổ. Không khôi phục dữ liệu của vùng chưa được quét.
- CSV WizTree kiểm tra cột, số byte, tên cột ngày/MFT và dấu phân cách ngày Windows. Nhật ký nhập ghi tổng file, phần bị loại trừ, reparse và hardlink. Nguồn CSV nén có SHA-256, mặc định tối đa 512 MB / 90 ngày, chỉnh trong Hệ thống hoặc dọn riêng tại Lịch sử. Nguồn có thể đã hết hạn; không tự khẳng định dữ liệu cũ phục hồi chính xác.
- Thanh trạng thái kéo chỉnh chiều cao, tiến độ riêng toàn chiều rộng, tóm tắt dài cuộn/sao chép được. Log có `[DEBUG]`, `[INFO]`, `[WARNING]`, `[ERROR]`, màu theo mức; đọc cả định dạng cũ và traceback. Bấm thông báo desktop khôi phục cửa sổ đang chạy.
- GUI EXE có manifest `requireAdministrator`; source launcher và `dgm.exe gui` cũng xin UAC trước khi tạo cấu hình/log/data. CLI không giao diện và lịch nền giữ cơ chế quyền theo tác vụ; không bật UAC bất ngờ trong service.

CLI tương đương (ID là ID nội bộ trong kết quả `history`):

```powershell
dgm.exe snapshot-audit --id 10 --id 14
dgm.exe snapshot-normalize --all-legacy --yes
dgm.exe snapshot-original --id 10 --yes
dgm.exe compare 14 --older 13 --common
dgm.exe source-cache-clear --yes
```

Kiểm tra trước không đổi dữ liệu gốc. So sánh tự chuẩn bị lớp chuẩn hóa một lần nếu cần; lượt mới dùng kết quả đã chuẩn hóa lúc nhập. Dùng lại bản gốc sẽ tắt tự áp dụng lớp cho lượt đó đến khi bạn chủ động chuẩn hóa lại. Chỉ số/data/log và mọi dữ liệu cá nhân không nằm trong mã nguồn hay bộ cài.

## Bản 1.1.1

- Lịch sử giải thích vì sao một lượt hoàn tất nhưng chưa xác minh đầy đủ: engine ngoài không chứng minh được độ bao phủ; native có mục/vùng không đọc được sẽ nêu số lượng. Đây là mức tin cậy thận trọng, không tự khẳng định chắc chắn đã bỏ sót.
- Mỗi lượt quét, so sánh và benchmark ghi dung lượng data/log trước, sau và chênh lệch; chỉ đo kích thước file database riêng của app (SQLite/WAL/SHM) cùng log app/audit, không quét lại file trên ổ và không phải số cluster NTFS đã cấp phát. Lịch sử có cột Δ data/log và tooltip chi tiết. SQLite có thể tái sử dụng trang trống, log có thể xoay vòng; số Δ là ước lượng footprint của thao tác.
- Kiểm chứng bản này: 210 test tự động và smoke test bộ cài frozen trên fixture riêng (cài, scan/compare, update an toàn và gỡ cài đặt giữ lại dữ liệu).

### Các cải tiến cập nhật/giao diện tích hợp trong 1.2.0

Cập nhật trong app dùng nguyên data/lịch sử hiện tại và không yêu cầu chọn thư mục mới. Với bản chạy từ source, runtime Release nằm tại `.dgm-app` bên trong thư mục hiện có; launcher cũ và lệnh `python -m dgm` tự mở bản đã cập nhật. Source cùng `.venv` phát triển vẫn giữ lại; dùng biến môi trường `DGM_SOURCE=1` khi muốn chạy source để phát triển. Bản EXE cài đặt cập nhật ngay tại vị trí EXE hiện tại. Updater sao lưu cả database lịch sử quét/benchmark lẫn so sánh.

Tiến độ quét, so sánh, tải chi tiết, benchmark và tải cập nhật chạy 0–100% theo các bước. Bước chưa biết tổng giữ ở mốc hiện tại và hiển thị số mục đã đọc. Các bước có tổng (nhập CSV, tổng hợp thư mục, tải bộ cài) tăng theo khối lượng; phần trăm không suy ngược từ thời gian chờ. Dòng “Đã xong” và kết quả sau quét liệt kê cụ thể những phần đã xử lý.

- Chuẩn snapshot v3 dùng cùng quy tắc cho WizTree/native/gdu/WinDirStat: đường dẫn chuẩn được lập chỉ mục lúc nhập, giữ riêng logical/allocated và giá trị chưa biết. Tổng folder đối chiếu từ file một lần lúc quét; không chuẩn hóa lại khi xem/so sánh các snapshot mới. Hardlink chọn cùng đường dẫn đại diện, kể cả khi engine export byte cấp phát ở đường dẫn khác.
- Byte cấp phát Windows lấy qua `GetFileInformationByHandleEx` (Standard/Compression Info), không lấy độ dài file thông thường làm size-on-disk. [FILE_STANDARD_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_standard_info). So sánh khác engine dùng độ chính xác thời gian chung một giây; cùng engine giữ độ chính xác gốc. Khác quyền đọc/vùng chưa xác minh vẫn được cảnh báo, không suy ra chắc chắn file đã mất.
- Cấu hình gọn ở trên; các lựa chọn quét luôn có sẵn, chỉ ghi chú ẩn/hiện. Hai khung cây/chi tiết ưu tiên chiều cao; cửa sổ nhỏ dùng cuộn dọc thay vì cắt khung. Ghi chú kết quả tự mở sau thành công, không bật lại khi tải thêm/sắp xếp.
- Lịch sử tự nạp khi mở, làm mới theo kết quả mới, giữ lựa chọn/vị trí cuộn; nâng cấp thuật toán không làm mất tóm tắt cũ. Chi tiết giới hạn dữ liệu/data/log nằm ở vùng xem dòng được chọn.
- Một nút **Cập nhật** thực hiện kiểm tra → tải → SHA-256 → sao lưu → cài đúng vị trí → xác minh phiên bản/lịch sử → mở lại. Installer mới yêu cầu UAC admin; tiến trình mở lại được khởi động trước UAC để giữ tài khoản ban đầu. Không báo thành công chỉ vì đã mở bộ cài. CLI tương đương: `dgm.exe app-update install --yes`.

## Bộ cài EXE

App Windows tiếng Việt để biết file/folder nào làm ổ đĩa tăng dung lượng giữa các lần quét. CLI và GUI gọi cùng core và đọc cùng database/config. Môi trường nằm trong thư mục app trên ổ D; không cài package Python global hoặc sửa System PATH.

## Bộ cài EXE — 1.1.0

Tải `installer.exe` và đối chiếu `SHA256SUMS.txt` trong Release. Windows 10/11 x64, không cần cài Python/pip. Bộ cài cho chọn thư mục, mặc định `D:\DiskGrowthMonitor-App` nếu có ổ D, nếu không dùng thư mục Programs của tài khoản hiện tại. Không cài đè thư mục mã nguồn hoặc thư mục không rỗng chưa có dấu nhận diện bản cài.

- GUI: `DiskGrowthMonitor.exe`; CLI tương đương: `dgm.exe --help`. Các ví dụ `launch-cli.cmd` bên dưới có thể thay bằng `dgm.exe` ở bản cài.
- Hai EXE dùng **một runtime riêng trong `.venv`**, không nhân đôi Python/Qt, không sửa PATH hay đăng ký Python hệ thống. Đây là runtime đóng gói, không phải virtualenv để chạy pip; muốn thay thư viện hãy sửa/kiểm thử/build từ nguồn.
- Installer không kèm cấu hình cá nhân, snapshot, database, log, cache, WizTree hoặc engine OSS đã tải trên máy phát triển. Không tự quét/xóa/cài Task/Service. WizTree chọn từ bản người dùng đã cài; engine OSS chỉ tải khi người dùng yêu cầu.
- Cài và mở GUI đều yêu cầu Admin qua UAC. Shortcut Start menu, Desktop tùy chọn; có trình gỡ cài đặt trong Installed apps. Không tự đóng app đang chạy khi nâng cấp.
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
- GUI yêu cầu UAC ngay khi mở để dùng được bản cài trong Program Files; đã Admin thì không hỏi lặp. CLI không giao diện xin quyền riêng khi tác vụ cần (MFT, kiểm tra/dọn hệ thống, quản lý chạy nền).
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

### Quyền Admin tự động

- Từ 1.2.0, GUI yêu cầu Admin ngay từ lúc mở. CLI không giao diện giữ quyền hiện tại và dùng worker Admin cho tác vụ cần quyền; lựa chọn chưa lưu (phạm vi, engine, MFT, loại trừ, bảo vệ browser, ngưỡng) được chuyển nguyên vẹn, không ghi đè cấu hình.
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

GUI gọi profile là **Cấu hình quét**. Quét ngay/Quét lại dùng lựa chọn hiện tại, không tự ghi đè cấu hình; Lưu cấu hình để lịch dùng lựa chọn mới. Các lựa chọn luôn hiện, chỉ ghi chú ẩn/hiện. Mốc Cũ phải trước Mới, cùng danh tính ổ; khác phạm vi được ghi rõ ở từng dòng, không chặn toàn bộ so sánh. Chênh lệch chỉ tính phần đủ bằng chứng, giờ Việt Nam UTC+07. Lượt bị hủy/lỗi/đang chạy không đưa vào danh sách so sánh. Snapshot là quan sát trong khoảng quét, không phải backup hay filesystem snapshot nguyên tử.

### Giao diện và phân trang 1.1

- Số **Lần quét 01, 02…** cố định theo cấu hình, tính cả lượt lỗi/hủy; không đánh số lại sau khi xóa/sắp xếp. ID trong JSON/CLI vẫn là khóa nội bộ, không phải số thứ tự hiển thị.
- WizTree/MFT xuất tổng thư mục/root sẵn có; so sánh WizTree dùng trực tiếp số đó, không chuẩn hóa lại. Native/các engine khác cộng dung lượng từ con lên cha ngay khi quét và ghi dấu nguồn dữ liệu. Chỉ snapshot cũ thiếu dấu nguồn mới cần chuẩn hóa tương thích một lần, có cache. Hardlink chỉ tính physical một lần.
- Tổng quan dùng toàn snapshot, không chỉ những file >10 MB hoặc 20 dòng. Dung lượng file được quét khác dung lượng volume đã dùng; file nhỏ vẫn nằm trong tổng. Partial/coverage chưa đủ được ghi rõ là chênh số liệu quan sát, không bằng chứng đã xóa file.
- Dropdown **20/50/100/200/500/1000 mục mỗi lượt**, mặc định 20, nhớ trong `data/ui-settings.json`. Đổi số lượng giữ dòng đang xem; lần tải tiếp nối đúng thứ hạng. Ví dụ 20 + 100 = top/bottom 120 toàn tập kết quả, không chỉ sắp xếp 20 dòng cũ. Mở phép so sánh mới dùng số đang chọn.
- Nhấp tiêu đề sắp xếp/lọc toàn kết quả **trong phạm vi và bộ lọc đang áp dụng** trước khi phân trang. Tên/đường dẫn bắt đầu A–Z, dung lượng/ngày bắt đầu giảm dần; nhấp lại đảo chiều. Đổi cột/chiều quay về đầu, lấy lại cùng số lượng đã mở theo thứ tự mới. Cột thay đổi sắp theo số có dấu. Ngưỡng file áp dụng cho `max(Cũ, Mới) > ngưỡng`; folder không bị ngưỡng file loại bỏ.
- Bấm chọn folder trên cây giới hạn bảng trong folder; bật Gồm thư mục con để tính cả nhánh, tắt để chỉ hiện con trực tiếp. Chỉ bung mũi tên không đổi phạm vi. Xem toàn bộ bỏ giới hạn folder; tổng quan không đổi. Tên trong nhánh được lập chỉ mục riêng, không ép lập thứ tự tên cả ổ; các nhánh/tên toàn phạm vi không ghi đè thứ tự của nhau. COUNT được tái sử dụng khi đổi cột/chiều.
- Model giữ cache metadata **400–2000 dòng**, không giữ toàn tập trong RAM. Trang có kích thước khác nhau giữ riêng mốc bắt đầu/con trỏ; đọc lại trang bị loại khỏi RAM không làm đổi con trỏ tải tiếp. Dừng tải hủy cả chuẩn bị chỉ mục/cây; So sánh hoặc Lọc để tải lại. Dừng quét riêng hủy scan/export/import/SQL. Chỉ mục ban đầu/nhánh chưa cache vẫn có thể cần chờ.
- Cây bên trái luôn theo **hai lượt hợp lệ gần nhất của cấu hình, cùng phạm vi**. Nó không theo cặp Cũ/Mới bên phải. Bấm cây chỉ lọc chi tiết, không đổi cặp mốc hoặc tổng quan. Xanh ↑ tăng; đỏ ↓ giảm; xám — chưa biết/mốc đầu. Màu xanh không có nghĩa thay đổi có lợi.
- Tab **Lịch sử** lưu mọi trạng thái quét và tóm tắt so sánh toàn phạm vi: dung lượng, số file/folder tăng/giảm/thêm/mất/khác. Mở so sánh cũ bằng nhấp đúp; tóm tắt còn sau khi chi tiết cache được loại bỏ. Không suy đoán file mới/mất trong vùng chưa xác minh.
- Tab **Lịch sử** cho chọn một/nhiều lượt (Ctrl/Shift) hoặc **Chọn tất cả lượt**, rồi xóa metadata và cache so sánh phụ thuộc sau xác nhận; không xóa file thật, cấu hình, lịch hay log. CLI tương ứng: `snapshots-delete --id 12 --id 15` hoặc `snapshots-delete --all` (toàn bộ còn yêu cầu nhập `XOA LICH SU`).
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
.\launch-cli.cmd snapshots-delete --id 12 --id 15
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

Cây mặc định đủ tên `System Volume Information` ở cấp đầu và luôn giữ cả ba cột trong chiều ngang đang có. Thu hẹp cửa sổ thì cắt tên bằng `…`; tooltip chỉ hiện khi chữ bị cắt. Chế độ cây: **Cặp Cũ–Mới / 2 lượt mới nhất**, chiều cao theo font/DPI. Cột chi tiết trạng thái đứng đầu; các cột còn lại đo tối đa 20 dòng trong RAM, không đo toàn ổ; kéo tay vẫn được giữ. Bố cục mới dùng `details-v4`, không áp nhầm cột v3.

Trạng thái lọc chọn nhiều mục cùng lúc, có chọn tất cả/bỏ chọn/chỉ có thay đổi; bấm Lọc để áp dụng. Bỏ chọn hết cho kết quả rỗng. Lọc toàn bộ phạm vi rồi mới sắp xếp và phân trang; tổng quan Cũ/Mới không đổi theo bộ lọc. Các ô dung lượng có nền nhẹ và nhãn/giá trị riêng; lượt partial vẫn ghi cảnh báo. Tùy chọn quét mở mặc định, nhớ trạng thái riêng; icon thông tin không phụ thuộc font ký hiệu.

Realtime **chỉ kiểm tra trạng thái Task/Service**, không theo dõi file và không quét lại ổ: 3 giây khi hiện cửa sổ, 15 giây khi ẩn/thu nhỏ, không chạy kiểm tra chồng nhau. Mở quản lý Windows chỉ mở Task Scheduler/Services, không tự bật. Bộ chạy thuộc bản app khác được ghi rõ và không cho sửa từ bản hiện tại.

Tab Hệ thống có phần **cập nhật chính app**, độc lập với kiểm tra thư viện. Chỉ tra/tải/cài khi bấm nút. Nguồn cố định là Release chính thức của `bbkien2312/DiskGrowthMonitor`; lấy bản ổn định, không downgrade. Tải có thể hủy; xác minh kích thước/SHA-256 trước khi chạy. Hash kiểm tra toàn vẹn, **không phải chữ ký nhà phát hành**; binary hiện chưa ký mã.

Bản cài dùng coordinator ngoài runtime để đóng app an toàn, tắt/dừng đúng runner của bản đó (không gỡ), sao lưu cấu hình/UI/history và comparison bằng SQLite backup, cài và khôi phục trạng thái bật/tắt/startup type ban đầu. Session, log và backup nằm ở `data/updates/session-*`, được giữ để kiểm tra/khôi phục. Bộ cài giữ portable mode. Bản source cập nhật vào runtime quản lý `.dgm-app` và tiếp tục dùng nguyên data/log hiện có; launcher cũ tự chuyển sang bản mới. Nâng từ bản 1.0.0 chưa có updater cần dùng bộ cài mới thủ công.

```powershell
.\launch-cli.cmd compare 2 --older 1 --changes grown,shrunk,new --limit 100
.\launch-cli.cmd background-status --watch --interval 3
.\launch-cli.cmd background-open task
.\launch-cli.cmd app-update check
.\launch-cli.cmd app-update download
.\launch-cli.cmd app-update install
# Chỉ khi chủ động muốn cài một bản độc lập khác:
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

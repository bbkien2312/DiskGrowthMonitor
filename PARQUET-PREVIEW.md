# Lõi nhanh thử nghiệm trên D:

**Bản chính 2.0.0 — Parquet + DuckDB (19/10/2026):** lõi trong tài liệu này đã được nối vào các entry point chính qua `production_core.py` và `main_ui.py`, giữ nguyên GUI đầy đủ. Dùng `launch-gui.cmd`/`launch-cli.cmd` hoặc bộ cài 2.0.0. Kho preview đã duyệt được dùng tại chỗ, không sao chép hay đổi ID. Giữ launcher preview để chẩn đoán; phần mô tả “chưa tích hợp” bên dưới là biên bản thử nghiệm trước đó, không mô tả trạng thái release 2.0.0.

**Sửa cách tích hợp GUI:** màn hình preview rút gọn không còn được ghép vào GUI chính. Bản chính dùng lại đầy đủ `gui.Window`, `DiskBrowser`, `HistoryPanel`, `TaskPanel` và bố cục cũ, chỉ thay dịch vụ dữ liệu bằng `columnar_gui_backend.py`. Giữ Chung/Riêng, tile Cũ/Mới/Chênh lệch/Toàn phạm vi, tooltip/chú giải, dropdown tải thêm, sort toàn tập và cây hai chế độ. Mỗi cặp đang dùng có một phiên RAM, dùng lại khi phân trang/lọc; phiên cũ không còn dùng và spill được giải phóng khi rảnh/đóng app.

## Biên bản thử trước khi phát hành 2.0.0 (lưu để tham khảo)

Các phần dưới ghi lại giai đoạn thử độc lập trước tích hợp: không nâng cấp bản cài trên C:, không thay lịch sử JSONL cũ và khi đó chưa thay worker lịch chạy. Source ở thời điểm thử còn mang phiên bản 1.5.0; không phải mô tả phiên bản đang phát hành ở đầu tài liệu.

## Mở thử

- GUI: `launch-preview.cmd` (yêu cầu UAC khi mở, theo thiết lập app).
- CLI: `launch-preview-cli.cmd --help`.
- Home riêng: `D:\DiskGrowthMonitor\data\parquet-preview`.
- Kho: `data/columnar-preview` bên trong home trên. Không mở Store/SQLite của app cũ.

Đã sao chép hai mốc JSONL #36/#37 của bản D: thành mốc Parquet #3/#4 để thử, không quét lại C:. Chọn Cũ #3, Mới #4 rồi So sánh. Hai bản thử #1/#2 chưa phát hành đã được tạo lại với khóa v2 sau khi sửa alias file không có đuôi. Mỗi lần mở GUI chỉ đọc catalog nhỏ; chỉ mở snapshot khi bạn yêu cầu so sánh.

## CLI tương đương lõi GUI

```bat
launch-preview-cli.cmd history
launch-preview-cli.cmd compare 4 3 --sort new --direction desc --limit 20
launch-preview-cli.cmd compare 4 3 --sort new --direction desc --limit 100 --offset 20
launch-preview-cli.cmd compare 4 3 --root "C:\Windows" --section common --changes grown --limit 100
launch-preview-cli.cmd compare 4 3 --measure logical --sort new --direction desc --limit 20
launch-preview-cli.cmd tree 4 3 --root "C:\Windows"
launch-preview-cli.cmd import-legacy D:\DiskGrowthMonitor --id 36 --id 37
launch-preview-cli.cmd import-csv D:\export.csv --root C:\ --timezone UTC --mft
launch-preview-cli.cmd scan --engine native --root D:\ThuMucThu --no-mft
launch-preview-cli.cmd configure gdu D:\DiskGrowthMonitor\.venv\tools\gdu\gdu_windows_amd64.exe
launch-preview-cli.cmd jobs
launch-preview-cli.cmd resume ID_TAC_VU
launch-preview-cli.cmd storage
launch-preview-cli.cmd clear-temp
```

`delete ID...` cần xác nhận hoặc `--yes`, chỉ xóa snapshot thử nghiệm. `discard-job ID` chỉ bỏ journal/export tạm, giữ snapshot và nguồn ngoài. Không có thao tác dọn file người dùng tự động trong bản thử.

## Dữ liệu và tính đúng

- WizTree xuất CSV theo UTC, đọc bằng bộ đọc native DuckDB, không stat lại từng đường dẫn. Chỉ chuẩn hóa khi nhập snapshot mới. Các mốc đã nhập không chuẩn hóa lại khi mở.
- Chỉ dùng `--mft` khi export thật sự bằng MFT Admin. Chế độ WizTree directory-walk xuất số thứ tự giả vào cột MFT; không dùng các số đó làm identity/hardlink. Với walk/nguồn không chắc, giữ số cấp phát gốc trong note, còn phép tính Đĩa để chưa rõ thay vì cộng trùng hardlink. Dùng MFT Admin để có số cấp phát khử trùng.
- Snapshot Parquet nén ZSTD là metadata, không chứa nội dung file. Chỉ một bản Parquet/mốc, một catalog nhỏ; không có SQLite/DuckDB lưu lâu dài. Kết quả so sánh ở RAM, không tạo bản sao đầy đủ lịch sử khác.
- Khóa ghép: danh tính ổ + đường dẫn chuẩn hóa lexical. Không mở file hiện tại để sửa log cũ. Hai danh tính ổ đã biết và khác nhau không ghép. Log cũ thiếu ID ổ được ghép cùng mount với cảnh báo giả định, không coi là đã xác minh ổ vật lý.
- Khóa v2 xử lý alias Win32 đuôi chấm/khoảng trắng: WizTree walk có thể xuất `filename.` cho file không đuôi trong khi Native là `filename`. Tên literal dạng `\\?\...` có đuôi chấm thật giữ riêng; bản ghi gốc vẫn được lưu nguyên. Không ghép lẫn khóa thử nghiệm v1/v2, phải nhập lại bản thử v1 từ nguồn.
- Chung: có ở cả hai mốc; Riêng: chỉ Cũ hoặc chỉ Mới. File thêm/xóa thực sự chỉ kết luận khi mốc đối diện có bằng chứng bao phủ; loại trừ, Chrome/Edge, thiếu quyền, export chưa xác minh không bị biến thành 0 B/xóa mất.
- Tổng toàn phạm vi chỉ cộng file, không cộng thêm folder cha. Logical tính theo từng đường dẫn, **không phải Used của ổ**; hardlink, sparse và nén có thể làm Logical lớn hơn Used. Cấp phát hardlink chỉ tính một lần nếu có ID. Thiếu số cấp phát giữ null/chưa rõ; không lấy chênh Used rồi gọi toàn bộ là file ≤10 MB.
- Cây, Cũ/Mới, chênh lệch, sắp xếp và lọc trạng thái mặc định dùng **Trên đĩa (Allocated)**, không dùng Logical làm dung lượng chiếm ổ. CLI `compare`/`tree` cũng mặc định `--measure allocated`; `--measure logical` là chế độ phụ chủ động chọn.
- Đơn vị mặc định **GiB = 1024³ byte**, cùng cơ số Windows (Explorer ghi nhãn GB). Số nguyên rút gọn như Windows; tooltip giữ byte chính xác. Các thành phần rút gọn có thể không cộng khớp số tổng đã rút gọn; phép tính vẫn trên byte nguyên. Có lựa chọn GB = 1.000.000.000 byte riêng, làm tròn số nguyên.
- Tổng ổ/Used/Trống lấy từ số đo Windows đã chụp cùng mốc, không suy từ Logical. Quét mới lưu số đo volume cho cả Native/gdu/WinDirStat; CSV WizTree có số đo ổ được ưu tiên. Mốc cũ thiếu hoặc mâu thuẫn số đo để chưa rõ, không lấy Used hôm nay thay lịch sử. Nút **Used hiện tại** chỉ gọi API dung lượng ổ, không liệt kê file, hiển thị riêng.
- **Chưa quy vào file = Used − cấp phát file đã biết** chỉ khi quét toàn ổ và không thiếu cấp phát. Phần này có thể gồm metadata NTFS, mục loại trừ/vùng không đọc được, không gán cho file ≤10 MB. Nếu cấp phát vượt Used, giữ chênh âm và cảnh báo thay vì cắt số. Quét thư mục hoặc thiếu cấp phát không suy ra phần dư chính xác. `≥ … ?` là tổng phần đã biết, chưa phải tổng cấp phát đầy đủ.
- Giữ các trường đã chụp và overlay chuẩn hóa v2 đang có khi sao chép JSONL. Giữ nguyên cảnh báo số liệu cũ; Parquet không chữa được số đo lịch sử bị mất nguồn. Chuẩn hóa mới không tự ghi đè/xóa nguồn cũ.
- Sắp xếp/lọc trên **toàn bộ tập trong phạm vi**, sau đó mới lấy trang 20/100/1000. Chọn folder thì chi tiết chỉ hiện bên dưới folder đó. Cây lấy tổng chênh file xác minh được; nếu có phần chưa rõ, chênh xác minh có thể khác Mới trừ Cũ.
- Native, gdu, WinDirStat dùng cùng schema; không chia nhóm chỉ vì khác engine. Trường công cụ không cung cấp giữ thiếu. Hai adapter gdu/WinDirStat cần kiểm thử thêm trên các dữ liệu thật đặc biệt (hardlink/reparse); chưa coi là đạt độ tương đương tuyệt đối với WizTree.

## Dừng / tài nguyên

- Export hoàn tất được kiểm checksum và dùng lại khi tiếp tục. Export dở và Native dở chạy lại từ đầu, không trộn hai lần đo sống.
- DuckDB memory budget mặc định 768 MiB, **không phải giới hạn RSS cứng của toàn bộ Python/GUI**; cấu hình spill 256 MiB không phải trần kích thước hiển thị của toàn thư mục phiên. Export chờ tiếp tục có ngân sách 512 MiB. Hết ngân sách thì báo lỗi, không tự xóa lịch sử; file thuộc phiên được dọn khi đóng.
- Đóng bình thường sẽ giải phóng RAM và dọn phiên tạm. Nếu bị kill/mất điện, `clear-temp` hoặc nút Dọn tạm chỉ xóa các session riêng có owner đã chết, không xóa phiên đang chạy/snapshot.
- So sánh không lưu kết quả dài hạn; mở lại cần ghép Parquet trong RAM, nhưng không dựng database từ JSONL lại. CLI mỗi lệnh là một phiên; GUI giữ phiên khi lọc/tải thêm.

## Kết quả thử thực tế (không phải cam kết mọi máy)

Hai log D: #36/#37: 909.525 và 905.892 bản ghi; hợp 931.024, Chung 884.393, Riêng 46.631. Phép cộng Chung + Riêng kiểm tra khớp từng snapshot.

- Chuẩn bị so sánh đầy đủ + cây + kiểm tra tổng: khoảng 13 giây.
- Trang đầu 20 file: khoảng 0,7 giây; gốc cây khoảng 0,4 giây.
- RSS đỉnh đo được khoảng 1 GB (có cả bước nhập lần đầu); snapshot nén hai mốc khoảng 118 MB.
- GUI mở cửa sổ và đọc catalog khoảng 0,6 giây trong bài thử; mở cặp + trang đầu + cây khoảng 15–18 giây khi máy còn chạy các bài thử khác. Không gọi đây là mức RAM/tốc độ tương đương WizTree.
- Sao chép hai JSONL lớn lần đầu khoảng 34 giây, chỉ chạy khi chủ động nhập. Bài thử CSV 216 MB / 905.892 dòng được tạo từ metadata D: (không phải một lượt quét C: mới), nhập khoảng 32 giây; số dòng, Logical và Đĩa khớp snapshot nguồn. CSV và snapshot bài thử này đã được dọn ngay sau đối chiếu.
- Kiểm thử hồi quy: 529 test qua, cùng các test tập trung cho UAC/preview. Bốn executable Native/WizTree-walk/gdu/WinDirStat thật đã đối chiếu cùng fixture D: có hardlink; Logical và tập đường dẫn khớp. Không chạy MFT admin toàn ổ C: để kiểm thử trong lượt này.
- Đây không phải bài ghép đơn giản ~1 giây trước đó; số đo này có xử lý coverage, trạng thái, cây thư mục và đối chiếu tổng. OS cache/CPU nền ảnh hưởng thời gian.

Bản thử dùng các chức năng lõi chung giữa CLI/GUI; các tab dọn dẹp, lịch chạy, cập nhật của bản ổn định vẫn ở app cũ. Chưa tự chuyển toàn bộ lịch sử, chưa xóa JSONL/SQLite cũ, chưa build/push/release. Cần người dùng test bản D: đạt trước khi thay lõi chính và thiết kế migration production.

## Đối chiếu dung lượng mốc #5 — 19/10/2026

Đọc snapshot WizTree MFT #5 người dùng đã lưu, không quét lại C: và không đổi quyền file. Số lịch sử:

- Tổng ổ: 161.290.907.648 byte → **150 GiB**.
- Used Windows tại lúc quét: 149.150.740.480 byte → **138 GiB**.
- Cấp phát file đã đo: 148.535.681.024 byte → **138 GiB**; không thiếu cấp phát.
- Chưa quy vào file: **615.059.456 byte → 586 MiB**.
- Logical: 163.802.063.620 byte → 152 GiB, hoặc 164 GB theo cơ số 1000. Đây không phải Used, chỉ xuất hiện khi chọn Logical.

Đã kiểm tra GUI ở 1440×980 và 1152×800; đổi chỉ số dùng lại phiên so sánh, đổi đơn vị không truy vấn lại snapshot. Chuẩn bị cặp #4/#5 khoảng 18 giây trong bài thử. SHA-256 snapshot #5 trước/sau giống nhau; thư mục phiên tạm còn 0 byte sau đóng. Ảnh và báo cáo: `artifacts/disk-size-fix-20261019/`.

Kiểm thử sửa dung lượng: 539 test hồi quy qua; sau phần xử lý số đo lịch sử sai kiểu/thiếu và tooltip byte chính xác, 31 test tập trung qua. `git diff --check` sạch. Fixture kiểm thử của lượt này còn khoảng 214 MB trong artifacts; lệnh dọn bị môi trường chặn, chưa xóa. Work tạm của app được dọn khi đóng như báo cáo trên.

# Disk Growth Monitor 1.5.0 — 18/10/2026

- Khởi động không tự so sánh hoặc nạp toàn bộ JSONL; nạp mốc/tóm tắt từ catalog, kể cả khi cây lưu chế độ hai lượt mới nhất. Chi tiết chỉ dựng khi người dùng yêu cầu.
- Chuyển SQLite nguồn sang JSONL một lần; gọi lại bỏ qua phần đã hoàn thành. Thu hồi sau kiểm chứng, không xóa backup/CSV; sau khi không còn SQLite nguồn thì không đọc lại archive để kiểm tra thu hồi. Không coi chỉ mục SQLite tạm là nguồn cũ có thể bỏ trước khi lịch sử đã lưu.
- Đóng app khi có tác vụ: hỏi Tiếp tục chạy / Thu nhỏ xuống khay / Dừng an toàn rồi thoát; chờ worker và bảo trì Windows kết thúc an toàn.
- Nhật ký có hàng đợi tác vụ bền vững và nút Tiếp tục / Quét mới / Xem / Bỏ điểm lưu. CLI tương đương: `tasks list/show/resume/discard/limit`.
- Checkpoint JSON nhỏ, metadata JSONL gzip có checksum; giữ tạm export WizTree hoàn chỉnh nếu cần tiếp tục nhập. Quét chưa hoàn chỉnh chạy lại từ đầu; metadata hoàn chỉnh giữ thời điểm nguồn, không trộn file hiện tại. Tổng tệp trung gian mặc định tối đa 512 MB; chỉ giữ 100 tóm tắt đã hoàn tất, không tự xóa checkpoint dở hay phát lại xóa file.
- Mặc định hiển thị cấp phát trên đĩa; chuyển được sang kích thước file. Cũ/Mới, thống kê và cây đồng bộ theo chế độ; ± vẫn là thay đổi kích thước file, ± Đĩa là cấp phát. Sắp xếp Cũ/Mới vẫn toàn tập trước phân trang.
- Đối soát Windows cùng mốc / cấp phát đã đo / phần chưa quy vào file; không gán phần dư cho file nhỏ, không cắt số cho khớp. File ≤10 MB đã tính trong tổng, thống kê riêng khi có dữ liệu. Chuẩn hóa cũ có thể bổ sung thống kê này từ metadata, không quét lại ổ.
- Chênh lệch cấp phát đã xác minh không lấy riêng một phía của file thiếu số đo. Giữ unknown, kiểm tra Chung + Riêng = Tổng; metadata bị mất không được suy thành 0.
- Ghi phiên bản app/chuẩn dữ liệu/chuẩn hóa cho mốc mới; mốc cũ thiếu provenance vẫn ghi chưa biết. Riêng chỉ Cũ nền xám, chỉ Mới xanh lá nhạt; Chung ± / ± Đĩa tăng xanh nước biển, giảm đỏ, độc lập từng ô.

# Disk Growth Monitor 1.4.0 — 18/10/2026

- Chi tiết có icon folder/file, ký hiệu ngắn, màu cả hàng và viền chọn giữ màu trạng thái; tiêu đề/dropdown cây cao hơn, không cắt chữ. Phân trang, sắp xếp và phép tính byte gốc giữ nguyên.
- Dọn dẹp có duyệt: catalog tiếng Việt, giải thích điều kiện/tác động và vùng bảo vệ. Không tick sẵn; tool xóa cả cache được phân biệt với xóa từng file. Chặn project trong Temp, model AI, SQLite/WAL/SHM và reparse; không dọn registry hoặc xóa tay thành phần Windows.
- Kế hoạch dọn chỉ lưu metadata JSONL nén có checksum/hạn một giờ; tổng hạn log 64 MB / chỉ mục tạm 128 MB. GUI/CLI phân trang toàn đề xuất, xác nhận và kiểm lại identity/mtime/size; chống dùng lại, xử lý lựa chọn đã đổi. Không tạo bản sao nội dung để phục hồi.
- Thêm kiểm tra thủ công file lớn/trùng/folder rỗng trong folder chọn riêng, có giới hạn tài nguyên, tham chiếu được đánh dấu giữ, không tự xóa. Tìm trùng cần đọc nội dung để hash và hỏi trước trên GUI; không đếm hardlink thành dung lượng có thể thu hồi.
- Hệ thống chia bốn mục; footprint data/log app được đo riêng để không bỏ sót vùng tự loại trừ hoặc cộng trùng backup. Lịch sử phân biệt phiên bản chuẩn hóa và độ bao phủ; không coi khác phạm vi là tăng thật.
- Lịch có tên/tạo/sửa, thứ dễ chọn, lần cuối/kế tiếp; Task/Service có trạng thái độc lập. Nhật ký có màu theo hàng, lọc theo việc/mức/từ khóa, chi tiết và xuất metadata hỗ trợ có che thông tin/xem trước, không tự gửi web.
- Các khả năng mới có CLI dùng chung API. Runtime, update tại chỗ và lịch sử nén của 1.3.1 được giữ; không thêm package hệ thống hoặc rule/script bên ngoài.

## Bản 1.3.1 — 17/10/2026

- Sửa xung đột nâng cấp schema cache khi cây thư mục và lịch sử mở đồng thời; kiểm tra lại schema dưới khóa ghi SQLite. Xác minh log sau cập nhật không ghi đè file lịch sử; con trỏ phân trang giữ ổn định khi tạo lại chỉ mục tạm.

- Bổ sung lịch sử JSONL gzip làm nguồn metadata, không chứa nội dung file; SQLite là chỉ mục tạm theo phiên GUI/CLI. Dọn chỉ mục khi tiến trình cuối thoát, có nút/CLI bỏ chủ động; mở lại cặp cần đọc log/lập chỉ mục đầy đủ trước khi tải trang.
- Chuyển lịch sử SQLite cũ qua GUI/CLI, xác minh từng log/checksum/số bản ghi, giữ mốc/tóm tắt/benchmark/lớp chuẩn hóa. Nguồn thay đổi/dừng/lỗi thì không xóa SQLite; thu hồi nguồn cũ/CSV/backup hoàn tất chỉ theo duyệt. Bản cài mới không giữ CSV nguồn mặc định.

- Sửa tích lũy backup SQLite/installer sau cập nhật: mặc định giữ một backup thành công gần nhất, chỉ dọn phiên hoàn tất đã kiểm chứng; bảo vệ phiên lỗi/chờ khôi phục. Khởi động GUI xử lý các phiên thành công từ phiên bản cũ; giữ nhật ký và dữ liệu hiện dùng.
- Thêm Hệ thống → Dung lượng app và CLI storage: phân loại dữ liệu/log, đo trang trống SQLite, xem trước/xác nhận dọn backup/installer, chọn riêng khi muốn xóa cả backup gần nhất, lưu số backup/dung lượng dự phòng. Không tự đổi retention lịch sử.
- Thu gọn cache giữ dữ liệu/kết quả, yêu cầu đủ chỗ tạm. Cache mới dùng incremental auto-vacuum có giới hạn thời gian khi dọn; không tự chạy VACUUM lớn trên cache cũ.
- Kiểm tra dung lượng cho quét, chuẩn hóa, tạo cache và backup/cài cập nhật; mặc định chừa 1.024 MB. SQL dài kiểm tra định kỳ và trả lỗi rõ ràng; cache có sẵn vẫn đọc được khi thiếu chỗ tạo cache mới. Có khóa bảo trì/cập nhật và kiểm tra lại đường dẫn/file trước khi xóa.

# Disk Growth Monitor 1.3.0 — 17/10/2026

- Chung là bản ghi có ở cả hai mốc; Riêng là chỉ một mốc. Hiện diện tách khỏi độ tin cậy: thêm/mất thật trong Riêng vẫn được tính vào tổng, không quét/chưa rõ không bị coi là đã xóa. Ghi rõ vấn đề ở Cũ hay Mới; giữ lịch sử gốc, không đọc file hiện tại để giả làm dữ liệu cũ.
- Chuẩn hóa v2 có lớp dữ liệu riêng; đồng bộ native/WizTree/WinDirStat/gdu, giữ null khi thiếu dữ liệu, hardlink cấp phát một lần. Thư mục thưa, đổi file/thư mục, di chuyển giữa các nhánh và tổng số byte có kiểm thử. Cache so sánh mới tạo lại từ dữ liệu đã lưu khi cần, không phải quét lại ổ.
- Trạng thái ở cột đầu, ký hiệu và nền/chữ phân biệt. Chỉ đường dẫn Không còn mới gạch ngang xám. Cột Cũ/Mới gọn, màu riêng; lọc trạng thái, mốc có bản ghi và lý do trên toàn bộ dữ liệu trước phân trang. Chuột phải Xem dữ liệu hai mốc đọc byte gốc/lý do/đường dẫn di chuyển từ snapshot.
- Sắp xếp Chung/Riêng lập chỉ mục một lần khi cần; trang rỗng không duyệt nhóm khác. Giữ tải 20/50/100/200/500/1000 mục, thứ tự toàn phạm vi hoặc nhánh đang chọn, bộ nhớ GUI có giới hạn và kích thước cột kéo tay không bị đặt lại khi tải trang.
- Gọn phần cấu hình, ưu tiên chiều cao cây/chi tiết; cuộn ngang/dọc dự phòng cho màn hình nhỏ, dropdown chế độ cây rõ và vừa font/DPI. Dung lượng số nguyên, 20 px đậm; ghi chú dùng hết chiều ngang sẵn có, ưu tiên một dòng và tối đa hai dòng, không kéo rộng thẻ. Phép tính, byte gốc và dữ liệu xuất không bị làm tròn.
- Icon ổ đĩa/mũi tên riêng với 9 cỡ 16–256 px, dùng chung cửa sổ/taskbar/khay hệ thống/shortcut/bộ cài. Nút Quét ngay nổi bật, nút chức năng đậm, màu nhấn phân vùng. Tooltip chỉ xuất hiện khi chữ bị cắt; nút chỉ có icon giữ tên chức năng.
- Tách app.log INFO/WARNING/ERROR và debug.log DEBUG theo lựa chọn, tính cả footprint data/log; lịch sử và chuẩn hóa vẫn có công cụ dọn riêng với xác nhận.
- Cập nhật tại vị trí hiện tại, sao lưu/giữ lịch sử quét, so sánh, benchmark, cấu hình và log. Không cài package Python toàn hệ thống, không tự đăng ký Task/Service, không đóng gói WizTree hay dữ liệu cá nhân.
- Kiểm chứng: 372 bài kiểm thử tự động; giao diện DPI 100/125/150%; bốn công cụ trên fixture riêng ở D:. Bộ cài frozen được kiểm thử cài mới, nâng cấp tại chỗ, chuyển bản source sang runtime quản lý, nâng cấp từ bộ cài cũ, lỗi checksum và gỡ giữ dữ liệu. Task/Service thật và bản đang dùng không bị sửa bởi bài thử.

Giới hạn: nguồn quét không chứng minh độ bao phủ vẫn có cảnh báo, không tự khẳng định mọi mục thiếu là đã xóa. Size theo đường dẫn khác Allocated và dung lượng Windows đã dùng. Bộ cài chưa ký mã; kiểm tra nguồn Release và SHA-256 khi Windows hiện cảnh báo. Windows 11 x64 đã kiểm thử trực tiếp; Windows 10 x64 là nền tảng nhắm tới, chưa có máy riêng kiểm thử.

# Disk Growth Monitor 1.2.0 — 17/10/2026

- Chuẩn hóa lịch sử theo lớp dữ liệu có phiên bản, giữ nguyên bản gốc, xem trước/sau và dùng lại bản gốc; GUI/CLI tương đương. File, thư mục, reparse, ngày thiếu và hardlink cùng được đối soát. Tổng quan/cây/chi tiết dùng cùng dữ liệu; thay lớp chuẩn hóa vô hiệu hóa cache/phạm vi/con trỏ liên quan.
- Phân biệt Size theo đường dẫn, Allocated hardlink một lần và dung lượng ổ, lựa chọn GB/GiB. Không ép Size bằng sức chứa ổ. Dữ liệu cũ mất số cấp phát phải báo chưa xác định/thiếu nguồn, không tự đoán bằng metadata hiện tại.
- Giải thích cụ thể khác phạm vi/bộ loại trừ/danh tính ổ; lựa chọn Chỉ phần chung tính lại tổng theo vùng chung và ghi rõ không phải toàn ổ.
- Sửa đọc CSV WizTree hiện hành: CREATEDDATE/MFTRECNO, cột capacity tùy chọn, ngày dùng dấu cách theo Windows và ngày rỗng. Giữ nguồn nén/SHA-256 có giới hạn 512 MB/90 ngày mặc định; nhật ký đối soát nhập, nút dọn nguồn riêng. Lượt mới tái sử dụng chuẩn hóa lúc nhập, không cộng lại toàn bộ khi so sánh.
- Trạng thái kéo chỉnh chiều cao, tiến độ riêng toàn chiều rộng, tóm tắt dài cuộn được. Nhãn log có ngoặc vuông/màu, tương thích log cũ/traceback; bấm thông báo khôi phục app.
- GUI yêu cầu UAC Admin ngay khi mở qua manifest Windows; source/CLI mở GUI cũng xin quyền trước khi ghi dữ liệu. Không tự xin UAC từ lịch nền.

## Cải tiến trước đó nay phát hành cùng 1.2.0

- Snapshot contract v3 và khóa đường dẫn lưu khi nhập; tổng thư mục dùng cùng quy tắc file/hardlink cho các engine. Giữ null khi chưa biết cấp phát, sửa cách lấy size-on-disk Windows; thời gian khác engine dùng độ chính xác chung, không làm mất độ chính xác native trong lịch sử.
- Bỏ tạo lại chỉ mục/cập nhật scope toàn bảng mỗi lần đọc cache; tải nhánh dùng cặp đã chuẩn bị. Giữ tóm tắt lịch sử của thuật toán cũ, chuẩn hóa tương thích một lần khi cần so sánh chéo engine.
- Thu gọn cấu hình và thanh điều khiển, tăng chiều cao cây/chi tiết; tùy chọn cần thiết không bị thu gọn, chỉ ghi chú đóng/mở. Ghi chú so sánh tự mở khi có kết quả mới, không tự mở lại khi tải trang/sắp xếp.
- Lịch sử nạp tự động lúc mở, cập nhật sau tác vụ, giữ lựa chọn/cuộn và có vùng đọc chi tiết; tách kết quả đã lưu khỏi độ đầy đủ dữ liệu.
- Gộp tải/cài thành một nút Cập nhật; installer yêu cầu admin, khởi động lại trong phiên gốc, kiểm tra phiên bản và số bản ghi/cấu hình sau cài trước khi báo thành công.

- Cập nhật từ app không yêu cầu thư mục mới: bản EXE nâng cấp tại vị trí hiện tại, bản source sử dụng runtime quản lý `.dgm-app` trong thư mục app và tiếp tục dùng cùng data/log. Launcher cũ tự mở bản cập nhật; source và `.venv` phát triển giữ nguyên.
- Sao lưu cả lịch sử quét/benchmark và database so sánh; giữ nguyên lịch sử, cấu hình, log và trạng thái lịch chạy.
- Thanh tiến độ 0–100% theo các giai đoạn quét, so sánh, tải chi tiết, benchmark và tải cập nhật. Không dùng thanh chạy qua lại; bước chưa biết tổng giữ ở mốc hiện tại và cập nhật số mục đã đọc.
- Tóm tắt liệt kê các việc đã xong và số file/thư mục, số tăng/giảm/mới/mất/chưa rõ. Lịch sử hiển thị kết quả đã lưu thay nhãn hoàn tất chung chung.

# Disk Growth Monitor 1.1.1 — phát hành 16/10/2026

- Làm rõ trạng thái “Hoàn tất • có giới hạn”: với WizTree/engine ngoài, app không thể tự xác minh đã bao phủ toàn bộ phạm vi; với native, lịch sử hiển thị số mục/vùng không đọc được. Đây là cảnh báo về độ tin cậy, không phải kết luận chắc chắn đã thiếu dữ liệu. Các thay đổi thêm/mất có thể chuyển thành “chưa xác định” khi phạm vi chưa được xác minh.
- Ghi lại dung lượng data/log trước và sau từng lượt quét, cặp so sánh và lần chạy benchmark. Đo kích thước file SQLite của app (bao gồm WAL/SHM) cùng app/audit log đã xoay vòng; không duyệt nội dung ổ đĩa được quét và không phải số cluster NTFS đã cấp phát.
- Lịch sử GUI có cột “Δ data / log”, tooltip cho dung lượng trước/sau và giải thích cách đọc. CLI trả về cùng số liệu; benchmark gắn số đo tổng thao tác và số đo từng lượt scan.
- Chênh lệch là ước lượng footprint trong khoảng thao tác: SQLite có thể tái sử dụng trang đã cấp phát, cache comparison có thể bị trim và log có thể xoay vòng. Mẫu sau được chụp trước khi ghi metadata thống kê để không tự tính phần ghi của chính nó.
- Tiếp tục giữ các thay đổi đã duyệt cho lượt trước: mở file/folder bằng double-click, tối ưu chuẩn hóa đối chiếu, và xóa chọn một/nhiều/toàn bộ snapshot cùng cache phụ thuộc.
- Kiểm chứng: 210 test tự động; smoke test installer frozen với fixture riêng xác nhận cài mới, scan/compare, nâng cấp an toàn và gỡ cài đặt vẫn giữ data/log.

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

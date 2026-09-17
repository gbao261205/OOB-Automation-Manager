# Danh sách đề xuất nâng cấp & optimize — OOB-Automation-Manager

Tổng hợp từ audit kiến trúc (`docs/architecture-audit.md`), sắp xếp theo mức độ ưu tiên thực tế. Mỗi mục có: **Vấn đề → Đề xuất → Effort → Rủi ro nếu không làm**. Xem `docs/implementation-plan.md` cho kế hoạch triển khai chi tiết từng mục (được nhóm thành các Work Package).

---

## 🔴 ƯU TIÊN 1 — BẮT BUỘC (ảnh hưởng đúng đắn/an toàn của hệ thống)

### 1. Bật thật (hoặc công khai rõ) tính năng Push/Revert
- **Vấn đề**: `dry_run=True` bị hardcode ở cả 3 nơi gọi (`oob_monitor.py:1259, 1262`, `oob_web.py:674`) → Push/Revert không bao giờ thực sự gửi lệnh xuống thiết bị, nhưng baseline vẫn bị cập nhật như thể đã sửa thành công.
- **Đề xuất**: thêm 1 cờ config duy nhất (`push_live_mode: bool`) điều khiển toàn bộ, thay vì hardcode rải rác. Khi tắt (mặc định): đổi text log/UI thành "[MÔ PHỎNG]" rõ ràng, không cập nhật baseline. Khi bật: thêm xác nhận kép + hiển thị rõ lệnh sắp gửi.
- **Effort**: Thấp → Trung bình.
- **Rủi ro nếu bỏ qua**: Người vận hành tin rằng cổng đã được sửa đúng trong khi thiết bị thật vẫn sai.

### 2. Mã hoá credential trong `oob_config.json`
- **Vấn đề**: lưu password/enable_password/vertiv passwords plaintext. Chỉ `working_creds.json` được mã hoá, và fallback base64 gần như vô nghĩa vì `cryptography` không có trong `requirements.txt`.
- **Đề xuất**: thêm `cryptography` vào `requirements.txt`; áp dụng `_encrypt_dict`/`_decrypt_dict` (đã có sẵn) vào `save_config`/`load_config`.
- **Effort**: Thấp — tái dùng hàm đã có sẵn.

### 3. Ẩn credential khi trả về `GET /api/config`
- **Vấn đề**: chỉ lọc bỏ `credentials`, còn password/enable_password/vertiv_admin_password vẫn trả nguyên văn cho session Admin.
- **Đề xuất**: mask tất cả field mật khẩu khi GET, chỉ ghi đè khi POST có giá trị mới khác mask.
- **Effort**: Thấp.

---

## 🟠 ƯU TIÊN 2 — QUAN TRỌNG (đúng đắn dữ liệu & khả năng điều tra sự cố)

### 4. Ghi lại diff thật khi baseline tự cập nhật
- **Vấn đề**: `log_baseline_change()` chỉ ghi "CAP NHAT BASELINE: alias (ip)" — không lưu cái gì đã đổi.
- **Đề xuất**: ghi thêm nội dung `diff_options()` (extra/missing/changed) vào `baseline-logs/`, dạng JSON.
- **Effort**: Thấp-Trung bình.

### 5. Sửa timeout Deep Verify không hoạt động
- **Vấn đề**: `max_verify_duration`/`_verify_deadline` được tính nhưng không bao giờ kiểm tra lại trong vòng lặp verify.
- **Đề xuất**: thêm check thoát sớm khi vượt deadline trong vòng lặp `for key, opt in options.items()`.
- **Effort**: Thấp.

### 6. Gỡ trùng lặp logic parse menu Cisco
- **Vấn đề**: `oob_lib.parse_menu()` và logic inline trong `poll_host_multi()` là 2 bản parser khác nhau, đã lệch nhau ở xử lý `[bracket]` key.
- **Đề xuất**: gộp về 1 hàm duy nhất trong `oob_lib.py`.
- **Effort**: Trung bình — cần viết test trước khi gộp.

### 7. Làm cho 2 luồng Daemon thực sự độc lập
- **Vấn đề**: `run_verify_daemon()` và vòng scan trong `run_daemon()` dùng chung `action_lock` toàn cục → 2 luồng "độc lập" theo README thực chất chặn lẫn nhau.
- **Đề xuất**: tách lock theo mục đích/theo host thay vì 1 lock toàn cục.
- **Effort**: Trung bình.

---

## 🟡 ƯU TIÊN 3 — CHẤT LƯỢNG & BẢO TRÌ

### 8. Bổ sung bộ test tự động (hiện tại = 0%)
Bắt đầu với các hàm thuần logic: `parse_menu()`, `options_equal()`, `diff_options()`, `compute_next_scheduled_run()`, `extract_hostname()`.
- **Effort**: Trung bình, lợi ích dài hạn rất lớn.

### 9. Cache lại `_parse_verify_logs_for_status()`
- **Vấn đề**: đọc/parse toàn bộ file `.json` trong `verify-logs/` mỗi lần gọi, gọi ở gần như mọi API đọc.
- **Đề xuất**: cache theo `alias` với invalidation dựa trên mtime.
- **Effort**: Thấp-Trung bình.

### 10. Validate IP khi thêm thiết bị qua Web
- **Vấn đề**: `/api/device` POST không kiểm tra định dạng IP như CLI/Import Excel.
- **Đề xuất**: áp dụng cùng regex validate.
- **Effort**: Rất thấp.

### 11. Cảnh báo khi config JSON bị lỗi thay vì âm thầm reset
- **Đề xuất**: in cảnh báo rõ ràng khi `load_config()` rơi vào nhánh except.
- **Effort**: Rất thấp.

---

## 🟢 OPTIMIZE HIỆU NĂNG (chỉ cần khi quy mô tăng)

### 12. Giảm khoá toàn cục cho SQLite
- **Đề xuất**: bật `PRAGMA journal_mode=WAL` để giảm tranh chấp đọc/ghi.
- **Effort**: Trung bình.

### 13. Thread pool size nên theo cấu hình, không hardcode
- **Đề xuất**: đưa `scan_max_workers`/`verify_max_workers` thành config có thể chỉnh.
- **Effort**: Thấp.

### 14. Tránh chạy chồng lặp khi 1 chu kỳ scan/verify chưa xong
- **Đề xuất**: thêm guard "đang chạy" để bỏ qua tick trùng thay vì xếp chồng.
- **Effort**: Thấp.

---

## 🔵 NÂNG CẤP TÍNH NĂNG

### 15. Cho phép sửa (edit) alias thiết bị qua Web
- Thêm `PUT`/`PATCH` cho `/api/device` để đổi alias mà không cần xóa/thêm lại.

### 16. Hiển thị trạng thái Daemon thật (`daemon.pid`) trên Web
- Thêm API đọc file heartbeat để Web biết `--daemon` có đang chạy thật không.

### 17. Hỗ trợ lịch chạy theo ngày cụ thể trong tháng / one-shot
- Mở rộng `compute_next_scheduled_run()` hỗ trợ `monthly` và `once`.

### 18. Hỗ trợ Push cho thiết bị Vertiv thật
- Phụ thuộc mục 1 — code `push_vertiv_port_names` đã viết sẵn logic thật, chỉ đang bị khoá bởi `dry_run=True`.

---

## Đề xuất tăng độ chính xác & độ tin cậy thu thập dữ liệu (Deep Verify)

**Triệu chứng gốc**: pivot thủ công vào 1 line console vẫn vào bình thường, nhưng chạy tool báo TIMEOUT hoặc không lấy được data.

### Nguyên nhân chính (CONFIRMED bằng code) — "Drain xoá mất data vừa về"
Nhánh xử lý Cisco/Telnet/SSH trực tiếp trong `check_port_via_oob()` (`oob_monitor.py:996-1002`) dùng `_write("")` để gõ Enter "đánh thức" — với **MiniSSH**, `write()` luôn gọi `_drain_pending()` (xoá sạch buffer + đọc-bỏ data đang chờ trên socket) TRƯỚC khi gửi. Nếu banner/hostname của thiết bị đích đã về nhưng chưa kịp đọc đúng lúc `_write("")` chạy, data đó bị xoá mất trước khi hàm `_read()` cuối cùng kịp thấy — dẫn tới TIMEOUT giả dù kết nối vẫn tốt.

Codebase đã tự nhận ra đúng vấn đề này và viết hàm `write_no_drain()` (`oob_lib.py:329-340`) để tránh — **nhưng chỉ áp dụng cho nhánh Vertiv**, chưa áp dụng cho nhánh Cisco/Telnet/SSH trực tiếp.

### 19. Đổi `_write("")` → `_write_no_drain("")` trong nhánh Cisco/Telnet/SSH
- **Effort**: Rất thấp. **Ưu tiên cao nhất** trong nhóm này — khả năng trúng nguyên nhân cao nhất.

### 20. Đọc "ngay" trước khi gõ Enter đánh thức (giống pattern Vertiv)
- Áp dụng pattern "đọc trước, chỉ đánh thức nếu chưa thấy prompt" (Vertiv đã làm đúng) cho nhánh Cisco/Telnet/SSH.
- **Effort**: Trung bình.

### 21. Tăng/tách timeout riêng cho SSH vs Telnet
- SSH handshake luôn chậm hơn Telnet; thêm config riêng `verify_wait_after_connect_ssh`/`_telnet`.
- **Effort**: Thấp.

### 22. Thêm 1 lần retry khi lần đọc đầu tiên timeout (áp dụng mọi loại port)
- Hiện chỉ port ảo (>2000) mới có "clear line" retry; các loại port khác chỉ có 1 lần thử.
- **Effort**: Thấp-Trung bình.

### 23. Không nuốt exception âm thầm
- Đổi `except Exception: pass` (dòng ~1114) thành lưu lý do thật vào `note`, để log Verify hiển thị lý do cụ thể.
- **Effort**: Rất thấp — tăng khả năng tự chẩn đoán rất nhiều.

### 24. Log timing chi tiết khi `debug_verify` bật
- Thêm mốc thời gian tương đối vào từng bước ghi của `debug_dump()` để phân biệt "không có data" với "có data nhưng bị đọc trễ/mất".
- **Effort**: Thấp.

**Cách xác nhận đúng nguyên nhân trước khi sửa**: bật `debug_verify` (CLI `[u]`) hoặc dùng Live Debug (CLI `[l]` / nút 🐛 trên Web) trên đúng port đang lỗi, xem `debug-logs/deep_verify_debug.log`.

---

## Thứ tự triển khai thực tế đề xuất

1. **Tuần 1**: Mục 1 (label rõ mô phỏng), 2, 3 (bảo mật) + mục 19 (drain-race fix) — effort thấp, ảnh hưởng lớn nhất.
2. **Tuần 2-3**: Mục 4, 5, 10, 11, 20-24 (đúng đắn dữ liệu + Deep Verify reliability).
3. **Tháng sau**: Mục 8 (test) song song mục 6 (gộp parser).
4. **Khi cần**: Mục 7, 12-14 (concurrency/scale).
5. **Theo nhu cầu vận hành**: Mục 15-18 (tính năng UX).

Xem `docs/implementation-plan.md` để biết kế hoạch triển khai kỹ thuật đầy đủ (Work Package, dependency graph, phase roadmap, test plan, risk analysis) cho toàn bộ 24 mục trên.

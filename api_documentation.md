# Tài liệu API cho OOB Web (oob_web.py)

Tài liệu này mô tả chi tiết tất cả các endpoint (API) hiện có trong ứng dụng `oob_web.py`. Các API được chia thành 2 nhóm chính: **Read-only (Tra cứu không cần đăng nhập)** và **Action (Yêu cầu đăng nhập, làm thay đổi dữ liệu)**.

## 1. API Xác thực & Bảo mật (Authentication)

### `[POST] /login`
- **Chức năng**: Xử lý đăng nhập của người dùng vào Web UI.
- **Payload**: Form data (username, password).

### `[GET] /logout`
- **Chức năng**: Xử lý đăng xuất và xóa session.

### `[POST] /api/change-password`
- **Chức năng**: Thay đổi mật khẩu của user đang đăng nhập. Có kiểm tra rò rỉ mật khẩu qua Pwned Passwords.
- **Payload**: JSON
  ```json
  {
    "old_password": "mật khẩu hiện tại",
    "new_password": "mật khẩu mới"
  }
  ```
- **Yêu cầu đăng nhập**: Có (@login_required)

---

## 2. API Tra cứu trạng thái & Dữ liệu (Read-only)

> [!NOTE]
> Các API trong nhóm này được sử dụng để lấy dữ liệu hiển thị lên Dashboard, không yêu cầu `@login_required` để hỗ trợ hiển thị màn hình giám sát chung.

### `[GET] /api/stats`
- **Chức năng**: Trả về số liệu tổng quan (Tổng số thiết bị, Online, Offline, Đã có Baseline, Tổng số cảnh báo) cho Dashboard.
- **Response**: JSON 
  ```json
  {"total": 100, "online": 90, "offline": 10, "has_baseline": 85, "alarms": 5}
  ```

### `[GET] /api/devices`
- **Chức năng**: Lấy danh sách toàn bộ thiết bị OOB cùng trạng thái hiện tại (ping, menu_state, alarm_count, ok_count, update_time).
- **Response**: Mảng JSON các thiết bị.

### `[GET] /api/device/<ip>/options`
- **Chức năng**: Lấy danh sách các option (port/kết nối) chi tiết của một thiết bị IP cụ thể từ Baseline và kết quả Verify.
- **Response**: JSON chứa `device_name`, `menu_name`, và mảng `options` với trạng thái Verify của từng option.

### `[GET] /api/search?q=<query>`
- **Chức năng**: Tìm kiếm thiết bị, alias, description, target IP hoặc hostname thực tế, trả về kết quả được chấm điểm (score) theo độ chính xác.
- **Response**: Mảng JSON (tối đa 100 kết quả).

### `[GET] /api/export/excel`
- **Chức năng**: Xuất báo cáo chi tiết toàn bộ trạng thái OOB, Baseline, và Verify ra file Excel `.xlsx`.
- **Response**: Trả về file (Attachment).

---

## 3. API Quản lý & Cấu hình (Management)

> [!IMPORTANT]
> Tất cả API từ phần này trở đi đều yêu cầu đăng nhập (`@login_required`).

### `[GET, POST] /api/config`
- **Chức năng**: 
  - **GET**: Trả về cấu hình hệ thống hiện tại (bỏ qua `credentials`; các field nhạy cảm như `password`, `enable_password`, `vertiv_connect_password`, `vertiv_admin_password` trả về `"******"` nếu đã đặt, không trả mật khẩu thật).
  - **POST**: Cập nhật cấu hình hệ thống (interval, ports, schedules, `push_live_mode`...). Gửi lại đúng chuỗi `"******"` cho field nhạy cảm sẽ được bỏ qua (giữ nguyên giá trị cũ), không ghi đè bằng chuỗi mask.
- **Payload (POST)**: JSON các khóa cần cập nhật.
- **Lưu ý an toàn**: `push_live_mode` (mặc định `false`) quyết định `/api/action {"action":"push"}` và `/api/revert` gửi lệnh thật hay chỉ mô phỏng — xem mục 4.

### `[GET, POST, DELETE] /api/credentials`
- **Chức năng**: Quản lý danh sách tài khoản SSH/Telnet chung.
  - **GET**: Trả về danh sách tài khoản (chỉ hiện username và cờ có pass/enable).
  - **POST**: Thêm tài khoản mới.
  - **DELETE**: Xóa tài khoản theo `index`.

### `[POST, PUT, DELETE] /api/device`
- **Chức năng**: Quản lý danh sách IP OOB.
  - **POST**: Thêm một OOB mới (Payload: `ip`, `alias`). IP được validate định dạng, trả `400` nếu sai.
  - **PUT**: Sửa alias và/hoặc IP của 1 thiết bị đã có, **giữ nguyên vị trí dòng** trong file danh sách (Payload: `old_ip` *, `alias`, `new_ip` — 2 field sau optional). Trả `400` nếu `old_ip` không tồn tại, `new_ip` sai định dạng, hoặc `new_ip` trùng 1 dòng khác.
  - **DELETE**: Xóa một OOB khỏi danh sách (Payload: `ip`).

### `[GET] /api/daemon-status`
- **Chức năng**: Đọc trạng thái tiến trình `--daemon` CLI thật (qua file `daemon.pid`, không phải task tự tạo trên Web) — khác với sidebar cũ vốn chỉ đếm task Web tự khởi tạo.
- **Response**: `{"status": "running"|"stale"|"unknown", "pid": int|null, "age_seconds": number|null}`.
- **Không yêu cầu đăng nhập** (read-only, giống `/api/stats`).

### `[POST] /api/import`
- **Chức năng**: Nhập danh sách IP hàng loạt từ file Excel.
- **Payload**: FormData chứa `file` (.xlsx).
- **Response**: Báo cáo số lượng thêm mới, bỏ qua trùng lặp, bỏ qua lỗi.

---

## 4. API Thực thi Tác vụ (Action Execution)

### `[POST] /api/action`
- **Chức năng**: Kích hoạt chạy nền (background thread) các tác vụ như **Scan Baseline**, **Verify Vật lý**, **Push Log**.
- **Payload**: JSON
  ```json
  {
    "action": "scan|verify|push",
    "ip": "172.29.10.x" (Hoặc rỗng để chạy toàn bộ)
  }
  ```
- **Response**: JSON chứa `task_id`.
- **`action=push`**: nếu cấu hình `push_live_mode=false` (mặc định), lệnh push CHỈ MÔ PHỎNG — không kết nối thiết bị, Baseline không đổi, push-log ghi tiêu đề `[MO PHONG]`. Chỉ khi `push_live_mode=true`, lệnh được gửi thật, Baseline chỉ cập nhật nếu thành công, và tự động Re-Verify ngay sau đó.

### `[POST] /api/revert`
- **Chức năng**: Thực thi lệnh Revert lại cấu hình Menu (mô tả cũ) từ một file log Push trước đó, dựa trên các dòng `REVERT CMD:` trong file.
- **Payload**: JSON
  ```json
  {"filename": "Push_HCM-OOB_2026...log"}
  ```
- **Ràng buộc**: 
  - Trả về `400` nếu file push-log là push MÔ PHỎNG (`[MO PHONG]`) — không có gì để revert vì chưa từng gửi gì thật tới thiết bị.
  - Cũng tuân theo `push_live_mode` giống `action=push`: tắt thì chỉ mô phỏng revert, bật mới gửi lệnh thật.

### `[POST] /api/live-debug`
- **Chức năng**: Thực thi lệnh Verify chuyên sâu 1 Option cụ thể với kết nối trực tiếp (Live Debug) qua giao thức SSE.
- **Payload**: JSON
  ```json
  {"ip": "172.29.10.x", "opt_key": "10"}
  ```
- **Response**: Chứa `task_id` để kết nối vào luồng SSE.

---

## 5. API Lịch sử & Log (Logs & SSE)

### `[GET] /api/tasks`
- **Chức năng**: Trả về danh sách các tác vụ (background task) đang chạy hoặc vừa hoàn thành.

### `[GET] /api/events`
- **Chức năng**: Luồng Server-Sent Events (SSE) để theo dõi real-time console output của các tác vụ (scan, verify, push, revert, live-debug).

### `[GET] /api/logs` & `[GET] /api/logs/<path:fn>`
- **Chức năng**: Liệt kê 50 file log Verify mới nhất, và xem nội dung chi tiết của một file Verify Log.

### `[GET] /api/push-logs` & `[GET] /api/push-logs/<path:fn>`
- **Chức năng**: Liệt kê 50 file log Push mới nhất, và xem nội dung chi tiết của một file Push Log.

### `[GET] /api/debug-logs` & `[GET] /api/debug-logs/<path:fn>`
- **Chức năng**: Liệt kê 50 file log Live Debug mới nhất, và xem nội dung chi tiết của một file Debug Log.

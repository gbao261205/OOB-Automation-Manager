# OOB Network Manager

Công cụ **giám sát, xác minh vật lý và tự động phục hồi** menu OOB (Out-of-Band Console Server chạy Cisco IOS hoặc Vertiv ACS) qua SSH/Telnet. Phát hiện cấu hình menu bị thay đổi so với baseline, kiểm tra vật lý từng cổng console (Deep Verify — pivot vào từng cổng để xác nhận đúng thiết bị thật đang nối vào), tự động sửa lại mô tả (description) trên menu khi phát hiện sai lệch.

Gồm 3 file chính:
- `oob_lib.py` — thư viện kết nối (SSH ưu tiên qua `paramiko`, fallback Telnet tự viết), đọc/parse cấu hình `menu`, ghi (push) lại đúng 1 dòng mô tả khi cần sửa, và kiểm tra ping tới thiết bị.
- `oob_monitor.py` — chương trình **CLI**: menu quản lý, daemon giám sát tự động (2 luồng chạy nền có lịch riêng), Deep Verify / auto push, Import/Export Excel. Hỗ trợ song song 2 dòng thiết bị OOB: **Cisco IOS** (menu console) và **Vertiv ACS** (serial console server).
- `oob_web.py` — giao diện **Web Dashboard** (Flask), có đăng nhập (Admin/Guest), dùng lại các hàm trong `oob_monitor.py` nhưng **chạy như một tiến trình hoàn toàn tách biệt, không tự động lặp lại gì cả** — mọi hành động (Scan/Verify/Push) chỉ chạy khi có người bấm nút. Xem mục 8 để biết chi tiết Web đang thiếu gì so với CLI.

---

## 1. Yêu cầu hệ thống

- Python 3.9 trở lên (dùng cú pháp `str | None`).
- Truy cập mạng (SSH/Telnet) tới các thiết bị OOB cần giám sát.
- Quyền ghi thư mục chạy chương trình (để tạo file config, database SQLite, file trạng thái thiết bị, và các thư mục log).
- Nếu muốn dùng Web Dashboard (`oob_web.py`): mở thêm 1 cổng TCP (mặc định `5000`) để truy cập bằng trình duyệt, và máy chạy web cần ra được Internet tới `api.pwnedpasswords.com` (dùng để cảnh báo mật khẩu bị lộ khi đăng nhập/đổi mật khẩu — nếu không có mạng, tính năng này tự bỏ qua, không chặn đăng nhập).

## 2. Cài đặt thư viện

Thư viện ngoài cần cài đặt được liệt kê trong `requirements.txt`:

```
paramiko==3.5.1
rich>=13.7.0,<15.0.0
flask>=3.0.0,<4.0.0
werkzeug>=3.0.0
openpyxl>=3.1.0
```

Cài đặt bằng lệnh:

```bash
pip install -r requirements.txt
```

Nếu môi trường yêu cầu cờ `--break-system-packages` (một số bản Linux mới):

```bash
pip install -r requirements.txt --break-system-packages
```

Khuyến khích dùng **virtual environment** để tránh xung đột:

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
```

> - `paramiko` — SSH vào thiết bị (ưu tiên); nếu SSH thất bại, tự động rớt về Telnet (tự viết trong `oob_lib.py`, không cần gói nào thêm).
> - `rich` — vẽ giao diện console (bảng, khung, layout chia đôi màn hình, tự refresh log theo thời gian thực) cho `oob_monitor.py`.
> - `flask` + `werkzeug` — chạy Web Dashboard `oob_web.py` (routing, API JSON, session đăng nhập, hash mật khẩu). Không cần nếu bạn chỉ dùng CLI thuần túy.
> - `openpyxl` — đọc/ghi file Excel `.xlsx`. Dùng cho Import/Export ở CLI, và cho nút "Tải về Excel" ở Web.

## 3. Cấu trúc file khi chạy

Lần chạy đầu tiên, chương trình tự tạo các file/thư mục sau (không cần tạo tay trước):

| File / Thư mục               | Vai trò                                                              |
|------------------------------|----------------------------------------------------------------------|
| `oob_config.json`            | Cấu hình chính (tài khoản, mật khẩu Vertiv, cổng, chu kỳ, lịch chạy, đường dẫn...) |
| `oob_ips.txt`                | Danh sách IP + alias các thiết bị OOB cần giám sát                  |
| `baseline.db`                | SQLite — cấu hình "chuẩn" dùng để so sánh. **Đồng thời cũng chứa bảng `web_users`** (tài khoản đăng nhập Web) — do `oob_web.py` tạo/dùng chung file này |
| `snapshot.db`                | SQLite — cấu hình mới nhất vừa quét được                            |
| `device_status.json`         | Trạng thái ping/kết nối mới nhất của từng thiết bị (dùng chung bởi CLI và Web) |
| `verify-logs/`               | Log kết quả Deep Verify (mỗi lần quét 1 file `.log`)                |
| `push-logs/`                 | Log các lần tự động sửa mô tả (push) lên thiết bị                  |
| `reports/`                   | File báo cáo Excel xuất ra (CLI option `[e]` hoặc nút "Tải về Excel" trên Web) |
| `oob_import_template.xlsx`   | File mẫu Excel (tạo khi chọn xuất mẫu ở CLI option `[i]`)          |
| `daemon.pid`                 | File heartbeat — chỉ do `--daemon` (CLI) ghi; **Web không đọc/ghi file này** (xem mục 8.3) |
| `task_history.json`          | Do `oob_web.py` tự tạo — lưu lịch sử tối đa 50 tác vụ Scan/Verify/Push gần nhất chạy trên Web |

File `oob_ips.txt` — mỗi dòng 1 thiết bị, cách nhau bằng khoảng trắng, dòng bắt đầu bằng `#` bị bỏ qua:

```
172.29.10.36 OOB-HCM-01
172.29.10.40 OOB-HAN-02
```

> **Lưu ý:** Ngoài chỉnh tay `oob_ips.txt`, bạn có thể Import danh sách thiết bị từ file Excel qua option `[i]` trong Menu Quản Lý CLI (xem mục 6), hoặc thêm/xoá, Import file Excel trực tiếp trên Web Dashboard (xem mục 7).

## 4. Chạy chương trình (CLI)

```bash
python oob_monitor.py
```

Chương trình hỏi chọn 1 trong 3 chế độ:

```
1. Mo Menu Quan Ly (Them/Sua IP, Xem danh sach)
2. Mo Trinh Giam Sat (Chay log Daemon o terminal nay)
3. Mo CA HAI (Tu dong mo 2 cua so - Yeu cau Windows)
```

- **Chọn 1** → mở menu quản lý CLI (tương đương `python oob_monitor.py --menu`). Cửa sổ này **không** tự động chạy vòng lặp nào — chỉ dùng để thêm/xoá IP, đổi cấu hình, xem báo cáo, chạy quét/verify thủ công một lần.
- **Chọn 2** → chạy daemon (tương đương `python oob_monitor.py --daemon`) ngay trên terminal này. Daemon gồm **2 luồng chạy song song trong cùng 1 tiến trình**, cả hai đều tự động hoàn toàn (xem mục 5):
  - Luồng 1: quét/so sánh cấu hình `menu` định kỳ hoặc theo lịch cố định.
  - Luồng 2: Deep Verify vật lý + tự động push sửa mô tả, chạy theo lịch riêng.
  Log của cả 2 luồng hiển thị realtime, chia đôi màn hình (trên = Luồng 1, dưới = Luồng 2).
- **Chọn 3** → tự mở 2 cửa sổ terminal độc lập: 1 cửa sổ chạy `--daemon` (gồm cả 2 luồng tự động ở trên), 1 cửa sổ chạy `--menu` (chỉ để thao tác thủ công). **Chỉ hoạt động ổn định trên Windows.** Trên Linux/WSL, tự mở 2 tab và chạy tay:
  ```bash
  python oob_monitor.py --menu
  python oob_monitor.py --daemon
  ```

> **Lưu ý quan trọng:** dù bạn chọn 2 hay 3, tất cả tự động hoá (quét cấu hình, Deep Verify, auto push) đều nằm trong đúng 1 tiến trình `--daemon`. **Đây cũng là tiến trình DUY NHẤT trong toàn bộ hệ thống có khả năng tự lặp lại theo thời gian** — `oob_web.py` không có tiến trình tương đương (xem mục 8.1).

> 2 cửa sổ (khi chọn 3) là **2 tiến trình độc lập**, mỗi tiến trình tự đọc `oob_config.json` một lần lúc khởi động. Đổi cấu hình ở cửa sổ Menu sẽ ghi xuống file, nhưng cửa sổ Daemon đang chạy sẽ **không** tự nhận thay đổi đó cho tới khi được khởi động lại — trừ 2 mục lịch chạy (`[s]` và `[d]`, xem mục 5) và cờ `auto_verify`, các mục này daemon tự đọc lại cấu hình mới ở mỗi vòng lặp nên không cần khởi động lại.

Cũng có thể chỉ định file config khác qua tham số dòng lệnh:

```bash
python oob_monitor.py my_config.json --menu
```

## 5. Cấu hình lần đầu (`[3] Cấu hình`)

Vào **Menu Quản Lý → [3] Cấu hình** để thiết lập các thông số. Màn hình cài đặt được chia thành **4 nhóm rõ ràng**:

### Nhóm KẾT NỐI
| Mục | Ý nghĩa |
|-----|---------|
| `[1]` Username | Tài khoản đăng nhập thiết bị OOB |
| `[2]` Password | Mật khẩu |
| `[3]` Enable password | Mật khẩu Enable (Privilege EXEC, Cisco) |
| `[5]` SSH port | Cổng SSH (ưu tiên thử trước, mặc định 22) |
| `[6]` Telnet port | Cổng Telnet (dự phòng khi SSH thất bại, mặc định 23) |
| `[y]` Vertiv Connect Pass | Mật khẩu xác nhận khi pivot qua thiết bị Vertiv ACS (chỉ áp dụng cho thiết bị Vertiv) |

### Nhóm FILE DỮ LIỆU
| Mục | Ý nghĩa |
|-----|---------|
| `[8]` File danh sách IP | Đường dẫn file `oob_ips.txt` |
| `[9]` File baseline DB | Đường dẫn SQLite baseline |
| `[a]` File snapshot DB | Đường dẫn SQLite snapshot |

### Nhóm LUỒNG 1 — GIÁM SÁT CẤU HÌNH (daemon chạy liên tục)
Daemon kết nối định kỳ (hoặc theo lịch cố định) vào từng OOB, đọc toàn bộ cấu hình `menu` (Cisco) hoặc danh sách port (Vertiv ACS), so sánh với baseline đã lưu. Nếu khác → cảnh báo ngay.

| Mục | Ý nghĩa |
|-----|---------|
| `[4]` Tên menu | Ép dùng đúng 1 tên menu; để trống = tự động dò tất cả menu trên thiết bị |
| `[s]` Lịch chạy Thu thập | Chế độ lịch: `interval` (lặp theo chu kỳ) / `daily` (mỗi ngày 1 lần, đúng giờ cố định) / `weekly` (mỗi tuần 1 lần, đúng thứ + giờ cố định) |
| `[7]` Chu kỳ interval (s) | Số giây giữa các lần quét cấu hình — chỉ có hiệu lực khi `[s]` đang ở chế độ `interval`; bị bỏ qua hoàn toàn khi dùng `daily`/`weekly` |

Khi chọn `[s]`, chương trình hỏi lần lượt:

```
1. Lap lai theo chu ky (giay) - hanh vi mac dinh cu
2. Hang ngay, vao 1 gio co dinh (VD 01:00 = 1 gio sang)
3. Hang tuan, vao 1 thu + gio co dinh (VD Thu 2 luc 01:00)
```

- Chọn `2` → nhập giờ dạng `HH:MM`, daemon sẽ chỉ quét đúng 1 lần/ngày vào giờ đó.
- Chọn `3` → nhập thứ (`mon`/`tue`/`wed`/`thu`/`fri`/`sat`/`sun`) + giờ, daemon chỉ quét đúng 1 lần/tuần.
- Màn hình Cài đặt sẽ tự hiện dòng nhắc **"Không hiệu lực"** cạnh mục `[7]` khi bạn đang dùng lịch cố định.

### Nhóm LUỒNG 2 — VERIFY VẬT LÝ (Deep Verify — chạy theo lịch)
Daemon pivot vào từng port console, lấy hostname thực để kiểm tra description có đúng không. Chạy độc lập theo lịch riêng, không phụ thuộc Luồng 1.

- `[b]` = Bật/tắt chạy Verify ngầm tự động.
- `[v]`/`[d]` = tần suất/lịch **pivot vật lý** vào từng port console để lấy hostname thực.
- Đây là **2 luồng hoàn toàn độc lập**, chạy song song trên 2 thread khác nhau, mỗi luồng có lịch chạy riêng (interval/daily/weekly), không ảnh hưởng lẫn nhau.

> ⚠️ Cả `[s]` (lịch Luồng 1) và `[v]`/`[d]` (lịch Luồng 2) **chỉ có tác dụng khi tiến trình `--daemon` đang chạy**. Ghi các giá trị này qua Web (xem mục 7, tab "Lịch chạy") vẫn lưu được xuống `oob_config.json`, nhưng **không tự kích hoạt bất kỳ vòng lặp nào ở phía Web** — xem mục 8.1.

## 6. Các chức năng chính (Menu Quản Lý CLI)

```
[1] Them thiet bi OOB              - Them 1 IP + alias vao danh sach
[2] Xoa thiet bi OOB               - Xoa 1 IP khoi danh sach
[i] Import tu Excel                - Import nhieu thiet bi tu file .xlsx that
                                     (hoac xuat file mau de tham khao)

[3] Cau hinh                       - Sua username/password/port/lich...
[4] Xem danh sach thiet bi         - Liet ke tat ca IP dang giam sat
[5] Xem baseline (Chuan)           - Xem cau hinh menu "chuan" da luu
[6] Tim kiem thiet bi              - Tra cuu theo IP/alias/IP dich
[7] Quet kiem tra Cau hinh tuc thi - Doi chieu cau hinh hien tai vs baseline
[8] Deep Verify Vat ly tuc thi     - Pivot vao tung cong, xac nhan dung thiet bi
[9] Xem ket qua Verify gan nhat    - Hien thi file log Verify gan nhat
[e] Xuat bao cao menu OOB ra Excel - Xuat file .xlsx bao cao tat ca thiet bi

[0] Thoat
```

Màn hình chính của Menu Quản Lý luôn hiển thị **trạng thái Daemon** (`RUNNING` — kèm số giây từ lần cập nhật cuối, `STALE` nếu daemon treo/chết, hoặc `KHONG RO` nếu chưa từng chạy `--daemon` lần nào), dựa trên file heartbeat `daemon.pid`.

---

### `[i]` Import danh sách thiết bị từ Excel (CLI)

Import hàng loạt từ **file Excel `.xlsx` thật** (chọn đường dẫn file trên đĩa).

| Cột A (IP)    | Cột B (Alias / Tên gọi) |
|---------------|--------------------------|
| 192.168.1.1   | OOB-HCM-01               |
| 10.0.0.2      | OOB-HAN-02               |

- Hàng đầu tiên là header, dữ liệu bắt đầu từ hàng 2.
- Nếu cột B để trống → dùng IP làm alias.
- Tự động bỏ qua IP đã tồn tại (không trùng lặp) và IP không hợp lệ.
- In tóm tắt: *Đã thêm X / Bỏ qua Y (trùng) / Bỏ qua Z (IP không hợp lệ)*.
- Có thể xuất trước file mẫu `oob_import_template.xlsx` để điền theo đúng format.

### `[e]` Xuất báo cáo menu OOB ra Excel (CLI)

Xuất toàn bộ dữ liệu menu ra `reports/OOB_Menu_Report_YYYYMMDD_HHMMSS.xlsx`, gồm 3 sheet **"Chi tiet"** / **"Tom tat"** / **"Canh bao"** (mỗi sheet có freeze header + auto-filter). Không cần kết nối thiết bị — đọc từ baseline DB + log verify sẵn có.

### `[7]` Quét kiểm tra cấu hình / `[8]` Deep Verify vật lý (CLI)

Giống hệt logic mô tả ở mục 7 (Web) — cả 2 nơi dùng chung hàm trong `oob_monitor.py`/`oob_lib.py`. Khác biệt duy nhất: khi chạy tay ở CLI, bước xác nhận ghi baseline mới hoặc push sửa mô tả **luôn hỏi `y/n` ngay trên terminal**; khi chạy tự động trong `--daemon`, hành vi tuân theo cờ `auto_push_desc`.

**Nguyên tắc an toàn của tính năng push (áp dụng cho cả CLI và Web):**
- Chỉ sửa đúng 1 dòng `menu <tên> text <key> <mô tả mới>` của option đang sai lệch.
- Không bao giờ tự chạy `write memory` — chỉ sửa running-config.
- Luôn ghi log vào `push-logs/` và verify lại sau khi sửa.
- **Thiết bị Vertiv ACS chưa được hỗ trợ Push** — Deep Verify vẫn chạy và báo cáo bình thường trên Vertiv, nhưng chương trình chỉ cảnh báo chứ không tự sửa.

## 7. Web Dashboard (`oob_web.py`) — những gì ĐANG CÓ

```bash
python oob_web.py
```

Mặc định chạy tại `http://127.0.0.1:5000` (lắng nghe `0.0.0.0:5000`, truy cập được từ máy khác trong mạng qua IP máy chạy). `oob_web.py` **dùng lại trực tiếp** các hàm trong `oob_monitor.py` (đọc cùng `oob_config.json`, `oob_ips.txt`, `baseline.db`, `snapshot.db`, `device_status.json`) — **không cần** bật `--daemon` trước, các nút Scan/Verify/Push trên web tự kết nối thiết bị và chạy nền (background thread) ngay khi bấm.

### Đăng nhập & phân quyền

- Tài khoản mặc định lần đầu: **`admin` / `admin`** (tự tạo trong bảng `web_users` bên trong `baseline.db`) — **đổi ngay** qua "Đổi mật khẩu" sau khi cài đặt xong.
- **Guest (chưa đăng nhập):** chỉ xem — Dashboard, danh sách thiết bị, chi tiết 1 thiết bị, tìm kiếm, tải báo cáo Excel.
- **Admin (đã đăng nhập):** thêm tất cả các thao tác thay đổi dữ liệu — Scan/ Verify/Push, thêm/xóa thiết bị, sửa cấu hình, quản lý tài khoản phụ, Import, Revert.
- Khi đăng nhập hoặc đổi mật khẩu, hệ thống tự kiểm tra mật khẩu có từng bị lộ qua API "Have I Been Pwned" (`api.pwnedpasswords.com`) và cảnh báo nếu có.

### Trang Dashboard

- Bảng thống kê nhanh: tổng số thiết bị, số Online/Offline, số đã có baseline, số đang có cảnh báo (`CANH BAO`) trong 7 ngày gần nhất.
- Bảng danh sách toàn bộ thiết bị: Alias, IP, trạng thái Ping, trạng thái Menu, số option baseline, số lần OK/cảnh báo verify, thời điểm cập nhật.
- Nút thao tác riêng từng dòng: xem chi tiết, Scan, Verify, Push, Xóa (chỉ Admin thấy nút hành động).
- Nút **"Thêm OOB"** — thêm 1 thiết bị (IP + alias) ngay trên web.
- Ô tìm kiếm — tra theo hostname thật/alias/IP/description, kết quả xếp hạng độ khớp, có nút "Đi tới" nhảy sang trang chi tiết.
- Live Console (SSE) — xem log realtime của các tác vụ Scan/Verify/Push đang chạy, không cần refresh trang.

### Trang chi tiết 1 thiết bị (`/device/<ip>`)

Hostname, hãng sản xuất (Cisco/Vertiv), toàn bộ option/port trong baseline (phím menu, description, IP đích, port đích, giao thức), trạng thái verify từng dòng, và 3 nút Scan/Verify/Push riêng cho thiết bị này.

### Trang "Vận hành Tức thì" (chỉ Admin)

- **Lưu ý an toàn:** Tính năng chạy hàng loạt (all) cho toàn bộ danh sách thiết bị đã được lược bỏ hoàn toàn.
- Khu vực thao tác "Chạy cho thiết bị cụ thể": Nhập IP hoặc Alias để chạy lệnh Scan/Verify/Push cho duy nhất thiết bị đó.
- Khu vực Live Console.

### Trang Logs (chỉ Admin)

- Danh sách `verify-logs/` và `push-logs/` (tối đa 50 file mới nhất mỗi loại), xem nội dung log ngay trên web.
- Nút **Revert**: nếu 1 file push-log chứa dòng `REVERT CMD`, cho phép chạy lại đúng các lệnh đó để phục hồi mô tả cũ (dùng credential đầu tiên trong danh sách tài khoản).

### Trang Import / Export

- **Export:** tải file Excel `.xlsx` báo cáo toàn bộ baseline + trạng thái ping + kết quả verify (route `/api/export/excel`) — ai cũng xem/tải được, kể cả Guest.
- **Import:** Hỗ trợ upload trực tiếp file Excel `.xlsx` tương tự như CLI (Cột A = IP, Cột B = Alias) và tự động xử lý.

### Trang Settings (chỉ Admin) — 4 tab

- **Kết nối:** username/password/enable password/Vertiv Connect Password, SSH/Telnet port, tên menu ép dùng, toggle "Tự động Verify ngầm".
- **Multi-Account:** thêm/xóa tài khoản phụ (dùng lần lượt khi tài khoản chính thất bại) — dùng chung với CLI.
- **Lịch chạy:** chỉnh `interval`/`daily`/`weekly` và giờ/thứ cho cả 2 luồng Scan & Verify — **các giá trị này ghi xuống `oob_config.json` nhưng bản thân web không có gì đọc/thực thi lịch đó** (xem mục 8.1).
- **Files:** đường dẫn `oob_ips.txt`, `baseline.db`, `snapshot.db`.

### API nội bộ (có thể gọi trực tiếp nếu cần tích hợp)

| Endpoint | Method | Quyền | Chức năng |
|---|---|---|---|
| `/api/stats` | GET | Guest | Số liệu tổng quan |
| `/api/devices` | GET | Guest | Danh sách thiết bị + trạng thái |
| `/api/device/<ip>/options` | GET | Guest | Chi tiết option của 1 thiết bị |
| `/api/search` | GET (`?q=`) | Guest | Tìm kiếm, trả JSON xếp hạng |
| `/api/logs`, `/api/logs/<fn>` | GET | Guest | Danh sách + nội dung verify-logs |
| `/api/push-logs`, `/api/push-logs/<fn>` | GET | Guest | Danh sách + nội dung push-logs |
| `/api/export/excel` | GET | Guest | Tải báo cáo Excel |
| `/api/events` | GET (SSE) | Guest | Stream log realtime |
| `/api/tasks` | GET | Guest | Lịch sử/trạng thái task |
| `/api/config` | GET / POST | Admin | Đọc / cập nhật `oob_config.json` |
| `/api/credentials` | GET/POST/DELETE | Admin | Quản lý tài khoản phụ |
| `/api/device` | POST / DELETE | Admin | Thêm / xoá thiết bị |
| `/api/action` | POST | Admin | Chạy `scan`/`verify`/`push` nền (chỉ định 1 IP) |
| `/api/revert` | POST | Admin | Chạy lại lệnh REVERT từ 1 push-log |
| `/api/import` | POST | Admin | Import danh sách IP từ file `.xlsx` |
| `/api/change-password` | POST | Admin | Đổi mật khẩu tài khoản đang đăng nhập |

> **Lưu ý bảo mật:** mật khẩu thiết bị (Enable password, Vertiv Connect Password…) hiển thị **nguyên văn** trong form Settings khi Admin mở trang (không mask lại khi load). `SECRET_KEY` của Flask session mặc định là `os.urandom(24)` — nghĩa là **mỗi lần restart `oob_web.py`, mọi session đang đăng nhập bị đăng xuất**, trừ khi bạn tự set biến môi trường `FLASK_SECRET_KEY` cố định. Chỉ nên chạy trong mạng nội bộ tin cậy, không nên expose ra Internet mà không tự thêm lớp bảo vệ (reverse proxy + HTTPS, VPN, firewall...).

## 8. Web Dashboard đang THIẾU gì so với CLI

Đây là phần liệt kê rõ ràng để tránh hiểu nhầm "chạy web là đủ thay cho daemon".

### 8.1. ❌ Không có vòng lặp tự động / lập lịch chạy nền (quan trọng nhất)

`oob_web.py` **không khởi động `run_daemon()` hay bất kỳ thread lặp `while True` theo thời gian nào**. Mọi Scan/Verify/Push trên web đều là **on-demand**: chỉ chạy đúng 1 lần tại thời điểm người dùng bấm nút, rồi dừng hẳn.

Hệ quả:
- Tab Settings → "Lịch chạy" cho phép chỉnh `interval`/`daily`/`weekly` và lưu xuống `oob_config.json` — **nhưng các giá trị này chỉ có ý nghĩa nếu có một tiến trình `python oob_monitor.py --daemon` chạy song song và đọc cùng file config đó.** Nếu bạn chỉ chạy `oob_web.py` một mình (không có `--daemon` nào chạy nền), thì dù có set lịch "mỗi ngày 1:00 sáng" trên web, **sẽ không có gì tự chạy lúc 1:00 sáng cả** — đây chính là điều bạn nhắc tới: "không có chức năng lặp lịch tự động đi thu thập thông tin".
- Muốn có giám sát tự động thật sự (tự quét/tự verify theo lịch, kể cả khi không ai mở trình duyệt), **bắt buộc phải chạy thêm** `python oob_monitor.py --daemon` như một tiến trình nền riêng (systemd service, Task Scheduler, tmux, Docker container, v.v.) — Web chỉ là lớp giao diện thao tác/xem, không thay thế được daemon.

### 8.2. ❌ Không có trạng thái Daemon thật (`daemon.pid`) trên giao diện Web

- CLI: Menu Quản Lý đọc file `daemon.pid` (do `--daemon` ghi heartbeat mỗi 30s) để hiển thị chính xác `RUNNING`/`STALE`/`KHONG RO`.
- Web: chấm tròn "Web server hoạt động" / "N task đang chạy" ở góc trên chỉ đếm số task **do chính web tạo ra** (`task_history.json`), **không đọc `daemon.pid`**. Nghĩa là dù `--daemon` CLI có đang chạy nền thật hay không, Web không hề biết và không hiển thị đúng trạng thái đó.

### 8.3. ❌ Không tự mở 2 cửa sổ / không có chế độ "CA HAI" như CLI option 3

Web chỉ là 1 tiến trình `python oob_web.py` duy nhất — không có khái niệm mở song song 2 cửa sổ Menu + Daemon như CLI.

### 8.4. ❌ Không có sửa (edit) thiết bị đã thêm, chỉ có Thêm / Xóa

`/api/device` chỉ hỗ trợ `POST` (thêm) và `DELETE` (xóa). Muốn đổi alias của 1 IP đã có, phải xóa rồi thêm lại (hoặc sửa tay `oob_ips.txt` / dùng CLI).

### 8.5. Bảng tổng hợp nhanh

| Tính năng | CLI (`oob_monitor.py`) | Web (`oob_web.py`) |
|---|---|---|
| Scan cấu hình (1 lần, thủ công) | ✅ `[7]`, 1 hoặc nhiều IP | ✅ nút Scan, chỉ định 1 IP |
| Deep Verify (1 lần, thủ công) | ✅ `[8]` | ✅ nút Verify |
| Push sửa mô tả (1 lần, thủ công) | ✅ theo sau `[8]`, hỏi xác nhận | ✅ nút Push, không hỏi xác nhận trên web, chỉ định 1 IP |
| **Tự động lặp lại theo chu kỳ/lịch (không cần người bấm)** | ✅ `--daemon`, 2 luồng độc lập | ❌ **không có** |
| Trạng thái Daemon thật (`daemon.pid`) | ✅ | ❌ không đọc file này |
| Toggle "Tự động Verify ngầm" có tác dụng | ✅ Đã khắc phục | ✅ Đã khắc phục |
| Import Excel — đọc file `.xlsx` thật | ✅ | ✅ |
| Export Excel | ✅ `[e]`, 3 sheet (Chi tiết/Tóm tắt/Cảnh báo) | ✅ 1 sheet "Chi tiet OOB" |
| Xem lịch sử log Verify/Push | ✅ `[9]` (Verify gần nhất) | ✅ xem được nhiều file, cả 2 loại log |
| Revert theo log Push | ❌ không có sẵn trong menu CLI | ✅ có nút Revert |
| Sửa alias thiết bị đã thêm | có thể xóa/thêm lại | ❌ chỉ Thêm/Xóa |
| Đăng nhập / phân quyền Guest-Admin | không áp dụng (CLI chạy local) | ✅ có |
| Multi-account (tài khoản dự phòng) | ✅ | ✅ |
| Push cho thiết bị Vertiv | ❌ chưa hỗ trợ | ❌ chưa hỗ trợ (giống CLI) |

## 9. Log

| File | Nội dung |
|------|----------|
| `verify-logs/Verify_<alias>_<timestamp>.log` | Báo cáo Deep Verify dạng bảng, nhóm theo mức độ nghiêm trọng (CANH BAO lên đầu) |
| `push-logs/Push_<alias>_<timestamp>.log` | Lịch sử các lần tự động sửa mô tả (cũ → mới, kèm lệnh revert nếu cần sửa tay lại) |
| `reports/*.xlsx` | Báo cáo Excel xuất từ CLI `[e]` hoặc nút "Tải về Excel" trên Web |

Xem nhanh log Verify gần nhất ngay trong menu CLI qua mục **[9]**, hoặc xem đầy đủ danh sách log (cả Verify và Push) trong trang **Logs** của Web.

## 10. Dừng chương trình

- CLI, chế độ Menu: chọn **[0] Thoát**.
- CLI, chế độ Daemon: `Ctrl+C`.
- Web Dashboard: `Ctrl+C` trong terminal đang chạy `oob_web.py`.

## 11. Luồng hoạt động chuẩn (khuyến nghị)

### 11.1. Thiết lập lần đầu (làm 1 lần)

1. Cài thư viện (`pip install -r requirements.txt`).
2. Chạy `python oob_monitor.py --menu` → `[3] Cấu hình` → nhập username/password/ enable password/port SSH-Telnet. Nếu có thiết bị Vertiv, nhập thêm Vertiv Connect Password.
3. Thêm danh sách thiết bị: gõ tay từng cái bằng `[1]`, hoặc import hàng loạt bằng `[i]` (file Excel thật), hoặc sửa trực tiếp `oob_ips.txt`.
4. Đặt lịch chạy cho 2 luồng (mục `[s]` và `[v]`/`[d]` trong Cấu hình) theo nhu cầu thực tế — ví dụ Luồng 1 (Scan cấu hình) `interval` mỗi 30–60s để bắt lỗi cấu hình sớm, Luồng 2 (Deep Verify vật lý) `daily` lúc 1:00 sáng vì đây là thao tác pivot nặng hơn, không cần chạy dày.
5. Chạy `[7]` (Scan) 1 lần thủ công để tạo **baseline lần đầu** cho tất cả thiết bị — xác nhận `y` khi được hỏi "lưu làm baseline".
6. (Tuỳ chọn) Chạy `[8]` (Deep Verify) 1 lần để có dữ liệu verify ban đầu, phục vụ cho báo cáo/trang Web ngay từ đầu thay vì phải chờ tới lịch chạy đầu tiên.

### 11.2. Vận hành hàng ngày — tự động hoá thật sự

7. Khởi động và **giữ chạy liên tục** `python oob_monitor.py --daemon` (nên chạy dưới dạng service/systemd/Task Scheduler/tmux để không bị tắt khi đóng terminal hoặc mất kết nối SSH tới máy chủ). Đây là tiến trình duy nhất tự lặp lại theo lịch đã đặt ở bước 4 — nếu không có tiến trình này chạy nền, sẽ **không có gì tự động xảy ra**, bất kể bạn có mở Web hay không.
8. (Tuỳ chọn, không bắt buộc) Chạy thêm `python oob_web.py` song song **chỉ để xem** dashboard/log/báo cáo qua trình duyệt, hoặc để thao tác thủ công khi cần gấp (ví dụ vừa thay dây console, muốn Verify ngay 1 thiết bị mà không chờ tới lịch tiếp theo của daemon).
9. Khi `--daemon` phát hiện `CANH BAO` (Deep Verify sai lệch) và `auto_push_desc` đang bật → daemon tự sửa mô tả, ghi log vào `push-logs/`, tự verify lại. Nếu tắt `auto_push_desc`, daemon chỉ cảnh báo — bạn cần vào CLI `[8]` hoặc bấm nút Push trên Web để tự xác nhận sửa.
10. Định kỳ (tuần/tháng) vào CLI `[e]` hoặc Web → Import/Export → "Tải về Excel" để lưu báo cáo tổng hợp, đối chiếu với đội vận hành.

### 11.3. Nếu chỉ muốn dùng Web (không chạy `--daemon`)

Vẫn hoạt động được, nhưng cần hiểu rõ giới hạn: **không có gì tự chạy nền**. Quy trình sẽ là thao tác thủ công định kỳ do con người thực hiện:

11. Người trực chủ động mở Web, thực hiện **Scan CONFIG** cho từng thiết bị theo lịch làm việc thực tế của mình (ví dụ đầu giờ mỗi ca trực).
12. Bấm **DEEP VERIFY** khi cần xác minh vật lý (sau khi đấu lại dây, sau bảo trì phòng máy, hoặc theo lịch kiểm tra định kỳ tự quy định bằng tay).
13. Khi thấy `CANH BAO`, bấm **PUSH CONFIG** cho thiết bị đó để tự sửa mô tả sai lệch — nút Push trên Web **không hỏi xác nhận lại**, sửa ngay khi bấm (tính năng tự động chạy tất cả đã bị khóa).
14. Nếu sửa nhầm hoặc cần khôi phục mô tả cũ, vào trang **Logs → push-logs**, mở log lần Push liên quan, bấm **Revert**.

> Cách dùng này phù hợp cho môi trường có người trực theo dõi thường xuyên; nếu cần giám sát 24/7 không phụ thuộc con người, bắt buộc quay lại mục 11.2 (chạy `--daemon`).

## 12. Giới hạn hiện tại (áp dụng chung, ngoài mục 8)

- **Thiết bị Vertiv ACS chưa hỗ trợ tính năng Push Config** ở cả CLI lẫn Web. Deep Verify vẫn chạy và báo cáo `CANH BAO` bình thường trên Vertiv, nhưng không tự sửa description cho thiết bị loại này.
- Lịch `daily`/`weekly` (cả `[s]` và `[d]`, dù đặt qua CLI hay Web) hiện chỉ hỗ trợ mốc **giờ cố định trong ngày, hoặc thứ + giờ cố định trong tuần**. Chưa hỗ trợ lịch theo ngày cụ thể trong tháng (VD "ngày 15 hàng tháng") hay một mốc ngày/tháng/năm duy nhất (chạy 1 lần rồi thôi).
- 2 tiến trình `--menu` và `--daemon` (khi chạy chế độ 3) không chia sẻ bộ nhớ — đổi cấu hình ở cửa sổ Menu chỉ có hiệu lực ngay với 2 mục lịch chạy (`[s]`, `[d]`), các mục còn lại cần khởi động lại `--daemon` mới nhận.
- `oob_web.py` là 1 tiến trình hoàn toàn riêng biệt với `--daemon`/`--menu` — cũng không chia sẻ bộ nhớ, chỉ chia sẻ file config/DB trên đĩa. Đổi cấu hình trên Web ghi xuống `oob_config.json` như CLI, áp dụng đúng quy tắc trên nếu có `--daemon` đang chạy song song.
- `oob_web.py` mặc định tự sinh `SECRET_KEY` ngẫu nhiên mỗi lần khởi động (mất session khi restart) và hiển thị mật khẩu thiết bị dạng chữ thường trong form Settings — xem lưu ý bảo mật ở mục 7.

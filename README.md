# OOB Network Manager

Công cụ **giám sát, xác minh vật lý và tự động phục hồi** menu OOB (Out-of-Band
Console Server chạy Cisco IOS) qua SSH/Telnet. Phát hiện cấu hình menu bị thay
đổi so với baseline, kiểm tra vật lý từng cổng console (Deep Verify — pivot vào
từng cổng để xác nhận đúng thiết bị thật đang nối vào), và tự động sửa lại mô tả
(description) trên menu khi phát hiện sai lệch.

Gồm 2 file chính:
- `oob_lib.py` — thư viện kết nối (SSH ưu tiên qua `paramiko`, fallback Telnet tự
  viết), đọc/parse cấu hình `menu`, và ghi (push) lại đúng 1 dòng mô tả khi cần sửa.
- `oob_monitor.py` — chương trình chính: daemon giám sát, menu quản lý CLI, Deep
  Verify / auto push, Import/Export Excel.

---

## 1. Yêu cầu hệ thống

- Python 3.9 trở lên (dùng cú pháp `str | None`).
- Truy cập mạng (SSH/Telnet) tới các thiết bị OOB cần giám sát.
- Quyền ghi thư mục chạy chương trình (để tạo file config, database SQLite, và các
  thư mục log).

## 2. Cài đặt thư viện

Thư viện ngoài cần cài đặt được liệt kê trong `requirements.txt`:

```
paramiko==3.5.1
rich>=13.7.0,<15.0.0
openpyxl>=3.1.0          # cần cho Import/Export Excel
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

> - `paramiko` — SSH vào thiết bị (ưu tiên); nếu SSH thất bại, tự động rớt về
>   Telnet (tự viết trong `oob_lib.py`, không cần gói nào thêm).
> - `rich` — vẽ giao diện console (bảng, khung, layout chia đôi màn hình).
> - `openpyxl` — đọc/ghi file Excel `.xlsx` cho tính năng Import và Export báo cáo.
>   Nếu chưa cài, 3 hàm Import/Export vẫn chạy nhưng sẽ thông báo lỗi và thoát.

## 3. Cấu trúc file khi chạy

Lần chạy đầu tiên, chương trình tự tạo các file/thư mục sau (không cần tạo tay trước):

| File / Thư mục               | Vai trò                                                              |
|------------------------------|----------------------------------------------------------------------|
| `oob_config.json`            | Cấu hình chính (tài khoản, cổng, chu kỳ, đường dẫn...)              |
| `oob_ips.txt`                | Danh sách IP + alias các thiết bị OOB cần giám sát                  |
| `baseline.db`                | SQLite — cấu hình "chuẩn" dùng để so sánh                           |
| `snapshot.db`                | SQLite — cấu hình mới nhất vừa quét được                            |
| `verify-logs/`               | Log kết quả Deep Verify (mỗi lần quét 1 file `.log`)                |
| `push-logs/`                 | Log các lần tự động sửa mô tả (push) lên thiết bị                  |
| `reports/`                   | File báo cáo Excel xuất ra từ tính năng Export (option `[e]`)        |
| `oob_import_template.xlsx`   | File mẫu Excel (tạo khi chọn xuất mẫu ở option `[i]`)              |

File `oob_ips.txt` — mỗi dòng 1 thiết bị, cách nhau bằng khoảng trắng, dòng bắt
đầu bằng `#` bị bỏ qua:

```
172.29.10.36 OOB-HCM-01
172.29.10.40 OOB-HAN-02
```

> **Mới:** Thay vì nhập tay hoặc sửa file `oob_ips.txt`, bạn có thể Import danh sách
> thiết bị từ file Excel qua option `[i]` trong Menu Quản Lý (xem mục 6).

## 4. Chạy chương trình

```bash
python oob_monitor.py
```

Chương trình hỏi chọn 1 trong 3 chế độ:

```
1. Mo Menu Quan Ly (Them/Sua IP, Xem danh sach)
2. Mo Trinh Giam Sat (Chay log Daemon o terminal nay)
3. Mo CA HAI (Tu dong mo 2 cua so - Yeu cau Windows)
```

- **Chọn 1** → mở menu quản lý CLI (tương đương `python oob_monitor.py --menu`).
- **Chọn 2** → chạy vòng lặp giám sát nền (quét cấu hình liên tục + Deep Verify +
  auto push định kỳ), hiển thị log trực tiếp trên terminal này (tương đương
  `python oob_monitor.py --daemon`).
- **Chọn 3** → tự mở 2 cửa sổ terminal độc lập (1 chạy daemon, 1 chạy menu).
  **Chỉ hoạt động ổn định trên Windows.** Trên Linux/WSL, tự mở 2 tab và chạy tay:
  ```bash
  python oob_monitor.py --menu
  python oob_monitor.py --daemon
  ```

> Lưu ý: 2 cửa sổ này là **2 tiến trình độc lập**, mỗi tiến trình tự đọc
> `oob_config.json` một lần lúc khởi động. Đổi cấu hình ở cửa sổ Menu sẽ ghi xuống
> file, nhưng cửa sổ Daemon đang chạy sẽ **không** tự nhận thay đổi đó cho tới khi
> được khởi động lại.

Cũng có thể chỉ định file config khác qua tham số dòng lệnh:

```bash
python oob_monitor.py my_config.json --menu
```

## 5. Cấu hình lần đầu (`[3] Cấu hình`)

Vào **Menu Quản Lý → [3] Cấu hình** để thiết lập các thông số. Màn hình cài đặt
được chia thành **4 nhóm rõ ràng**:

### Nhóm KẾT NỐI
| Mục | Ý nghĩa |
|-----|---------|
| `[1]` Username | Tài khoản đăng nhập thiết bị OOB |
| `[2]` Password | Mật khẩu |
| `[3]` Enable password | Mật khẩu Enable (Privilege EXEC) |
| `[5]` SSH port | Cổng SSH (ưu tiên thử trước, mặc định 22) |
| `[6]` Telnet port | Cổng Telnet (dự phòng khi SSH thất bại, mặc định 23) |

### Nhóm FILE DỮ LIỆU
| Mục | Ý nghĩa |
|-----|---------|
| `[8]` File danh sách IP | Đường dẫn file `oob_ips.txt` |
| `[9]` File baseline DB | Đường dẫn SQLite baseline |
| `[a]` File snapshot DB | Đường dẫn SQLite snapshot |

### Nhóm LUỒNG 1 — GIÁM SÁT CẤU HÌNH (daemon chạy liên tục)
Daemon kết nối định kỳ vào từng OOB, đọc toàn bộ cấu hình `menu`, so sánh với
baseline đã lưu. Nếu khác → cảnh báo ngay.

| Mục | Ý nghĩa |
|-----|---------|
| `[4]` Tên menu | Ép dùng đúng 1 tên menu; để trống = tự động dò tất cả menu trên thiết bị |
| `[7]` Chu kỳ đọc cấu hình (s) | Số giây giữa các lần quét cấu hình (mặc định 30s) |

### Nhóm LUỒNG 2 — VERIFY VẬT LÝ (Deep Verify — chạy theo lịch)
Daemon pivot vào từng port console, lấy hostname thực để kiểm tra description có
đúng không. Chạy độc lập theo lịch, không phụ thuộc Luồng 1.

| Mục | Ý nghĩa |
|-----|---------|
| `[b]` Tự động Verify ngầm | Bật/tắt Deep Verify tự động trong Daemon |
| `[c]` Tự động Sửa lỗi ngầm | Bật/tắt tự động push sửa description khi phát hiện sai lệch |
| `[d]` Lịch chạy Verify | Chế độ lịch: `interval` (lặp theo chu kỳ) / `daily` (mỗi ngày 1 lần) / `weekly` (mỗi tuần 1 lần) |
| `[v]` Chu kỳ interval (s) | Số giây giữa các lần Verify **khi `[d]` đang ở chế độ `interval`**; bị bỏ qua hoàn toàn khi dùng `daily`/`weekly` |

> **Cách hiểu đúng về `[7]` và `[v]`:**
> - `[7]` = tần suất **đọc cấu hình** (so sánh text menu config với baseline).
> - `[v]` = tần suất **pivot vật lý** vào từng port console để lấy hostname thực.
> - Đây là **2 luồng hoàn toàn độc lập**, chạy song song trên 2 thread khác nhau.

## 6. Các chức năng chính (Menu Quản Lý)

```
[1] Them thiet bi OOB              - Them 1 IP + alias vao danh sach
[2] Xoa thiet bi OOB               - Xoa 1 IP khoi danh sach
[i] Import tu Excel                - Import nhieu thiet bi tu file .xlsx
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

---

### `[i]` Import danh sách thiết bị từ Excel *(Mới)*

Thay vì nhập thủ công hoặc sửa file `oob_ips.txt`, bạn có thể import hàng loạt từ
file Excel `.xlsx`.

**Định dạng file Excel:**

| Cột A (IP)    | Cột B (Alias / Tên gọi) |
|---------------|--------------------------|
| 192.168.1.1   | OOB-HCM-01               |
| 10.0.0.2      | OOB-HAN-02               |

- Hàng đầu tiên là header, dữ liệu bắt đầu từ hàng 2.
- Nếu cột B để trống → dùng IP làm alias.
- Tự động bỏ qua IP đã tồn tại trong danh sách (không trùng lặp).
- Tự động bỏ qua các dòng có IP không hợp lệ (không phải định dạng `x.x.x.x`).
- In tóm tắt: *Đã thêm X / Bỏ qua Y (trùng) / Bỏ qua Z (IP không hợp lệ)*.

Chọn `[i]` → chương trình hỏi trước: **"Xuất file mẫu trước?"** — nếu chọn `y`, sẽ
tạo file `oob_import_template.xlsx` với header đúng định dạng để điền vào.

Yêu cầu: `pip install openpyxl`

---

### `[e]` Xuất báo cáo menu OOB ra Excel *(Mới)*

Xuất toàn bộ dữ liệu menu của tất cả OOB ra file
`reports/OOB_Menu_Report_YYYYMMDD_HHMMSS.xlsx`.

**Không cần kết nối thiết bị** — đọc từ baseline DB và file log verify đã có sẵn.

File Excel gồm **2 sheet**:

#### Sheet "Chi tiet"
Mỗi dòng = 1 option trên menu OOB. Các cột:

| Cột | Nội dung |
|-----|----------|
| OOB IP | IP của thiết bị OOB |
| OOB Alias | Tên gọi |
| OOB Hostname | Hostname thực tế (từ DB) |
| Menu Name | Tên menu (VD: `OOB_MENU`) |
| Option Key | Phím chọn (VD: `[1]`, `2`, `KTHT`) |
| Description | Nội dung text hiển thị trên menu |
| Target IP | IP đích kết nối |
| Target Port | Port đích |
| Protocol | `telnet` / `ssh` |
| **Desc Status** | Kết quả kiểm tra mô tả (xem bảng màu bên dưới) |
| Ghi chú | Chi tiết thêm về trạng thái |

**Màu sắc cột Desc Status:**

| Màu | Giá trị | Ý nghĩa |
|-----|---------|---------|
| 🟢 Xanh lá | `OK - Khop` | Hostname verify được khớp với description |
| 🔴 Đỏ nhạt | `SAI - Sai desc` | Hostname thực tế không khớp description |
| 🟡 Vàng | `Chua Verify` | Chưa có dữ liệu verify (chưa chạy Deep Verify lần nào) |
| 🟠 Cam nhạt | `Khong ket noi duoc` | Verify đã chạy nhưng TIMEOUT hoặc không pivot được |
| ⬜ Xám | `Khong co desc` | Ô description trống (không thể kiểm tra) |

#### Sheet "Tom tat"
Mỗi dòng = 1 OOB. Hiển thị tổng số option / số Khớp / số Sai / số Chưa verify,
v.v. Tiện để nhìn tổng quan nhanh tình trạng toàn hệ thống.

Cả 2 sheet đều có **header freeze** (hàng đầu cố định khi cuộn) và **auto-filter**
để lọc/sắp xếp dễ dàng trong Excel.

Yêu cầu: `pip install openpyxl`

---

### `[7]` Quét kiểm tra cấu hình

Kết nối vào thiết bị OOB, đọc lại toàn bộ `menu`, so sánh với baseline đã lưu. Nếu
khác, hiển thị bảng so sánh và hỏi xác nhận trước khi ghi đè baseline. Nếu thiết bị
chưa có baseline, hỏi xác nhận để lưu lần đầu.

Có thể chỉ định 1/nhiều IP hoặc alias (cách nhau dấu phẩy), để trống để quét tất cả.

### `[8]` Deep Verify vật lý

Với từng option trong menu, kết nối pivot (telnet/ssh) từ chính OOB sang thiết bị
đích, đọc hostname thật của thiết bị đó, và so sánh với mô tả đang khai báo trên menu.

Kết quả mỗi option là một trong các trạng thái:

| Trạng thái | Ý nghĩa |
|---|---|
| `OK` | Hostname thật khớp với mô tả trên menu |
| `CANH BAO` | Sai lệch — hostname thật khác mô tả trên menu |
| `KHONG PIVOT` | Chưa pivot sang được thiết bị đích (vẫn ở console của chính OOB) |
| `TIMEOUT` | Không kết nối được / không có phản hồi |
| `YEU CAU DANG NHAP` | Thiết bị đích yêu cầu đăng nhập, không xác minh được hostname |

Nếu phát hiện `CANH BAO`, chương trình hỏi có muốn tự động **PUSH** sửa lại mô tả
trên menu OOB cho khớp hostname thật hay không.

Khi chạy tự động theo chu kỳ trong Daemon, việc push tuân theo cấu hình
`auto_push_desc` (`[c]` trong Settings), không hỏi xác nhận.

**Nguyên tắc an toàn của tính năng push:**
- Chỉ sửa đúng 1 dòng `menu <tên> text <key> <mô tả mới>` của option đang sai lệch.
- Không sửa nếu nghi ngờ trùng IP đích giữa nhiều OOB.
- Không bao giờ tự chạy `write memory` — chỉ sửa running-config, người dùng tự quyết
  định khi nào lưu vĩnh viễn.
- Luôn ghi log vào `push-logs/` và verify lại sau khi sửa.

## 7. Log

| File | Nội dung |
|------|----------|
| `verify-logs/Verify_<alias>_<timestamp>.log` | Báo cáo Deep Verify dạng bảng, nhóm theo mức độ nghiêm trọng (CANH BAO lên đầu) |
| `push-logs/Push_<alias>_<timestamp>.log` | Lịch sử các lần tự động sửa mô tả (cũ → mới, kèm lệnh revert nếu cần sửa tay lại) |
| `reports/OOB_Menu_Report_<timestamp>.xlsx` | Báo cáo Excel xuất từ option `[e]`, dùng dữ liệu tổng hợp từ log verify gần nhất |

Xem nhanh log Verify gần nhất ngay trong menu qua mục **[9]**.

## 8. Dừng chương trình

- Chế độ Menu: chọn **[0] Thoát**.
- Chế độ Daemon: `Ctrl+C`.
# OOB Network Manager

Công cụ **giám sát, xác minh vật lý và tự động phục hồi** menu OOB (Out-of-Band
Console Server chạy Cisco IOS hoặc Vertiv ACS) qua SSH/Telnet. Phát hiện cấu hình
menu bị thay đổi so với baseline, kiểm tra vật lý từng cổng console (Deep Verify —
pivot vào từng cổng để xác nhận đúng thiết bị thật đang nối vào), tự động sửa lại
mô tả (description) trên menu khi phát hiện sai lệch, và có thêm một **giao diện
Web Dashboard** để thao tác từ trình duyệt thay vì chỉ dùng CLI.

Gồm 3 file chính:
- `oob_lib.py` — thư viện kết nối (SSH ưu tiên qua `paramiko`, fallback Telnet tự
  viết), đọc/parse cấu hình `menu`, ghi (push) lại đúng 1 dòng mô tả khi cần sửa,
  và kiểm tra ping tới thiết bị.
- `oob_monitor.py` — chương trình chính (CLI): daemon giám sát, menu quản lý,
  Deep Verify / auto push, lập lịch cho cả 2 luồng, Import/Export Excel. Hỗ trợ
  song song 2 dòng thiết bị OOB: **Cisco IOS** (menu console) và **Vertiv ACS**
  (serial console server).
- `oob_web.py` — giao diện **Web Dashboard** (Flask) chạy song song/độc lập với
  CLI, dùng lại toàn bộ logic của `oob_monitor.py`: xem danh sách thiết bị, trạng
  thái ping/menu theo thời gian thực, tìm kiếm port/hostname, và bấm nút để chạy
  Scan / Verify / Push ngay trên trình duyệt (không cần mở terminal).

---

## 1. Yêu cầu hệ thống

- Python 3.9 trở lên (dùng cú pháp `str | None`).
- Truy cập mạng (SSH/Telnet) tới các thiết bị OOB cần giám sát.
- Quyền ghi thư mục chạy chương trình (để tạo file config, database SQLite, file
  trạng thái thiết bị, và các thư mục log).
- Nếu muốn dùng Web Dashboard (`oob_web.py`): mở thêm 1 cổng TCP (mặc định `5000`)
  để truy cập bằng trình duyệt.

## 2. Cài đặt thư viện

Thư viện ngoài cần cài đặt được liệt kê trong `requirements.txt`:

```
paramiko==3.5.1
rich>=13.7.0,<15.0.0
flask>=3.0.0,<4.0.0
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
> - `rich` — vẽ giao diện console (bảng, khung, layout chia đôi màn hình, tự
>   refresh log theo thời gian thực) cho `oob_monitor.py`.
> - `flask` — chạy Web Dashboard `oob_web.py` (routing trang chủ, trang chi tiết
>   thiết bị, và các API JSON). Không cần nếu bạn chỉ dùng CLI thuần tuý.
> - `openpyxl` — đọc/ghi file Excel `.xlsx` cho tính năng Import và Export báo cáo
>   trong `oob_monitor.py`. Nếu chưa cài, 3 hàm Import/Export vẫn chạy nhưng sẽ
>   thông báo lỗi và thoát.

## 3. Cấu trúc file khi chạy

Lần chạy đầu tiên, chương trình tự tạo các file/thư mục sau (không cần tạo tay trước):

| File / Thư mục               | Vai trò                                                              |
|------------------------------|----------------------------------------------------------------------|
| `oob_config.json`            | Cấu hình chính (tài khoản, mật khẩu Vertiv, cổng, chu kỳ, lịch chạy, đường dẫn...) |
| `oob_ips.txt`                | Danh sách IP + alias các thiết bị OOB cần giám sát                  |
| `baseline.db`                | SQLite — cấu hình "chuẩn" dùng để so sánh                           |
| `snapshot.db`                | SQLite — cấu hình mới nhất vừa quét được                            |
| `device_status.json`         | Trạng thái ping/kết nối mới nhất của từng thiết bị (dùng bởi Dashboard, cả CLI và Web đều đọc/ghi) |
| `verify-logs/`               | Log kết quả Deep Verify (mỗi lần quét 1 file `.log`)                |
| `push-logs/`                 | Log các lần tự động sửa mô tả (push) lên thiết bị                  |
| `reports/`                   | File báo cáo Excel xuất ra từ tính năng Export (option `[e]`)        |
| `oob_import_template.xlsx`   | File mẫu Excel (tạo khi chọn xuất mẫu ở option `[i]`)              |
| `daemon.pid`                 | File heartbeat — Menu Quản Lý dùng để hiển thị trạng thái Daemon (RUNNING/STALE) |

File `oob_ips.txt` — mỗi dòng 1 thiết bị, cách nhau bằng khoảng trắng, dòng bắt
đầu bằng `#` bị bỏ qua:

```
172.29.10.36 OOB-HCM-01
172.29.10.40 OOB-HAN-02
```

> **Lưu ý:** Ngoài chỉnh tay `oob_ips.txt`, bạn có thể Import danh sách thiết bị
> từ file Excel qua option `[i]` trong Menu Quản Lý CLI (xem mục 6), hoặc thêm/xoá
> từng thiết bị trực tiếp trên Web Dashboard (nút "Thêm OOB" / "Xóa OOB", xem mục 7).

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

- **Chọn 1** → mở menu quản lý CLI (tương đương `python oob_monitor.py --menu`).
  Cửa sổ này **không** tự động chạy vòng lặp nào — chỉ dùng để thêm/xoá IP, đổi
  cấu hình, xem báo cáo, chạy quét/verify thủ công một lần.
- **Chọn 2** → chạy daemon (tương đương `python oob_monitor.py --daemon`) ngay
  trên terminal này. Daemon gồm **2 luồng chạy song song trong cùng 1 tiến
  trình**, cả hai đều tự động hoàn toàn (xem mục 5):
  - Luồng 1: quét/so sánh cấu hình `menu` định kỳ hoặc theo lịch cố định.
  - Luồng 2: Deep Verify vật lý + tự động push sửa mô tả, chạy theo lịch riêng.
  Log của cả 2 luồng hiển thị realtime, chia đôi màn hình (trên = Luồng 1,
  dưới = Luồng 2).
- **Chọn 3** → tự mở 2 cửa sổ terminal độc lập: 1 cửa sổ chạy `--daemon` (gồm cả
  2 luồng tự động ở trên), 1 cửa sổ chạy `--menu` (chỉ để thao tác thủ công).
  **Chỉ hoạt động ổn định trên Windows.** Trên Linux/WSL, tự mở 2 tab và chạy tay:
  ```bash
  python oob_monitor.py --menu
  python oob_monitor.py --daemon
  ```

> **Lưu ý quan trọng:** dù bạn chọn 2 hay 3, tất cả tự động hoá (quét cấu hình,
> Deep Verify, auto push) đều nằm trong đúng 1 tiến trình `--daemon`. Cửa sổ
> `--menu` (nếu có) chỉ là công cụ thao tác tay, không tự chạy gì cả — không cần
> mở nó nếu bạn chỉ cần daemon chạy nền.

> 2 cửa sổ (khi chọn 3) là **2 tiến trình độc lập**, mỗi tiến trình tự đọc
> `oob_config.json` một lần lúc khởi động. Đổi cấu hình ở cửa sổ Menu sẽ ghi xuống
> file, nhưng cửa sổ Daemon đang chạy sẽ **không** tự nhận thay đổi đó cho tới khi
> được khởi động lại — trừ 2 mục lịch chạy (`[s]` và `[d]`, xem mục 5), 2 mục này
> daemon tự đọc lại cấu hình mới ở mỗi vòng lặp nên không cần khởi động lại.

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
Daemon kết nối định kỳ (hoặc theo lịch cố định) vào từng OOB, đọc toàn bộ cấu
hình `menu` (Cisco) hoặc danh sách port (Vertiv ACS), so sánh với baseline đã lưu.
Nếu khác → cảnh báo ngay.

| Mục | Ý nghĩa |
|-----|---------|
| `[4]` Tên menu | Ép dùng đúng 1 tên menu; để trống = tự động dò tất cả menu trên thiết bị |
| `[s]` **Lịch chạy Thu thập** *(mới)* | Chế độ lịch: `interval` (lặp theo chu kỳ) / `daily` (mỗi ngày 1 lần, đúng giờ cố định) / `weekly` (mỗi tuần 1 lần, đúng thứ + giờ cố định) |
| `[7]` Chu kỳ interval (s) | Số giây giữa các lần quét cấu hình — **chỉ có hiệu lực khi `[s]` đang ở chế độ `interval`**; bị bỏ qua hoàn toàn khi dùng `daily`/`weekly` |

Khi chọn `[s]`, chương trình hỏi lần lượt:

```
1. Lap lai theo chu ky (giay) - hanh vi mac dinh cu
2. Hang ngay, vao 1 gio co dinh (VD 01:00 = 1 gio sang)
3. Hang tuan, vao 1 thu + gio co dinh (VD Thu 2 luc 01:00)
```

- Chọn `2` → nhập giờ dạng `HH:MM`, daemon sẽ chỉ quét đúng 1 lần/ngày vào giờ đó.
- Chọn `3` → nhập thứ (`mon`/`tue`/`wed`/`thu`/`fri`/`sat`/`sun`) + giờ, daemon
  chỉ quét đúng 1 lần/tuần.
- Màn hình Cài đặt sẽ tự hiện dòng nhắc **"Không hiệu lực"** cạnh mục `[7]` khi
  bạn đang dùng lịch cố định, để tránh nhầm lẫn tưởng đổi `[7]` sẽ có tác dụng.

### Nhóm LUỒNG 2 — VERIFY VẬT LÝ (Deep Verify — chạy theo lịch)
Daemon pivot vào từng port console, lấy hostname thực để kiểm tra description có
đúng không. Chạy độc lập theo lịch riêng, không phụ thuộc Luồng 1.

> - `[v]`/`[d]` = tần suất **pivot vật lý** vào từng port console để lấy hostname thực.
> - Đây là **2 luồng hoàn toàn độc lập**, chạy song song trên 2 thread khác nhau,
>   mỗi luồng có lịch chạy riêng (interval/daily/weekly), không ảnh hưởng lẫn nhau.

## 6. Các chức năng chính (Menu Quản Lý CLI)

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

Ngoài ra, màn hình chính của Menu Quản Lý luôn hiển thị **trạng thái Daemon**
(`RUNNING` — kèm số giây từ lần cập nhật cuối, `STALE` nếu daemon treo/chết, hoặc
`KHONG RO` nếu chưa từng chạy `--daemon` lần nào), dựa trên file heartbeat
`daemon.pid` được daemon tự ghi lại mỗi 30 giây.

---

### `[i]` Import danh sách thiết bị từ Excel

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

### `[e]` Xuất báo cáo menu OOB ra Excel

Xuất toàn bộ dữ liệu menu của tất cả OOB ra file
`reports/OOB_Menu_Report_YYYYMMDD_HHMMSS.xlsx`.

**Không cần kết nối thiết bị** — đọc từ baseline DB và file log verify đã có sẵn.

File Excel gồm **3 sheet**:

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
| Protocol | `telnet` / `ssh` / `serial` (Vertiv) |
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

#### Sheet "Canh bao"
Chỉ liệt kê các option đang ở trạng thái `SAI - Sai desc` hoặc
`Khong ket noi duoc` trong lần quét gần nhất — giúp nhìn thẳng vào vấn đề cần xử
lý mà không phải lọc qua toàn bộ sheet "Chi tiet".

Cả 3 sheet đều có **header freeze** (hàng đầu cố định khi cuộn) và **auto-filter**
để lọc/sắp xếp dễ dàng trong Excel.

Yêu cầu: `pip install openpyxl`

---

### `[7]` Quét kiểm tra cấu hình

Kết nối vào thiết bị OOB, đọc lại toàn bộ `menu`, so sánh với baseline đã lưu. Nếu
khác, hiển thị bảng so sánh và hỏi xác nhận trước khi ghi đè baseline. Nếu thiết bị
chưa có baseline, hỏi xác nhận để lưu lần đầu.

Có thể chỉ định 1/nhiều IP hoặc alias (cách nhau dấu phẩy), để trống để quét tất cả.

> **Quan trọng:** dù chạy thủ công (option `[7]`) hay tự động trong Daemon (Luồng
> 1), bước xác nhận ghi đè/tạo baseline **luôn luôn cần người dùng gõ `y`** trong
> vòng 5 giây — không có cờ cấu hình nào khiến bước này tự động "y" giúp bạn. Đây
> là khác biệt quan trọng so với Luồng 2 (Deep Verify), nơi việc push sửa lỗi có
> thể diễn ra hoàn toàn tự động nếu bật `auto_push_desc`.

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

Khi chạy thủ công (option `[8]`), nếu phát hiện `CANH BAO`, chương trình hỏi có
muốn tự động **PUSH** sửa lại mô tả trên menu OOB cho khớp hostname thật hay không.

Khi chạy tự động theo chu kỳ/lịch trong Daemon (Luồng 2), việc push **tuân theo
đúng 1 cờ duy nhất**: `auto_push_desc` (mục `[c]` trong Settings):
- **Bật (mặc định)** → phát hiện `CANH BAO` là push sửa ngay, **không hỏi xác
  nhận**, có ghi log vào `push-logs/` và tự Verify lại option vừa sửa.
- **Tắt** → daemon vẫn chạy Deep Verify và ghi log cảnh báo bình thường, nhưng sẽ
  **không** tự sửa gì trên thiết bị — bạn cần vào option `[8]` chạy tay rồi tự
  xác nhận push.

**Nguyên tắc an toàn của tính năng push:**
- Chỉ sửa đúng 1 dòng `menu <tên> text <key> <mô tả mới>` của option đang sai lệch.
- Không sửa nếu nghi ngờ trùng IP đích giữa nhiều OOB.
- Không bao giờ tự chạy `write memory` — chỉ sửa running-config, người dùng tự quyết
  định khi nào lưu vĩnh viễn.
- Luôn ghi log vào `push-logs/` và verify lại sau khi sửa.
- **Thiết bị Vertiv ACS chưa được hỗ trợ Push** — Deep Verify vẫn chạy và báo cáo
  bình thường trên Vertiv, nhưng nếu phát hiện sai lệch, chương trình chỉ cảnh báo
  chứ không tự sửa description (xem mục 9).

## 7. Web Dashboard (`oob_web.py`)

Ngoài CLI, có thể chạy một giao diện web (Flask) để xem trạng thái và thao tác
nhanh từ trình duyệt — không cần mở terminal, không cần cài `rich`.

```bash
python oob_web.py
```

Mặc định chạy tại `http://127.0.0.1:5000` (lắng nghe trên `0.0.0.0:5000`, có thể
truy cập từ máy khác trong mạng qua IP của máy chạy chương trình).

`oob_web.py` **dùng lại trực tiếp** các hàm trong `oob_monitor.py` (đọc cùng
`oob_config.json`, `oob_ips.txt`, `baseline.db`, `snapshot.db`,
`device_status.json`) — không cần phải bật daemon `--daemon` trước, các nút Scan/
Verify/Push trên web sẽ tự kết nối thiết bị và chạy nền (background thread) ngay
khi bấm.

### Trang chủ (Dashboard)

- Bảng danh sách toàn bộ thiết bị OOB: Alias, IP, trạng thái Ping (Online/Offline),
  trạng thái Menu (OK / Lỗi Connect / khác), tổng số dòng (option) đang có trong
  baseline, thời điểm cập nhật gần nhất.
- 3 nút thao tác hàng loạt: **Scan All** (thu thập lại data), **Verify All** (kiểm
  tra dây cắm vật lý), **Push Config All** (tự sửa mô tả sai — bỏ qua thiết bị
  Vertiv vì chưa hỗ trợ).
- Mỗi dòng thiết bị có nút thao tác riêng: xem chi tiết, Scan, Verify, Push, Xóa.
- Nút **"Thêm OOB"** — thêm nhanh 1 thiết bị (IP + alias) vào `oob_ips.txt` mà
  không cần sửa file tay hay vào CLI.
- Nút **"Cài đặt"** — sửa nhanh username/password/enable password/Vertiv Connect
  Password và bật/tắt `auto_verify` ngay trên web, ghi thẳng vào `oob_config.json`.
- Ô tìm kiếm ở góc trên — tra cứu theo hostname thật, alias, IP, hoặc mô tả
  description; kết quả xếp hạng theo độ khớp và hiện trong 1 modal, có nút "Đi tới"
  để nhảy thẳng đến trang chi tiết OOB tương ứng.

### Trang chi tiết 1 thiết bị (`/device/<ip>`)

- Hiển thị hostname OOB, hãng sản xuất (Cisco/Vertiv), và toàn bộ danh sách
  option/port đang lưu trong baseline: phím menu/cổng, description, IP đích, port
  đích, giao thức (`telnet`/`ssh`/`serial`).
- 3 nút thao tác riêng cho thiết bị này: Quét Lại Data, K.Tra Dây Cắm (Verify),
  Sửa Lỗi Desc (Push).
- Nếu thiết bị chưa có baseline (chưa từng Scan), hiển thị cảnh báo thay vì bảng
  trống.

### API (dùng nội bộ bởi giao diện web, có thể gọi trực tiếp nếu cần tích hợp)

| Endpoint | Method | Chức năng |
|---|---|---|
| `/api/config` | GET / POST | Đọc / cập nhật `oob_config.json` |
| `/api/device` | POST / DELETE | Thêm / xoá thiết bị trong `oob_ips.txt` |
| `/api/action` | POST | Chạy `scan` / `verify` / `push` (nền, có thể chỉ định 1 IP hoặc để trống = tất cả) |
| `/api/search` | GET (`?q=`) | Tìm kiếm port/hostname theo từ khoá, trả JSON đã xếp hạng |

> **Lưu ý:** `oob_web.py` không có xác thực đăng nhập (không có login/password
> bảo vệ trang web) và mật khẩu thiết bị hiển thị nguyên văn trong modal Cài đặt —
> chỉ nên chạy trong mạng nội bộ tin cậy, không nên expose ra Internet mà không tự
> thêm lớp bảo vệ (reverse proxy + auth, VPN, firewall...).

## 8. Log

| File | Nội dung |
|------|----------|
| `verify-logs/Verify_<alias>_<timestamp>.log` | Báo cáo Deep Verify dạng bảng, nhóm theo mức độ nghiêm trọng (CANH BAO lên đầu) |
| `push-logs/Push_<alias>_<timestamp>.log` | Lịch sử các lần tự động sửa mô tả (cũ → mới, kèm lệnh revert nếu cần sửa tay lại) |
| `reports/OOB_Menu_Report_<timestamp>.xlsx` | Báo cáo Excel xuất từ option `[e]`, dùng dữ liệu tổng hợp từ log verify gần nhất |

Xem nhanh log Verify gần nhất ngay trong menu CLI qua mục **[9]**.

## 9. Dừng chương trình

- CLI, chế độ Menu: chọn **[0] Thoát**.
- CLI, chế độ Daemon: `Ctrl+C`.
- Web Dashboard: `Ctrl+C` trong terminal đang chạy `oob_web.py`.

## 10. Lưu ý & giới hạn hiện tại

- **Thiết bị Vertiv ACS chưa hỗ trợ tính năng Push Config.** Deep Verify vẫn chạy
  và báo cáo `CANH BAO` bình thường trên Vertiv, nhưng cả CLI (`[8]`) lẫn Web
  Dashboard (nút Push) đều bỏ qua/không tự sửa description cho thiết bị loại này.
- **`[b]` Tự động Verify ngầm — hiện chưa có tác dụng thực tế.** Cờ này đang chỉ
  được hiển thị/toggle trong màn hình Settings, nhưng logic chạy Daemon (Luồng 2)
  hiện chưa kiểm tra giá trị của nó — Deep Verify vẫn luôn tự chạy theo lịch dù
  bạn tắt mục này. Nếu muốn Deep Verify dừng hẳn, cách duy nhất hiện tại là tắt
  hẳn Daemon (không chạy `--daemon`), hoặc yêu cầu vá lại logic để `[b]` gate
  đúng việc khởi động luồng Verify.
- Lịch `daily`/`weekly` (cả `[s]` và `[d]`) hiện chỉ hỗ trợ mốc **giờ cố định
  trong ngày, hoặc thứ + giờ cố định trong tuần**. Chưa hỗ trợ lịch theo ngày cụ
  thể trong tháng (VD "ngày 15 hàng tháng") hay một mốc ngày/tháng/năm duy nhất
  (chạy 1 lần rồi thôi).
- 2 tiến trình `--menu` và `--daemon` (khi chạy chế độ 3) không chia sẻ bộ nhớ —
  đổi cấu hình ở cửa sổ Menu chỉ có hiệu lực ngay với 2 mục lịch chạy (`[s]`,
  `[d]`), các mục còn lại (username/password/port/interval số giây...) cần khởi
  động lại `--daemon` mới nhận.
- `oob_web.py` là 1 tiến trình hoàn toàn riêng biệt với `--daemon`/`--menu` —
  cũng không chia sẻ bộ nhớ, chỉ chia sẻ file config/DB trên đĩa. Đổi cấu hình
  trên Web sẽ ghi xuống `oob_config.json` như CLI, nhưng nếu đang có `--daemon`
  chạy song song thì áp dụng đúng quy tắc ở trên (chỉ 2 mục lịch chạy tự nhận
  ngay, còn lại cần khởi động lại daemon).
- `oob_web.py` không có xác thực đăng nhập — xem lưu ý bảo mật ở mục 7.

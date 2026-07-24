# OOB Network Manager

Công cụ giám sát menu OOB (out-of-band console server, chạy Cisco IOS) qua
SSH/Telnet: phát hiện cấu hình menu bị thay đổi so với baseline, kiểm tra vật
lý (Deep Verify - pivot vào từng cổng để xác nhận đúng thiết bị thật đang nối
vào), và tự động sửa lại mô tả (description) trên menu khi phát hiện sai lệch.

Gồm 2 file:
- `oob_lib.py` — thư viện kết nối (SSH ưu tiên qua `paramiko`, fallback
  Telnet), đọc/parse cấu hình `menu`, và ghi (push) lại đúng 1 dòng mô tả khi
  cần sửa.
- `oob_monitor.py` — chương trình chính: daemon giám sát, menu quản lý CLI, và
  toàn bộ logic Deep Verify / auto push.

---

## 1. Yêu cầu hệ thống

- Python 3.9 trở lên (dùng cú pháp `str | None`).
- Truy cập mạng (SSH/Telnet) tới các thiết bị OOB cần giám sát.
- Quyền ghi thư mục chạy chương trình (để tạo file config, database SQLite,
  và các thư mục log).

## 2. Cài đặt thư viện

Thư viện ngoài cần cài đặt được liệt kê trong `requirements.txt`:

```
paramiko==3.5.1
rich>=13.7.0,<15.0.0
```

Cài đặt bằng lệnh:

```bash
pip install -r requirements.txt
```

Nếu môi trường yêu cầu cờ `--break-system-packages` (một số bản Linux mới):

```bash
pip install -r requirements.txt --break-system-packages
```

Khuyến khích dùng virtual environment để tránh xung đột với các gói Python
khác trên máy:

```bash
pip install -r requirements.txt
```

> `paramiko` dùng để SSH vào thiết bị (ưu tiên); nếu SSH thất bại, chương
> trình tự động rớt về Telnet (tự viết sẵn trong `oob_lib.py`, không cần cài
> thêm gói nào). `rich` dùng để vẽ giao diện console (bảng, khung, layout
> chia đôi màn hình).

## 3. Cấu trúc file khi chạy

Lần chạy đầu tiên, chương trình tự tạo các file/thư mục sau (không cần tạo
tay trước):

| File/Thư mục         | Vai trò                                                   |
|----------------------|------------------------------------------------------------|
| `oob_config.json`    | Cấu hình chính (tài khoản, cổng, chu kỳ, đường dẫn...)      |
| `oob_ips.txt`         | Danh sách IP + alias các thiết bị OOB cần giám sát          |
| `baseline.db`         | SQLite — cấu hình "chuẩn" dùng để so sánh                   |
| `snapshot.db`         | SQLite — cấu hình mới nhất vừa quét được                    |
| `verify-logs/`        | Log kết quả Deep Verify (mỗi lần quét 1 file)               |
| `push-logs/`          | Log các lần tự động sửa mô tả (push) lên thiết bị           |

File `oob_ips.txt` — mỗi dòng 1 thiết bị, cách nhau bằng khoảng trắng, dòng
bắt đầu bằng `#` bị bỏ qua:

```
172.29.10.36 CTO-OOB-02
172.29.10.40 CTO-OOB-03
```

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

- **Chọn 1** → mở menu quản lý CLI (tương đương chạy `python oob_monitor.py --menu`).
- **Chọn 2** → chạy vòng lặp giám sát nền (quét cấu hình liên tục + Deep
  Verify + auto push định kỳ), hiển thị log trực tiếp trên terminal này
  (tương đương `python oob_monitor.py --daemon`).
- **Chọn 3** → tự mở 2 cửa sổ terminal độc lập (1 chạy daemon, 1 chạy menu).
  **Chỉ hoạt động ổn định trên Windows.** Trên Linux/WSL, tự mở 2 tab và chạy
  tay:
  ```bash
  python oob_monitor.py --menu
  python oob_monitor.py --daemon
  ```

> Lưu ý: 2 cửa sổ này là **2 tiến trình độc lập**, mỗi tiến trình tự đọc
> `oob_config.json` một lần lúc khởi động. Đổi cấu hình ở cửa sổ Menu sẽ ghi
> xuống file, nhưng cửa sổ Daemon đang chạy sẽ **không** tự nhận thay đổi đó
> cho tới khi được khởi động lại.

Cũng có thể chỉ định file config khác qua tham số dòng lệnh:

```bash
python oob_monitor.py my_config.json --menu
```

## 5. Cấu hình lần đầu

Vào **Menu Quản Lý → [3] Cấu hình** để nhập:

| Mục | Ý nghĩa |
|---|---|
| Username / Password / Enable password | Tài khoản đăng nhập thiết bị OOB |
| Tên menu (menu_name_override) | Ép dùng đúng 1 tên menu; để trống = tự động dò tất cả menu trên thiết bị |
| SSH port / Telnet port | Cổng kết nối (SSH được ưu tiên thử trước) |
| Chu kỳ thu thập (interval) | Số giây giữa các lần quét cấu hình (mặc định 30s) |
| Chu kỳ Verify vật lý (verify_interval) | Số giây giữa các lần Deep Verify tự động (mặc định 3600s) |
| File danh sách IP / baseline DB / snapshot DB | Đường dẫn tùy chỉnh nếu cần |
| Tự động Verify ngầm (auto_verify) | Bật/tắt Deep Verify tự động sau khi baseline thay đổi |
| Tự động Sửa lỗi ngầm (auto_push_desc) | Bật/tắt tính năng tự động sửa mô tả (push) khi phát hiện sai lệch |

Sau đó vào **[1] Thêm thiết bị OOB** để nhập IP + alias từng thiết bị (hoặc
sửa tay file `oob_ips.txt`).

## 6. Các chức năng chính (Menu Quản Lý)

```
[1] Them thiet bi OOB              - Them 1 IP + alias vao danh sach
[2] Xoa thiet bi OOB                - Xoa 1 IP khoi danh sach
[3] Cau hinh                        - Sua username/password/port/chu ky...
[4] Xem danh sach thiet bi          - Liet ke tat ca IP dang giam sat
[5] Xem baseline (Chuan)            - Xem cau hinh menu "chuan" da luu
[6] Tim kiem thiet bi               - Tra cuu theo IP/alias
[7] Quet kiem tra Cau hinh tuc thi   - Doi chieu cau hinh hien tai vs baseline
[8] Deep Verify Vat ly tuc thi       - Pivot vao tung cong, xac nhan dung thiet bi
[9] Xem ket qua Verify vat ly gan nhat
[0] Thoat
```

**[7] Quét kiểm tra cấu hình** — kết nối vào thiết bị OOB, đọc lại toàn bộ
`menu`, so sánh với baseline đã lưu. Nếu khác, hiển thị bảng so sánh và hỏi
xác nhận trước khi ghi đè baseline. Nếu thiết bị chưa có baseline, hỏi xác
nhận để lưu lần đầu.

**[8] Deep Verify vật lý** — với từng option trong menu, kết nối pivot
(telnet/ssh) từ chính OOB sang thiết bị đích, đọc hostname thật của thiết bị
đó, và so sánh với mô tả (description) đang khai báo trên menu. Có thể chỉ
định 1/nhiều IP hoặc alias (cách nhau dấu phẩy), để trống để quét tất cả.

Kết quả mỗi option sẽ là một trong các trạng thái:

| Trạng thái | Ý nghĩa |
|---|---|
| `OK` | Hostname thật khớp với mô tả trên menu |
| `CANH BAO` | Sai lệch — hostname thật khác mô tả trên menu |
| `KHONG PIVOT` | Chưa pivot sang được thiết bị đích (vẫn ở console của chính OOB) |
| `TIMEOUT` | Không kết nối được / không có phản hồi |
| `YEU CAU DANG NHAP` | Thiết bị đích yêu cầu đăng nhập, không xác minh được hostname |

Nếu phát hiện `CANH BAO`, chương trình sẽ hỏi có muốn tự động **PUSH** sửa lại
mô tả trên menu OOB cho khớp hostname thật hay không (chỉ hỏi khi chạy thủ
công qua `[8]`; khi chạy tự động theo chu kỳ ở chế độ Daemon, việc push tuân
theo cấu hình **auto_push_desc**, không hỏi xác nhận).

Nguyên tắc an toàn của tính năng push (không thay đổi khi nâng cấp sau này):
- Chỉ sửa đúng 1 dòng `menu <tên> text <key> <mô tả mới>` của option đang sai
  lệch — không đụng đến cấu hình nào khác.
- Không sửa nếu nghi ngờ trùng IP đích giữa nhiều OOB (tránh sửa nhầm khi
  chưa rõ baseline nào đúng).
- Không bao giờ tự chạy `write memory`/lưu cấu hình khởi động — chỉ sửa
  running-config, người dùng tự quyết định khi nào lưu vĩnh viễn.
- Luôn ghi log vào `push-logs/` và verify lại sau khi sửa.

## 7. Log

- `verify-logs/Verify_<alias>_<timestamp>.log` — báo cáo Deep Verify dạng
  bảng, nhóm theo mức độ nghiêm trọng (CANH BAO lên đầu).
- `push-logs/Push_<alias>_<timestamp>.log` — lịch sử các lần tự động sửa mô
  tả (cũ → mới, kèm lệnh revert nếu cần sửa tay lại).

Xem nhanh log Verify gần nhất ngay trong menu qua mục **[9]**.

## 8. Dừng chương trình

- Chế độ Menu: chọn **[0] Thoát**.
- Chế độ Daemon: `Ctrl+C`.
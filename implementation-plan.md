# IMPLEMENTATION PLAN — OOB-Automation-Manager
### Scope: toàn bộ 24 hạng mục đã đề xuất trong `docs/upgrade-proposals.md` (nâng cấp/optimize + tăng độ chính xác thu thập dữ liệu Deep Verify)

**PROJECT_CONTEXT.md**: không tồn tại trong repo (`.agent/skills/` và `PROJECT_CONTEXT.md` đều không có). Dùng `README.md` + `api_documentation.md` + `docs/architecture-audit.md` làm ngữ cảnh dự án thay thế.

**LƯU Ý VỀ SKILL**: các skill network-automation kỳ vọng (`python-pro`, `network-config-diff`, `baseline-comparison`, `production-audit`, `textfsm-parser`, `git-commit`, `poyntail`) **không thực sự tồn tại** trong `.agent/skills/` của repo lẫn trong danh sách skill khả dụng của phiên làm việc tạo ra kế hoạch này. Skill thực sự khả dụng và được chỉ định dùng: `security-review`, `code-review`, `simplify`.

---

## 1. Scope

24 hạng mục thô từ `docs/upgrade-proposals.md` không độc lập — chúng chia sẻ cùng file/hàm ở nhiều chỗ. Nhóm lại thành **8 Work Package (WP)** theo nguyên tắc "cùng chạm 1 hàm/module → phải lên kế hoạch cùng nhau":

| WP | Tên | Gồm các đề xuất gốc | File/hàm lõi chung |
|---|---|---|---|
| **WP-A** | Deep Verify Data-Collection Reliability | #19 drain-race fix, #20 read-before-wake, #21 SSH/Telnet timing riêng, #22 retry mọi loại port, #23 hết nuốt exception, #24 debug timing chi tiết | `oob_monitor.py::check_port_via_oob()` (trong `run_deep_verify`) |
| **WP-B** | Push/Revert Safety & Truthfulness | #1 dry_run gate + label rõ, #18 Vertiv push thật | `oob_lib.py::push_menu_descriptions/push_vertiv_port_names`, `oob_monitor.py::process_push_and_reverify`, `oob_web.py::api_revert/api_action` |
| **WP-C** | Credential Security Hardening | #2 mã hoá `oob_config.json`, #3 mask `GET /api/config` | `oob_monitor.py::load_config/save_config`, `oob_web.py::api_config` |
| **WP-D** | Verify Timeout Enforcement & Baseline Audit Trail | #4 persist diff baseline, #5 fix `_verify_deadline` chết | `oob_monitor.py::run_deep_verify()` (cùng vùng hàm với WP-A), `log_baseline_change()` |
| **WP-E** | Parser Consolidation + Test Foundation | #6 gộp parser trùng, #8 test tự động | `oob_lib.py::parse_menu`, `oob_monitor.py::poll_host_multi`, thư mục `tests/` (mới) |
| **WP-F** | Read-Path Performance & Concurrency Config | #9 cache verify-log status, #12 giảm khoá DB toàn cục, #13 pool size cấu hình được, #14 chống chạy chồng chu kỳ | `oob_monitor.py::_parse_verify_logs_for_status`, `_init_db`, `run_daemon`, `run_verify_daemon` |
| **WP-G** | Daemon Concurrency Model Fix | #7 tách `action_lock` cho 2 luồng | `oob_monitor.py::run_daemon`, `run_verify_daemon` (cùng file với WP-F, phải làm sau) |
| **WP-H** | Web/CLI UX Additions | #10 validate IP, #11 cảnh báo config lỗi, #15 sửa alias qua Web, #16 daemon status thật trên Web, #17 lịch monthly/one-shot | `oob_web.py` routes, `oob_monitor.py::compute_next_scheduled_run` |

## 2. Current State Summary

- Toàn bộ business logic nằm trong `oob_monitor.py` (2070 dòng), không có module boundary; `oob_web.py` import thẳng module này.
- `run_deep_verify()`/`check_port_via_oob()` là vùng code phức tạp nhất; nhánh Vertiv dùng `write_no_drain()`, nhánh Cisco/Telnet/SSH trực tiếp vẫn dùng `write()` (có drain) cho thao tác "đánh thức" — nguyên nhân khả dĩ nhất cho triệu chứng TIMEOUT giả người dùng báo cáo.
- `push_menu_descriptions`/`push_vertiv_port_names` luôn được gọi với `dry_run=True` hardcode ở cả 3 call site.
- `oob_config.json` lưu credential dạng plaintext; chỉ `working_creds.json` có encrypt (Fernet nếu có `cryptography`, nếu không thì base64 — mà `cryptography` không nằm trong `requirements.txt`).
- Không có test nào trong repo.
- `max_verify_duration`/`_verify_deadline` được tính nhưng không bao giờ được kiểm tra lại trong vòng lặp verify.
- `db_lock` là 1 lock toàn cục cho mọi thao tác SQLite; `action_lock` được cả `run_daemon` và `run_verify_daemon` dùng chung, khiến 2 luồng không thực sự độc lập như README mô tả.

## 3. Requirement Analysis (Classification theo WP)

| WP | Classification |
|---|---|
| WP-A | Bug Fix, Reliability Improvement, Network Automation |
| WP-B | Bug Fix, Security Improvement, Reliability Improvement, Network Automation |
| WP-C | Security Improvement |
| WP-D | Bug Fix, Reliability Improvement, Database (log persistence, không phải schema DB) |
| WP-E | Refactoring, Testing |
| WP-F | Performance Improvement, Database, Infrastructure |
| WP-G | Architecture Change, Reliability Improvement |
| WP-H | New Feature (nhỏ), API, CLI |

## 4. Dependency Graph

```
                    PHASE 0
                    WP-A(#24 debug timing)  <- làm trước để validate các fix khác
                           |
              +------------+-------------+
              v                          v
          WP-C (độc lập)            WP-E(a) test-first
              |                          |
              |               +----------+----------+
              |               v                     v
              |      WP-A(còn lại) + WP-D      WP-E(b) gộp parser
              |      (1 phase, HARD dep:              (an toàn vì đã có test)
              |       cùng hàm run_deep_verify)
              |               |
              |               v  (soft dep: WP-B cần WP-A/D ổn định)
              +----------> WP-B
                              |
                    +---------+---------+
                    v                   v
                  WP-F              WP-G (HARD dep: phải làm SAU WP-F,
                (cache,WAL,               vì cùng chạm run_daemon/run_verify_daemon)
                 pool,guard)
                    |
                    v
                  WP-H (độc lập phần lớn, có thể chạy song song)
```

**Hard dependencies:**
- WP-A ⇄ WP-D: PHẢI làm cùng 1 phase — cả hai sửa cùng vòng lặp trong `run_deep_verify()`.
- WP-E(a) → WP-E(b): PHẢI có test trước khi gộp parser (rủi ro regression baseline dữ liệu thật).
- WP-F → WP-G: PHẢI làm cache/pool/overlap-guard trước khi tái cấu trúc lock.

**Soft dependencies:**
- WP-B nên đợi WP-A/D ổn định: `process_push_and_reverify()` gọi `run_deep_verify()` để re-verify sau khi "push" — nếu bật push thật mà verify vẫn còn lỗi race, kết quả re-verify sẽ tiếp tục sai lệch.
- WP-C không phụ thuộc gì, có thể chạy sớm nhất, song song WP-E(a).
- WP-H phần lớn độc lập.

**Xung đột tiềm ẩn (conflicting changes) đã xác định:**
- WP-A và WP-D cùng sửa 1 hàm → gộp vào 1 phase, 1 review.
- WP-F(#14 overlap-guard) và WP-G(#7 lock) cùng sửa `run_daemon`/`run_verify_daemon` → làm WP-F trước.
- WP-E(#6 gộp parser) và WP-A không xung đột trực tiếp (khác hàm) nhưng cùng nằm trong `oob_monitor.py`/`oob_lib.py` — khuyến nghị làm tuần tự.

## 5. Recommended Implementation Order

```
PHASE 0 -> PHASE 1 -> PHASE 2 -> PHASE 3 -> PHASE 4 -> PHASE 5 -> PHASE 6 -> PHASE 7
(Prep)     (Foundation) (Core Reliability) (Trust/Push) (Parser) (Perf/Concurrency) (UX) (Hardening)
```
Chi tiết từng phase ở mục 22 (Final Roadmap).

---

## 6. Detailed Plan — WP-A: Deep Verify Data-Collection Reliability

### 6.1 Objective
Loại bỏ nguyên nhân gốc khiến Deep Verify báo TIMEOUT/không lấy được data trong khi pivot thủ công vẫn vào bình thường, và tăng khả năng tự chẩn đoán khi vẫn thất bại.

### 6.2 Current Implementation
- File: `oob_monitor.py`, hàm `run_deep_verify()` (dòng ~792-1187), sub-closure `check_port_via_oob()` (dòng ~838-1044).
- Nhánh Vertiv: đọc trước (`_read(...)`) rồi mới quyết định có cần gõ Enter đánh thức hay không, dùng `write_no_drain()` khi cần đánh thức (đúng pattern an toàn).
- Nhánh Cisco/Telnet/SSH trực tiếp (else, dòng ~996-1002): `_write(cmd)` -> `sleep(verify_wait_after_connect)` -> `_write("")` -> `_write("")` -> `_read(timeout=5)`. `_write` map tới `session.write()`, mà với `MiniSSH.write()` luôn gọi `_drain_pending()` trước khi gửi — xoá bất kỳ data nào đã về nhưng chưa đọc.
- `except Exception: pass` bọc quanh lời gọi `check_port_via_oob` (dòng ~1114) — nuốt hết exception.
- `verify_wait_after_connect` là 1 giá trị config duy nhất dùng chung cho cả SSH và Telnet.
- Không có retry cho port "Direct/Vertiv" khi lần đọc đầu fail (chỉ port ảo >2000 mới có "clear line" retry).
- `debug_dump()` đã ghi `repr()` từng bước nhưng không kèm mốc thời gian tương đối giữa các bước.

### 6.3 Gap Analysis
| Gap | Ảnh hưởng |
|---|---|
| Không đọc-trước-khi-đánh-thức ở nhánh Cisco/Telnet/SSH | Data thật bị `_drain_pending()` xoá đúng lúc vừa về |
| 1 giá trị wait chung cho SSH/Telnet | SSH handshake chậm hơn Telnet, dễ timeout giả |
| Không retry cho port Direct/Vertiv | 1 lần fail = TIMEOUT vĩnh viễn trong chu kỳ đó |
| Exception bị nuốt | Không phân biệt được "thật sự không có data" vs "lỗi socket/decode" |
| Debug log thiếu timestamp tương đối | Không đo được khoảng cách thời gian giữa lệnh gửi và data về |

### 6.4 Proposed Technical Solution
- Không đổi kiến trúc tổng thể (vẫn giữ session per-host reuse qua `nonlocal session`), chỉ thay đổi trình tự đọc/ghi bên trong nhánh else để giống pattern Vertiv đã chứng minh hoạt động: đọc trước -> chỉ đánh thức nếu chưa thấy dấu hiệu prompt -> dùng `write_no_drain` khi đánh thức.
- Component mới: hàm nội bộ dùng chung `_wake_if_needed(session, read_patterns, wake_timeout, live_print)` để tránh lặp code giữa nhánh Vertiv và nhánh Cisco.
- Config mới: tách `verify_wait_after_connect` thành `verify_wait_after_connect_telnet` (giữ default cũ 1.5) và `verify_wait_after_connect_ssh` (default mới, đề xuất 3.0) — migration mặc định: nếu config cũ chỉ có `verify_wait_after_connect`, dùng giá trị đó cho cả hai (backward compatible).
- Retry: thêm 1 lần retry có điều kiện — CHỈ khi lần đọc đầu tiên trả về rỗng/không khớp pattern nào — dùng `write_no_drain("")` + timeout dài hơn (gấp đôi) cho lần thử lại.
- Error handling: đổi `except Exception: pass` thành `except Exception as e: note_parts.append(f"Exception: {type(e).__name__}: {e}")`.
- Debug: `debug_dump()` thêm tham số `elapsed_ms` vào mỗi dòng ghi.
- Không đổi: cấu trúc kết quả trả về (`results` list), format file log (`.log`/`.json`).

### 6.5 Files Expected to Change
```
MODIFY: oob_monitor.py     (run_deep_verify, check_port_via_oob, DEFAULT_CONFIG, debug_dump)
MODIFY: oob_web.py         (loadSettings()/saveSched() JS + api_config allowed-list - thêm 2 field wait mới)
MODIFY: README.md          (mục 5 - mô tả lại 2 giá trị wait riêng SSH/Telnet)
TEST:   tests/test_deep_verify_timing.py   (MỚI - unit test cho _wake_if_needed logic, dùng mock session)
```

### 6.6 Detailed Implementation Steps
1. Thêm `verify_wait_after_connect_ssh`/`verify_wait_after_connect_telnet` vào `DEFAULT_CONFIG`, giữ `verify_wait_after_connect` cũ làm fallback. Verify: config cũ vẫn load được, không lỗi.
2. Viết mock `FakeSession` tái hiện đúng race condition đã xác định. Verify: test FAIL với code hiện tại (chứng minh đúng bug).
3. Sửa nhánh else trong `check_port_via_oob()`: thay `_write(""); _write("")` bằng logic đọc-trước rồi chỉ gọi `_write_no_drain("")` nếu chưa thấy prompt. Verify: test ở bước 2 PASS.
4. Áp dụng `verify_wait_after_connect_ssh`/`_telnet` tương ứng theo `proto`. Verify: đúng giá trị được chọn theo proto.
5. Thêm retry có điều kiện cho nhánh Direct. Verify: test mock "lần đầu rỗng, lần 2 có data" -> status cuối phải OK/CANH BAO thay vì TIMEOUT.
6. Đổi `except Exception: pass` -> ghi lại exception vào `note`. Verify: test mock ném exception giả, kiểm tra `results[i]["note"]`.
7. Thêm `elapsed_ms` vào `debug_dump()`. Verify: bật `debug_verify=True`, chạy trên thiết bị thật đang lỗi, xác nhận log có timing rõ ràng.
8. Chạy lại Deep Verify thủ công trên đúng port người dùng báo lỗi trước đây. Verify: xác nhận hết TIMEOUT giả (không thể tự động hoá hoàn toàn, cần thiết bị thật).
9. Cập nhật `oob_web.py` Settings tab (`tab-sched`) thêm 2 field mới, cập nhật `api_config` allowed-list.

### 6.7 Data Flow
```
INPUT: options{key: {ip, port, protocol, description, vendor}}
  -> VALIDATION: session đã mở, proto xác định SSH hay Telnet
  -> SERVICE: check_port_via_oob() - gửi lệnh pivot, ĐỌC TRƯỚC (mới), chỉ đánh thức
     không-drain nếu cần, retry có điều kiện nếu rỗng
  -> BUSINESS LOGIC: extract_hostname() so khớp với description (không đổi)
  -> DRIVER: MiniSSH/MiniTelnet (chỉ thêm dùng write_no_drain đúng chỗ)
  -> OUTPUT: results[] + verify-logs/*.log,*.json (format không đổi) + debug-logs (thêm timing)
```

### 6.8 Error & Failure Handling
| Tình huống | Xử lý mới |
|---|---|
| Target thật sự không phản hồi | Sau retry vẫn rỗng -> TIMEOUT như cũ, note ghi rõ "đã retry 1 lần" |
| Exception socket giữa chừng | Note ghi rõ loại exception, không còn "TIMEOUT hoặc lỗi mạng" mơ hồ |
| Session OOB bị mất giữa chừng | Không đổi - `reset_session()` hiện có vẫn giữ nguyên |
| Config thiếu 2 key wait mới | Fallback về giá trị `verify_wait_after_connect` cũ, không lỗi |

### 6.9 Security Considerations
Không phát sinh rủi ro bảo mật mới. `debug_dump()` chỉ ghi khi `debug_verify=True` (tuỳ chọn, tắt mặc định).

### 6.10 Testing Plan
- Unit test (mock session): race-condition reproduction, retry logic, exception surfacing, timeout theo proto.
- Integration test (thiết bị thật/lab): Live Debug trên đúng port từng lỗi.
- Regression test: chạy Deep Verify trên toàn bộ thiết bị hiện có, so sánh kết quả trước/sau.
- Edge case: thiết bị trả lời ngay lập tức (0ms); thiết bị không bao giờ trả lời.

### 6.11 Validation Criteria
- [ ] Test mock race-condition PASS.
- [ ] Trên port người dùng từng báo lỗi, Deep Verify qua tool khớp với pivot thủ công.
- [ ] Không có CANH BAO/TIMEOUT mới phát sinh trên thiết bị đang hoạt động bình thường.
- [ ] Log Verify khi thất bại có lý do cụ thể thay vì mơ hồ.
- [ ] Config cũ vẫn chạy được không cần migrate tay.

---

## 7. Detailed Plan — WP-D: Verify Timeout Enforcement & Baseline Audit Trail

*(Gộp cùng phase với WP-A do chung hàm)*

### 7.1 Objective
(a) Làm cho `max_verify_duration` thực sự có tác dụng chặn vòng lặp Verify quá lâu. (b) Lưu lại nội dung diff thật khi baseline tự động cập nhật.

### 7.2 Current Implementation
- `_verify_deadline = time.time() + max_duration` (dòng ~795) được tính nhưng không so sánh lại trong vòng `for key, opt in options.items():` (dòng ~1105).
- `log_baseline_change(alias, ip, action)` (dòng ~151-159) chỉ ghi 1 dòng text.

### 7.3 Gap Analysis
- Deadline: biến tồn tại nhưng chết.
- Audit: không thể tra cứu sau này baseline đổi từ gì sang gì.

### 7.4 Proposed Technical Solution
- Thêm check `if time.time() > _verify_deadline: break` đầu vòng lặp options, kèm log rõ số option chưa kiểm tra, giữ kết quả cũ cho các option còn lại (kế thừa cơ chế "kế thừa kết quả verify cũ" đã có sẵn).
- Đổi chữ ký `log_baseline_change(alias, ip, action, diff=None)` — nếu `diff` được truyền, ghi thêm JSON compact chứa `extra/missing/changed`.
- 3 call site (`run_daemon`, `scan_specific_devices`, `oob_web._run_scan`) truyền thêm `diff_options(baseline, snapshot)`.

### 7.5 Files Expected to Change
```
MODIFY: oob_monitor.py   (run_deep_verify - deadline check; log_baseline_change - tham số diff;
                           3 call site: run_daemon, scan_specific_devices, oob_web._run_scan)
MODIFY: oob_web.py       (_run_scan - truyền diff khi gọi log_baseline_change)
TEST:   tests/test_baseline_audit.py   (MỚI)
```

### 7.6 Detailed Implementation Steps
1. Thêm early-exit deadline check. Verify: unit test mock `time.time()` giả lập vượt deadline giữa chừng.
2. Đổi chữ ký `log_baseline_change`, thêm serialize diff. Verify: file log chứa đúng JSON diff khi truyền diff, vẫn hoạt động như cũ khi `diff=None`.
3. Cập nhật 3 call site truyền diff thực tế. Verify: scan thủ công trên thiết bị đã đổi 1 mô tả, xác nhận log có diff.
4. Cập nhật README mục 9 (Log).

### 7.7 Data Flow
```
INPUT: baseline (cũ) + snapshot (mới)
  -> VALIDATION: options_equal() (không đổi)
  -> SERVICE: diff_options() tính chi tiết (đã có sẵn, gọi thêm đúng chỗ)
  -> BUSINESS LOGIC: quyết định ghi đè baseline (không đổi hành vi tự động ghi đè)
  -> REPOSITORY: save_options() (không đổi) + log_baseline_change() (MỞ RỘNG)
  -> OUTPUT: baseline-logs/baseline_updates.log có thể tra cứu diff lịch sử
```

### 7.8 Error & Failure Handling
- Ghi log thất bại: giữ nguyên `except Exception: pass` (audit log không được crash luồng chính).
- Deadline vượt giữa chừng: không coi là lỗi, chỉ là điều kiện dừng sớm hợp lệ.

### 7.9 Security Considerations
Diff log không chứa credential, không cần mã hoá riêng.

### 7.10 Testing Plan
Unit: deadline early-exit, log format với/không có diff. Integration: scan thật với thay đổi mô tả cố ý. Edge case: deadline vượt ngay từ option đầu tiên.

### 7.11 Validation Criteria
- [ ] `max_verify_duration=30` với thiết bị nhiều option chậm -> vòng lặp dừng đúng hạn.
- [ ] Baseline update log chứa nội dung diff đọc được (JSON hợp lệ).
- [ ] Không có thay đổi hành vi khi diff=None.

---

## 8. Detailed Plan — WP-B: Push/Revert Safety & Truthfulness

### 8.1 Objective
Chấm dứt tình trạng Push/Revert luôn mô phỏng nhưng báo cáo như thành công thật; cho phép bật push thật có kiểm soát, kể cả cho Vertiv.

### 8.2 Current Implementation
- `oob_lib.push_menu_descriptions()`/`push_vertiv_port_names()`: `dry_run=True` mặc định, `if dry_run: return True` sau khi in lệnh giả lập.
- 3 call site hardcode `dry_run=True`: `oob_monitor.py:1259` (Vertiv), `:1262` (Cisco), `oob_web.py:674` (Revert).
- `process_push_and_reverify()` sau khi "push" thành công (giả) vẫn cập nhật baseline description.

### 8.3 Gap Analysis
- Không có cờ cấu hình điều khiển dry_run.
- UI không phân biệt "đã push thật" và "đã mô phỏng".
- Baseline bị cập nhật ngay cả khi là mô phỏng.

### 8.4 Proposed Technical Solution
- Config mới: `push_live_mode: bool` (mặc định `False`) trong `DEFAULT_CONFIG`.
- Caller (`process_push_and_reverify`, `api_revert`) truyền `dry_run = not cfg.get("push_live_mode", False)` thay vì hardcode `True`.
- Baseline chỉ cập nhật khi push thật thành công; khi mô phỏng, ghi push-log tiêu đề `=== PUSH LOG [MÔ PHỎNG] ===` và không cập nhật baseline.
- Daemon tự động: chỉ tự động push thật nếu cả `auto_push_desc=True` và `push_live_mode=True`.
- Web: modal xác nhận rõ ràng hơn khi `push_live_mode=True`, hiển thị chính xác lệnh sắp gửi.
- Vertiv push thật: `push_vertiv_port_names()` đã có code thật đầy đủ, chỉ cần `dry_run=False` để kích hoạt.

### 8.5 Files Expected to Change
```
MODIFY: oob_monitor.py   (DEFAULT_CONFIG + push_live_mode; process_push_and_reverify;
                           settings_menu CLI toggle; manual_push_devices label rõ mô phỏng/thật)
MODIFY: oob_web.py       (api_action push path; api_revert; api_config allowed-list; HTML settings toggle)
MODIFY: oob_lib.py       (rà lại docstring cho khớp hành vi mới)
MODIFY: README.md, api_documentation.md
TEST:   tests/test_push_gate.py   (MỚI)
```

### 8.6 Detailed Implementation Steps
1. Thêm `push_live_mode: False` vào `DEFAULT_CONFIG`. Verify: config cũ tự có giá trị mặc định False.
2. Sửa `process_push_and_reverify` tính `dry_run_flag`. Verify: `push_live_mode=False` -> hành vi y hệt hiện tại.
3. Thêm điều kiện chỉ cập nhật baseline khi không dry_run. Verify: mock push trả True nhưng dry_run_flag=True -> baseline KHÔNG đổi.
4. Đổi tiêu đề push-log động theo dry_run_flag. Verify: đọc file log, tiêu đề đúng.
5. Áp dụng cùng logic cho `api_revert`. Verify: revert mô phỏng như cũ khi tắt; cần thiết bị test thật khi bật.
6. Thêm điều kiện kép `auto_push_desc AND push_live_mode` cho daemon tự động. Verify: test 4 tổ hợp cờ.
7. Cập nhật CLI Settings menu, thêm toggle mới với cảnh báo rõ.
8. Cập nhật Web Settings tab + JS.
9. Test tích hợp trên thiết bị TEST/lab (không phải production): bật push_live_mode=True, gây sai lệch cố ý, chạy push, xác nhận lệnh thật gửi đúng và baseline chỉ cập nhật sau khi xác nhận thành công.
10. Cập nhật README/API doc để mô tả đúng 2 chế độ.

### 8.7 Data Flow
```
INPUT: verify_results (CANH BAO items) + cfg.push_live_mode + cfg.auto_push_desc
  -> VALIDATION: kiểm tra không trùng target_ip trên nhiều OOB (đã có, không đổi)
  -> SERVICE: process_push_and_reverify() quyết định dry_run_flag
  -> DRIVER: push_menu_descriptions()/push_vertiv_port_names() - gửi lệnh thật hoặc mô phỏng
  -> BUSINESS LOGIC: CHỈ khi thật + thành công -> cập nhật baseline description
  -> REPOSITORY: save_options() + push-log (tiêu đề phản ánh đúng mô phỏng/thật)
  -> OUTPUT: re-verify phản ánh đúng trạng thái thiết bị thật
```

### 8.8 Error & Failure Handling
| Tình huống | Xử lý |
|---|---|
| Push thật thất bại | Giữ nguyên logic `all_success`; baseline KHÔNG cập nhật |
| Push thật timeout giữa chừng | Ghi rõ option nào đã gửi, option nào chưa trong push-log |
| Thiếu vertiv_admin_username/password khi bật push_live_mode | Giữ nguyên check hiện có |

### 8.9 Security Considerations
Rủi ro vận hành cao nhất trong toàn bộ kế hoạch — bắt buộc review bằng `security-review` + `code-review` trước khi merge. Cân nhắc ghi actor (ai bật push thật) vào push-log — đánh dấu ASSUMPTION cần xác nhận với chủ dự án.

### 8.10 Testing Plan
Unit: 4 tổ hợp cờ. Integration: bắt buộc trên lab/thiết bị test trước. Failure case: thiết bị từ chối lệnh -> baseline không cập nhật sai. Rollback: Revert hoạt động đúng với push-log thật.

### 8.11 Validation Criteria
- [ ] Mặc định `push_live_mode=False` -> hành vi 100% giống hệ thống hiện tại.
- [ ] Bật trên lab -> lệnh thật xác nhận đến thiết bị (kiểm tra thủ công).
- [ ] Push mô phỏng không còn làm baseline tự nhận đã sửa xong.
- [ ] Push-log luôn ghi rõ MÔ PHỎNG hay THẬT.

---

## 9. Detailed Plan — WP-C: Credential Security Hardening

### 9.1 Objective
Bảo vệ credential trong `oob_config.json` và ngăn rò rỉ qua `GET /api/config`.

### 9.2 Current Implementation
- `load_config`/`save_config` (`oob_monitor.py:194-203`): `json.load`/`json.dump` thẳng, không encrypt.
- `_encrypt_dict`/`_decrypt_dict` (dòng ~244-248) đã tồn tại, hiện chỉ dùng cho `working_creds.json`.
- `requirements.txt` không có `cryptography`.
- `api_config` GET (`oob_web.py:582-586`) trả toàn bộ field trừ `credentials`.

### 9.3 Gap Analysis
Field nhạy cảm cần bảo vệ: `password`, `enable_password`, `vertiv_connect_password`, `vertiv_admin_password`, và từng phần tử `credentials[]`.

### 9.4 Proposed Technical Solution
- Không đổi format file `oob_config.json` — chỉ đổi giá trị field nhạy cảm thành chuỗi có prefix (`ENC:`/`B64:`, tái dùng cơ chế đã có).
- `save_config()`: encrypt field nhạy cảm trước khi ghi. `load_config()`: decrypt ngược lại — trong suốt với phần code còn lại.
- Migration: file cũ chưa có prefix coi như chưa mã hoá, giữ nguyên; lần save kế tiếp tự động mã hoá — không cần script migrate riêng.
- Thêm `cryptography` vào `requirements.txt`.
- `api_config` GET: mask field nhạy cảm (`"******"`) thay vì loại trừ; POST bỏ qua field nếu giá trị gửi lên == mask (tránh ghi đè password thật bằng literal `"******"`).

### 9.5 Files Expected to Change
```
MODIFY: requirements.txt   (+ cryptography)
MODIFY: oob_monitor.py     (load_config, save_config - encrypt/decrypt toàn bộ field nhạy cảm
                             kể cả trong credentials[])
MODIFY: oob_web.py         (api_config GET mask; api_config POST bỏ qua nếu giá trị == mask)
TEST:   tests/test_config_encryption.py   (MỚI)
```

### 9.6 Detailed Implementation Steps
1. Thêm `cryptography` vào `requirements.txt`. Verify: cài thành công, `_FERNET_AVAIL=True`.
2. Viết `_encrypt_config_fields(cfg)`/`_decrypt_config_fields(cfg)` bao ngoài hàm hiện có. Verify: round-trip encrypt->decrypt đúng giá trị gốc.
3. Gọi 2 hàm trên trong `save_config`/`load_config`. Verify: file `oob_config.json` không còn plaintext đọc được; load lại đúng giá trị.
4. Test tương thích ngược với config cũ (plaintext, không prefix). Verify: load thành công; save lại; load lần 2 đã ở dạng mã hoá.
5. Sửa `api_config` GET dùng mask. Verify: field nhạy cảm trả `"******"`, field khác đúng giá trị thật.
6. Sửa `api_config` POST bỏ qua field == mask. Verify: GET rồi POST lại y nguyên -> password thật không bị ghi đè.
7. Kiểm tra `.gitignore` đảm bảo `.oob_secret.key` không commit.

### 9.7 Data Flow
```
INPUT: oob_config.json (plaintext cũ hoặc đã mã hoá)
  -> VALIDATION: kiểm tra prefix ENC:/B64:
  -> SERVICE: load_config() giải mã trong suốt -> cfg (plaintext trong bộ nhớ, dùng như cũ)
  -> [...business logic hiện tại không đổi...]
  -> REPOSITORY: save_config() mã hoá trước khi ghi
  -> OUTPUT: oob_config.json luôn ở dạng mã hoá sau lần save đầu tiên
```

### 9.8 Error & Failure Handling
- `.oob_secret.key` bị mất: giá trị `ENC:...` cũ trở thành không thể giải mã — README phải cảnh báo rõ cần backup file này. Thêm log CẢNH BÁO khi phải tạo key mới lần đầu.
- Giải mã thất bại (key sai/hỏng): đổi `except Exception: pass` trong `_decrypt_cred` thành trả `None`/raise rõ ràng kèm cảnh báo, tránh dùng nhầm chuỗi mã hoá làm password thật.

### 9.9 Security Considerations
Đây chính là mục tiêu của WP. `.oob_secret.key` cần quyền file hạn chế (chmod 600).

### 9.10 Testing Plan
Unit: round-trip encrypt/decrypt; tương thích ngược; POST không ghi đè khi gửi lại mask. Regression: toàn bộ luồng connect thiết bị vẫn hoạt động. Failure case: key bị corrupt/xoá.

### 9.11 Validation Criteria
- [ ] `oob_config.json` sau khi save không còn chứa plaintext password.
- [ ] Load config cũ không lỗi, tự nâng cấp ở lần save kế tiếp.
- [ ] `GET /api/config` không trả password thật.
- [ ] POST lại giá trị mask không xoá mất password thật.
- [ ] Toàn bộ chức năng connect thiết bị không bị ảnh hưởng.

---

## 10. Detailed Plan — WP-E: Parser Consolidation + Test Foundation

### 10.1 Objective
Xây dựng bộ test nền tảng cho các hàm thuần logic quan trọng nhất, sau đó gộp 2 bản parser Cisco menu đang trùng lặp thành 1.

### 10.2 Current Implementation
- `oob_lib.py::parse_menu()` (dòng 531-606): dùng bởi `oob_lib.poll_host()` — cần xác nhận thêm liệu còn được gọi ở đâu.
- `oob_monitor.py::poll_host_multi()` (dòng 636-715): logic parse Cisco riêng, khác cách xử lý bracket key.
- Không có test nào cho cả hai.

### 10.3 Gap Analysis
2 implementation khác nhau cho cùng bài toán, đã lệch hành vi ở edge case bracket-key. Không test nào bảo vệ trước khi gộp.

**ASSUMPTION cần xác nhận**: cần grep toàn bộ repo xác nhận `oob_lib.poll_host()`/`parse_menu()` còn được gọi ở đâu không trước khi quyết định hướng gộp-giữ-cả-hai-API hay xoá-code-chết.

### 10.4 Proposed Technical Solution
- Bước 1 (test-first, không đổi code sản phẩm): viết test cho hành vi HIỆN TẠI của cả 2 parser, dùng fixture output thật (ẩn danh hoá).
- Bước 2: grep xác nhận call site thật.
- Bước 3a (nếu `poll_host`/`parse_menu` đã chết): xoá code chết, giữ logic `poll_host_multi`.
- Bước 3b (nếu vẫn còn nơi gọi): refactor parser dùng chung đặt trong `oob_lib.py`, ưu tiên giữ logic bracket-key của `poll_host_multi` (code path production).

### 10.5 Files Expected to Change
```
CREATE: tests/__init__.py, tests/conftest.py
CREATE: tests/test_parse_menu.py, tests/test_options_diff.py, tests/test_schedule.py
CREATE: tests/fixtures/cisco_menu_samples.txt   (dữ liệu mẫu ẩn danh hoá)
MODIFY: oob_lib.py, oob_monitor.py   (chỉ ở Bước 3b, nếu cần)
MODIFY: requirements.txt   (+ pytest, tách requirements-dev.txt)
CREATE: pytest.ini hoặc pyproject.toml
```

### 10.6 Detailed Implementation Steps
1. Thêm `pytest` vào `requirements-dev.txt` (tách khỏi production requirements). Verify: `pytest --version` chạy được.
2. Thu thập 3-5 mẫu output thật (ẩn danh hoá) làm fixture. Verify: review bởi người có domain knowledge.
3. Viết characterization test cho `oob_lib.parse_menu()`. Verify: PASS với code hiện tại.
4. Viết test tương tự cho logic parse trong `poll_host_multi()` (có thể cần extract tạm ra hàm module-level trước để test được). Verify: PASS, hành vi giữ nguyên 100%.
5. So sánh kết quả 2 bộ test cùng fixture, liệt kê khác biệt (đặc biệt bracket key). Verify: báo cáo rõ ràng danh sách khác biệt.
6. Grep xác nhận call site thật của `poll_host`/`parse_menu` trong toàn repo.
7. Dựa trên kết quả 5+6, chọn 3a hoặc 3b, thực hiện gộp, chạy lại toàn bộ test. Verify: 100% test cũ PASS sau khi gộp.
8. Viết thêm test cho `options_equal`/`diff_options`/`compute_next_scheduled_run` (có thể song song bước 1-2).

### 10.7 Data Flow
Không có data flow runtime mới — công việc testing/refactor nội bộ.

### 10.8 Error & Failure Handling
Không áp dụng theo nghĩa runtime; giảm thiểu rủi ro "fixture không đại diện đủ" bằng cách lấy dữ liệu thật đã audit.

### 10.9 Security Considerations
Fixture phải được ẩn danh hoá (IP/alias/description thật không được commit lên git).

### 10.10 Testing Plan
Bản thân WP này LÀ testing plan cho các WP khác. Coverage mục tiêu: `parse_menu`, `options_equal`, `diff_options`, `compute_next_scheduled_run`, `extract_hostname`.

### 10.11 Validation Criteria
- [ ] Bộ test chạy được bằng `pytest` không cần thiết bị thật.
- [ ] Test characterization PASS trên code hiện tại trước khi gộp.
- [ ] Sau khi gộp, cùng bộ test vẫn PASS 100%.
- [ ] Xác định rõ ràng (không phải assumption) liệu `poll_host`/`parse_menu` còn được dùng hay là code chết.

---

## 11. Detailed Plan — WP-F: Read-Path Performance & Concurrency Config

### 11.1 Objective
Giảm chi phí đọc lặp lại verify-log, giảm tranh chấp lock DB, cho phép cấu hình pool size, tránh chạy chồng chu kỳ.

### 11.2 Current Implementation
- `_parse_verify_logs_for_status()` (`oob_monitor.py:1632-1676`) đọc + parse toàn bộ file `.json` mỗi lần gọi.
- `db_lock` là 1 lock toàn cục cho mọi thao tác DB.
- `ThreadPoolExecutor(max_workers=10)` (daemon) / `max_workers=5` (web) hardcode.
- Không có cờ "đang chạy" để bỏ qua tick lịch trùng.

### 11.3 Gap Analysis
Cache miss 100% mỗi request; lock toàn cục serialize không cần thiết; pool size không điều chỉnh được; có thể chạy chồng chu kỳ.

### 11.4 Proposed Technical Solution
- Cache verify-log status theo `alias`, invalidate dựa trên mtime file `.json` mới nhất của alias đó.
- Bật `PRAGMA journal_mode=WAL` trong `_init_db()` — chỉ bật WAL trước (rủi ro thấp), chưa tách lock đọc/ghi trong WP này.
- Thêm `scan_max_workers`/`verify_max_workers` vào `DEFAULT_CONFIG`, mặc định giữ giá trị hiện tại.
- Overlap guard: biến trạng thái `_scan_in_progress`/`_verify_in_progress` — nếu chu kỳ mới tới mà chu kỳ cũ chưa xong, ghi log bỏ qua tick thay vì xếp chồng.

### 11.5 Files Expected to Change
```
MODIFY: oob_monitor.py   (_parse_verify_logs_for_status cache; _init_db PRAGMA WAL;
                           DEFAULT_CONFIG max_workers; run_daemon/run_verify_daemon overlap guard)
MODIFY: oob_web.py       (_run_scan/_run_verify/_run_push đọc max_workers từ config)
TEST:   tests/test_verify_log_cache.py   (MỚI)
```

### 11.6 Detailed Implementation Steps
1. Thêm cache cho `_parse_verify_logs_for_status`. Verify: gọi 2 lần liên tiếp không có file mới -> không đọc lại disk.
2. Bật WAL mode. Verify: file `-wal`/`-shm` xuất hiện; test DB hiện có không regression.
3. Thêm config `scan_max_workers`/`verify_max_workers`. Verify: ThreadPoolExecutor nhận đúng số worker mới.
4. Thêm overlap guard. Verify: mock chu kỳ chạy lâu hơn interval, tick tiếp theo bị bỏ qua có log rõ ràng.
5. Cập nhật `oob_web.py` đọc max_workers từ cfg.

### 11.7 Data Flow
Không đổi luồng nghiệp vụ, chỉ tối ưu lớp truy xuất dữ liệu và điều phối.

### 11.8 Error & Failure Handling
- Cache stale nếu đồng hồ hệ thống lùi (hiếm, chấp nhận được).
- WAL cần filesystem hỗ trợ — bọc try/except khi set PRAGMA.
- Overlap guard cần timeout tối đa để tự reset nếu 1 chu kỳ treo vĩnh viễn (deadlock), kèm cảnh báo bất thường.

### 11.9 Security Considerations
Không có tác động bảo mật trực tiếp.

### 11.10 Testing Plan
Unit test cache invalidation, WAL không phá vỡ query hiện có, overlap guard đúng hành vi cả 2 trường hợp.

### 11.11 Validation Criteria
- [ ] Dashboard load nhanh hơn đo được khi verify-log lớn.
- [ ] Không có 2 batch scan/verify chạy chồng nhau.
- [ ] Pool size đổi được qua config.

---

## 12. Detailed Plan — WP-G: Daemon Concurrency Model Fix

### 12.1 Objective
Làm cho luồng Scan và Verify thực sự độc lập, đúng như README mô tả — hoặc quyết định có chủ đích giữ nguyên và sửa lại tài liệu.

### 12.2 Current Implementation
`action_lock` (1 lock toàn cục) được `with action_lock:` bọc quanh khối submit `ThreadPoolExecutor` trong cả `run_daemon()` và `run_verify_daemon()`.

### 12.3 Gap Analysis
**ASSUMPTION cần xác nhận**: không rõ lock này có chủ đích (tránh 2 session SSH đồng thời cùng thiết bị) hay tình cờ. Cần git blame/lịch sử commit trước khi sửa; nếu không truy được lý do, mặc định giả thiết an toàn hơn: lock có chủ đích bảo vệ per-host.

### 12.4 Proposed Technical Solution
Thay 1 lock toàn cục bằng per-host lock (`{ip: threading.Lock()}`) — đảm bảo scan/verify CÙNG 1 IP vẫn không chạy chồng, nhưng KHÁC IP hoàn toàn độc lập. Làm SAU WP-F để tương thích với overlap-guard mới.

### 12.5 Files Expected to Change
```
MODIFY: oob_monitor.py   (thay action_lock bằng HostLockRegistry; run_daemon; run_verify_daemon)
TEST:   tests/test_daemon_concurrency.py   (MỚI)
```

### 12.6 Detailed Implementation Steps
1. Điều tra lịch sử (`git log -p`/`git blame`) quanh `action_lock`. Verify: có bằng chứng ghi lại lý do gốc, hoặc xác nhận không truy được.
2. Thiết kế `HostLockRegistry` (tạo lock theo IP on-demand, thread-safe). Verify: unit test 100 lock đồng thời không deadlock/race.
3. Thay `with action_lock:` bằng `with host_lock_registry.get(ip):` ở đúng phạm vi. Verify: scan IP-A và verify IP-B chạy đồng thời thật (đo thời gian).
4. Test cùng IP: scan IP-A và verify IP-A KHÔNG chạy chồng. Verify: instrument timeline start/end.
5. Regression toàn diện trên staging, 2-3 chu kỳ đầy đủ.

### 12.7 Data Flow
Không đổi data flow nghiệp vụ — chỉ đổi mức độ song song hoá threading.

### 12.8 Error & Failure Handling
Thiết kế per-host lock an toàn theo cả 2 kịch bản giả định (dù đúng hay sai về lý do lock cũ). `HostLockRegistry` cần cơ chế không phình to vô hạn (dọn lock không dùng định kỳ, hoặc chấp nhận phình nhẹ có chủ đích).

### 12.9 Security Considerations
Không có tác động bảo mật trực tiếp.

### 12.10 Testing Plan
Test đồng thời thật (không chỉ mock); test per-host mutual exclusion; regression toàn bộ daemon.

### 12.11 Validation Criteria
- [ ] Scan và Verify trên thiết bị KHÁC nhau chạy đồng thời được (đo thời gian).
- [ ] Scan và Verify trên CÙNG thiết bị không chạy chồng.
- [ ] README cập nhật khớp đúng hành vi thật sau khi sửa (hoặc sửa lại nếu quyết định giữ nguyên).

---

## 13. Detailed Plan — WP-H: Web/CLI UX Additions

### 13.1 Objective
Đóng các khoảng trống UX nhỏ: validate IP qua Web, cảnh báo config lỗi, sửa alias qua Web, daemon status thật trên Web, lịch monthly/one-shot.

### 13.2 Current / Gap / Solution

| # | Current | Gap | Solution |
|---|---|---|---|
| #10 | `api_device` POST không validate IP | IP rác vào `oob_ips.txt` | Áp `_IP_RE` (đã có sẵn) trước khi `add_ip()` |
| #11 | `load_config` nuốt lỗi JSON decode | Không ai biết config hỏng | Log cảnh báo rõ khi rơi vào except |
| #15 | `/api/device` chỉ có POST/DELETE | Không sửa alias được | Thêm `PUT` + `update_ip()` mới, sửa dòng tại chỗ (giữ thứ tự file) |
| #16 | Web chỉ đếm task tự tạo | Không biết `--daemon` CLI có chạy thật | API `/api/daemon-status` gọi lại `_get_daemon_status()` đã có |
| #17 | Chỉ hỗ trợ interval/daily/weekly | Thiếu "ngày N hàng tháng"/"1 lần" | Thêm mode `monthly`/`once` vào `compute_next_scheduled_run` |

### 13.3 Files Expected to Change
```
MODIFY: oob_monitor.py   (_IP_RE dùng lại ở web; load_config warning; update_ip() mới;
                           compute_next_scheduled_run mở rộng; DEFAULT_CONFIG thêm field lịch mới)
MODIFY: oob_web.py       (api_device validate + PUT; api_daemon_status route mới;
                           HTML/JS: sidebar daemon status, form sửa alias, Settings monthly/once)
MODIFY: README.md, api_documentation.md
TEST:   tests/test_schedule_extended.py, tests/test_update_ip.py
```

### 13.4 Detailed Implementation Steps
1. #10: validate IP trong `api_device` POST. Verify: IP sai -> 400, đúng -> 200 như cũ.
2. #11: log cảnh báo trong except của `load_config`. Verify: làm hỏng config, khởi động lại, cảnh báo xuất hiện.
3. #16: route `/api/daemon-status`, JS gọi định kỳ. Verify: chạy `--daemon` CLI song song web, trạng thái khớp.
4. #15: `update_ip(path, old_ip, new_alias, new_ip=None)` sửa đúng dòng, giữ thứ tự file; route `PUT /api/device`. Verify: sửa alias giữa danh sách, vị trí dòng không đổi.
5. #17: mở rộng `compute_next_scheduled_run` với `day_of_month`/`once_datetime`. Xử lý edge case ngày 31 vào tháng thiếu ngày (ASSUMPTION: dùng ngày cuối tháng, cần thống nhất). Logic "đã chạy xong 1 lần thì đổi mode" nằm ở call site, không phải trong hàm thuần. Verify: unit test đầy đủ edge case.
6. Cập nhật CLI `_edit_scan_schedule`/`_edit_verify_schedule` và Web Settings tab.

### 13.5 Data Flow
Không đổi luồng nghiệp vụ chính — mở rộng cấu hình/API bề mặt.

### 13.6 Error & Failure Handling
- #17 "once": nếu daemon restart trước giờ chạy, giữ nguyên lịch; nếu crash ngay sau khi chạy xong nhưng chưa kịp ghi lại trạng thái, có rủi ro chạy lại — cần persist "đã chạy lúc nào" ngay sau khi thực thi.
- #15: `new_ip` trùng IP khác đã tồn tại -> từ chối rõ ràng.

### 13.7 Security Considerations
#10 giảm rủi ro dữ liệu rác, không phải lỗ hổng nghiêm trọng. Các mục khác không có tác động bảo mật.

### 13.8 Testing Plan
Unit test từng hàm mới/mở rộng; integration test daemon-status khớp CLI/Web.

### 13.9 Validation Criteria
- [ ] IP sai định dạng bị từ chối qua Web.
- [ ] Config lỗi có cảnh báo rõ ràng.
- [ ] Sửa alias không làm mất vị trí dòng trong `oob_ips.txt`.
- [ ] Web hiển thị đúng trạng thái Daemon thật.
- [ ] Lịch monthly/once hoạt động đúng qua ít nhất 2 chu kỳ thử nghiệm thật.

---

## 14. Architecture Impact (tổng hợp)

| Khía cạnh | Ảnh hưởng |
|---|---|
| Existing architecture | Không đổi ranh giới 3-file hiện tại; không tách microservice |
| New components | `tests/`, `HostLockRegistry` (WP-G), `_wake_if_needed` (WP-A), cache layer verify-log (WP-F) |
| Interfaces affected | `push_menu_descriptions`/`push_vertiv_port_names` call sites (chữ ký không đổi, chỉ đổi giá trị truyền); `log_baseline_change`/`compute_next_scheduled_run` chữ ký đổi (tham số optional, backward compatible) |
| Backward compatibility | Mọi thay đổi config đều có default giữ nguyên hành vi cũ — không breaking change nếu không chủ động bật tính năng mới |
| Coupling impact | WP-C giảm coupling credential/plaintext; WP-G giảm coupling sai giữa 2 luồng; WP-E giảm trùng lặp logic |
| Scalability impact | WP-F/WP-G là 2 WP duy nhất impact trực tiếp khả năng mở rộng theo số thiết bị |

## 15. Security Impact (tổng hợp)

- WP-C: cải thiện bảo mật rõ rệt.
- WP-B: rủi ro vận hành cao nhất — cần review riêng bằng `security-review`, rollout thận trọng (lab trước).
- Các WP khác trung tính về bảo mật.

## 16. Network / Production Impact

Áp dụng khung `CURRENT STATE -> DESIRED STATE -> VALIDATION -> DIFF -> RISK ANALYSIS -> DEPLOY -> VERIFY -> AUDIT`:

- **WP-A/D**: RISK = THẤP (chỉ đọc); rollout dần, bật `debug_verify` theo dõi vài chu kỳ đầu.
- **WP-B**: RISK = TRUNG BÌNH-CAO (lần đầu hệ thống thực sự đổi cấu hình thiết bị production); khuyến nghị bật `push_live_mode` cho 1 thiết bị non-critical trước, theo dõi vài ngày, rồi mở rộng. Không bỏ qua giai đoạn nào trong khung trên cho WP-B.

## 17. Testing Strategy (tổng hợp)

- Unit tests: mọi hàm thuần trong WP-A (mock session), WP-D (mock time), WP-C (round-trip encrypt), WP-E (parser fixtures), WP-F (cache), WP-H (schedule math).
- Integration tests: WP-A (Live Debug trên port thật), WP-B (push thật trên thiết bị non-critical), WP-G (đo concurrency thật), WP-H #16 (daemon status khớp CLI/Web thật).
- Regression tests: sau MỖI phase, chạy lại Scan+Verify trên toàn bộ thiết bị hiện có, so sánh OK/CANH BAO/TIMEOUT trước-sau.
- Failure/edge cases bắt buộc: thiết bị unreachable, sai auth, timeout thật, CLI output bất thường, partial failure khi push nhiều option, rollback qua Revert.

## 18. Migration Strategy

- Config: mọi field mới có default giữ nguyên hành vi cũ, không cần script migrate bắt buộc; WP-C tự nâng cấp plaintext -> mã hoá ở lần save đầu tiên.
- Database: **không có thay đổi schema SQL nào** trong toàn bộ 24 hạng mục — WP-D chỉ mở rộng file log.
- Code: merge theo đúng thứ tự Phase (mục 22), mỗi Phase 1 PR riêng, review riêng.

## 19. Risks (tổng hợp theo WP)

| WP | Technical | Security | Operational | Regression | Migration |
|---|---|---|---|---|---|
| WP-A | MEDIUM | LOW | LOW | MEDIUM | LOW |
| WP-B | MEDIUM | **HIGH** | **HIGH** | LOW | LOW |
| WP-C | LOW | LOW (giảm risk) | LOW | LOW | MEDIUM (nếu key không backup) |
| WP-D | LOW | LOW | LOW | LOW | LOW |
| WP-E | MEDIUM | LOW | LOW | MEDIUM-HIGH (giảm nhờ test-first) | LOW |
| WP-F | LOW-MEDIUM | LOW | LOW | LOW | LOW |
| WP-G | MEDIUM | LOW | MEDIUM | MEDIUM | LOW |
| WP-H | LOW | LOW | LOW | LOW | LOW |

**Rủi ro cao nhất toàn kế hoạch**: WP-B (Operational + Security HIGH) — cần rollout riêng, không triển khai cùng tốc độ các WP khác.

## 20. Out-of-Scope Findings

- **Vấn đề**: `oob_web.py` import toàn bộ `oob_monitor` module thay vì qua interface rõ ràng. **Impact**: refactor `oob_monitor.py` có rủi ro âm thầm phá vỡ `oob_web.py`. **Đề xuất tương lai**: tách lớp service/facade — không làm trong đợt này.
- **Vấn đề**: Không có rate-limiting/CSRF cho API ghi của Web. **Đề xuất tương lai**: Flask-Limiter + CSRF token, quyết định riêng ở roadmap bảo mật kế tiếp.
- **Vấn đề**: `AutoAddPolicy()` chấp nhận mọi SSH host key. **Đề xuất tương lai**: tuỳ chọn `known_hosts` cấu hình được.
- **Vấn đề**: Không có circuit-breaker cho thiết bị liên tục lỗi nhiều chu kỳ. **Đề xuất tương lai**: đánh giá sau khi WP-F/G có số liệu thật.
- **Vấn đề**: `.docx` hướng dẫn sử dụng chưa đối chiếu với hành vi thật sau các WP này. **Đề xuất tương lai**: cập nhật sau Phase 3 (WP-B).

## 21. Prioritization

| WP | Priority | Lý do |
|---|---|---|
| WP-A | **P0** | Đúng vấn đề người dùng đang gặp, ảnh hưởng tính năng cốt lõi |
| WP-D | **P0** | Gộp cùng phase với WP-A, tự thân là bug an toàn + audit gap |
| WP-B | **P0** | Sửa lỗi "false success" nghiêm trọng nhất đã xác nhận ở audit |
| WP-C | **P0** | Bảo mật credential, chi phí sửa thấp |
| WP-E | **P1** | Nền tảng chất lượng dài hạn, không chặn use-case hiện tại |
| WP-F | **P1/P2** | Cache verify-log (rẻ, lợi ích ngay) sớm hơn; WAL/pool có thể P2 |
| WP-G | **P2** | Đúng theo README nhưng chưa gây sự cố thực tế được báo cáo |
| WP-H | **P2/P3** | Cải thiện UX/vận hành, không khẩn cấp |

---

## 22. Final Implementation Roadmap

### PHASE 0 — Preparation
- Objective: chuẩn bị nền tảng an toàn trước khi sửa logic nghiệp vụ.
- Tasks: (a) thêm `pytest` + cấu trúc `tests/`; (b) thu thập fixture dữ liệu thật (ẩn danh hoá); (c) mở rộng `debug_dump()` với timing (WP-A #24, tách làm trước để validate Phase 2).
- Files/modules: `tests/` (mới), `requirements-dev.txt` (mới), `oob_monitor.py::debug_dump`.
- Skills: không có skill network-automation thật sự khả dụng trong phiên này.
- Dependencies: không có.
- Risks: LOW.
- Validation: `pytest` chạy được; debug log có timestamp.
- DoD: hạ tầng test sẵn sàng, chưa đổi hành vi sản phẩm nào.

### PHASE 1 — Foundation
- Objective: hoàn thành cải tiến an toàn, độc lập, ít rủi ro nhất.
- Tasks: WP-C (toàn bộ) song song WP-E(a) (test-first cho parser/diff/schedule hiện có).
- Files/modules: `oob_monitor.py` (load_config/save_config), `oob_web.py` (api_config), `requirements.txt`, `tests/test_parse_menu.py`, `tests/test_options_diff.py`, `tests/test_schedule.py`.
- Skills: `security-review` (bắt buộc trước khi merge WP-C).
- Dependencies: Phase 0.
- Risks: LOW.
- Validation: mục 9.11 + test characterization PASS.
- DoD: credential được mã hoá; bộ test nền tảng tồn tại và PASS trên code hiện tại.

### PHASE 2 — Core Reliability Fix
- Objective: giải quyết vấn đề người dùng báo cáo (TIMEOUT/mất data) + timeout tổng + audit trail baseline.
- Tasks: WP-A (toàn bộ) + WP-D (toàn bộ), CHUNG 1 phase/1 PR do chung hàm.
- Files/modules: `oob_monitor.py::run_deep_verify/check_port_via_oob/log_baseline_change`, `oob_web.py` (Settings 2 field wait mới), `README.md`.
- Skills: `code-review` (bắt buộc).
- Dependencies: Phase 0 (debug timing), Phase 1 khuyến nghị xong trước.
- Risks: MEDIUM (kỹ thuật), LOW (bảo mật/vận hành).
- Validation: mục 6.11 + 7.11.
- DoD: Deep Verify khớp pivot thủ công; `max_verify_duration` có tác dụng thật; baseline update có diff persist.

### PHASE 3 — Trust/Push Integrity
- Objective: chấm dứt push/revert giả-thành-công, cho phép bật push thật có kiểm soát.
- Tasks: WP-B (toàn bộ).
- Files/modules: `oob_lib.py`, `oob_monitor.py::process_push_and_reverify`, `oob_web.py::api_action/api_revert`, tài liệu.
- Skills: `security-review` + `code-review` (bắt buộc, rủi ro cao nhất kế hoạch).
- Dependencies: **HARD** — Phase 2 phải xong và ổn định.
- Risks: HIGH — rollout theo mục 16.
- Validation: mục 8.11.
- DoD: mặc định vẫn an toàn (dry_run); khi bật thật, đã kiểm chứng trên lab/thiết bị test.

### PHASE 4 — Parser Consolidation
- Objective: loại bỏ trùng lặp logic parser, dựa trên nền test đã có từ Phase 1.
- Tasks: WP-E(b).
- Files/modules: `oob_lib.py::parse_menu`, `oob_monitor.py::poll_host_multi`.
- Skills: `simplify`, `code-review`.
- Dependencies: **HARD** — WP-E(a) test PASS trước.
- Risks: MEDIUM, giảm nhờ test-first.
- Validation: mục 10.11.
- DoD: 1 implementation parser duy nhất, toàn bộ test cũ vẫn PASS.

### PHASE 5 — Performance & Concurrency
- Objective: tối ưu đường đọc, chuẩn bị khả năng mở rộng, sửa mô hình concurrency daemon.
- Tasks: WP-F trước, WP-G sau (hard dependency).
- Files/modules: `oob_monitor.py` (`_parse_verify_logs_for_status`, `_init_db`, `run_daemon`, `run_verify_daemon`), `oob_web.py`.
- Skills: `code-review`.
- Dependencies: Phase 2 nên xong trước.
- Risks: LOW-MEDIUM (WP-F), MEDIUM (WP-G).
- Validation: mục 11.11 + 12.11.
- DoD: cache hoạt động đo được; 2 luồng daemon độc lập thật (hoặc tài liệu sửa lại nếu quyết định giữ nguyên).

### PHASE 6 — UX Additions
- Objective: hoàn thiện khoảng trống UX nhỏ.
- Tasks: WP-H (5 mục con, có thể song song Phase 4/5).
- Files/modules: theo mục 13.3.
- Skills: không cần đặc biệt.
- Dependencies: mềm.
- Risks: LOW.
- Validation: mục 13.9.
- DoD: 5 tính năng nhỏ hoạt động, có test.

### PHASE 7 — Hardening & Documentation
- Objective: rà soát toàn diện trước khi coi 24 hạng mục hoàn tất.
- Tasks: chạy `security-review` cho toàn bộ diff tích luỹ; chạy `code-review` mức `high`; cập nhật README/api_documentation/.docx cho khớp hành vi thật; regression Scan+Verify đầy đủ trên toàn bộ thiết bị thật.
- Files/modules: tài liệu, toàn bộ test suite.
- Skills: `security-review`, `code-review`.
- Dependencies: tất cả Phase 1-6 hoàn tất.
- Risks: LOW (bước xác nhận).
- Validation: Definition of Done tổng thể (mục 23).
- DoD: toàn bộ tiêu chí mục 23 hoàn thành.

---

## 23. Definition of Done (toàn bộ scope)

**Functional**
- Deep Verify khớp pivot thủ công trên thiết bị/port đã từng báo lỗi.
- Push/Revert phản ánh đúng sự thật (mô phỏng hay thật) trong mọi log/UI.
- Baseline drift có lịch sử diff tra cứu được.
- Credential được mã hoá tại rest; API không rò rỉ mật khẩu.
- 2 luồng daemon độc lập đúng như tài liệu (hoặc tài liệu sửa khớp thực tế).

**Technical**
- Không còn code parser Cisco menu trùng lặp.
- `max_verify_duration` có tác dụng thật.
- Pool size, wait timing SSH/Telnet cấu hình được.

**Security**
- `security-review` chạy trên toàn bộ diff, không còn finding CRITICAL/HIGH mới phát sinh từ các thay đổi này.
- `push_live_mode` mặc định tắt, rollout thận trọng khi bật.

**Testing**
- Bộ test `pytest` PASS 100% cho các hàm thuần.
- Test tích hợp trên lab/thiết bị thật xác nhận WP-A và WP-B.
- Regression: không có thoái lui trên thiết bị đang hoạt động bình thường.

**Network**
- Deep Verify không gây kết nối chồng chéo/gây nhiễu thiết bị.
- Push thật (nếu bật) đã xác nhận đúng bằng cách đọc lại cấu hình thiết bị sau push.

**Documentation**
- README.md, api_documentation.md cập nhật khớp hành vi thật.
- `.docx` hướng dẫn sử dụng được rà soát lại (khuyến nghị Phase 7).

**Regression**
- Toàn bộ chức năng Scan/Import/Export Excel/Search/Multi-account không bị ảnh hưởng.

---

**Trạng thái: kế hoạch — chưa triển khai.** Sẵn sàng bắt đầu từ PHASE 0 khi được duyệt.

import sqlite3
import json
import os
import threading
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify

# Import thẳng các hàm lõi từ tool CLI (Đã cập nhật chuẩn Vertiv)
import oob_monitor

app = Flask(__name__)

# --- CÁC HÀM TIỆN ÍCH ---
def get_ips():
    return oob_monitor.load_ip_list_cached(oob_monitor.CONFIG_FILE_DEFAULT.replace("json", "txt").replace("oob_config", "oob_ips"))

def query_db(query, args=(), fetchall=True):
    if not os.path.exists(oob_monitor.DEFAULT_CONFIG["baseline_db"]): return []
    try:
        conn = sqlite3.connect(oob_monitor.DEFAULT_CONFIG["baseline_db"])
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall() if fetchall else cur.fetchone()
        conn.close()
        return rv
    except Exception: return []

# --- CÁC TIẾN TRÌNH CHẠY NGẦM (Tránh treo Web) ---
def _web_print(msg): 
    pass # Ẩn log CLI để Web không bị rác

def bg_task_scan(target_ip=None):
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    hosts = oob_monitor.load_ip_list(cfg["ip_list"])
    if target_ip: hosts = [h for h in hosts if h[0] == target_ip]

    for ip, alias in hosts:
        alive = oob_monitor.ping_host(ip)
        oob_monitor.save_device_status(ip, alias=alias, ping=alive)
        if not alive: continue

        try: hostname, menu_name, snapshot, menu_state = oob_monitor.poll_host_multi(ip, cfg, timeout=10)
        except Exception: oob_monitor.save_device_status(ip, alias=alias, menu_state="conn_failed"); continue

        oob_monitor.save_device_status(ip, alias=alias, menu_state=menu_state)
        if menu_state in ["fetch_failed", "no_menu"] or not snapshot: continue

        oob_monitor.save_options(cfg["snapshot_db"], "snapshot_menu", ip, menu_name, hostname, snapshot)
        _mn, _dn, baseline = oob_monitor.get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)

        if baseline is None or not oob_monitor.options_equal(baseline, snapshot):
            oob_monitor.save_options(cfg["baseline_db"], "baseline_menu", ip, menu_name, hostname, snapshot)
            oob_monitor.log_baseline_change(alias, ip, "CAP NHAT QUA WEB")

def bg_task_verify(target_ip=None):
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    hosts = oob_monitor.load_ip_list(cfg["ip_list"])
    if target_ip: hosts = [h for h in hosts if h[0] == target_ip]

    for ip, alias in hosts:
        _mn, _dn, baseline = oob_monitor.get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if baseline: oob_monitor.run_deep_verify(cfg, alias, ip, baseline, print_fn=_web_print)

def bg_task_push(target_ip=None):
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    hosts = oob_monitor.load_ip_list(cfg["ip_list"])
    if target_ip: hosts = [h for h in hosts if h[0] == target_ip]

    for ip, alias in hosts:
        _mn, _dn, baseline = oob_monitor.get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if not baseline: continue
        
        vendor = next(iter(baseline.values())).get("vendor", "cisco") if baseline else "cisco"
        if vendor == "vertiv": continue # Vertiv chưa hỗ trợ Push Config
        
        results = oob_monitor.run_deep_verify(cfg, alias, ip, baseline, print_fn=_web_print)
        if any(r["status"] == "CANH BAO" for r in results):
            oob_monitor.process_push_and_reverify(cfg, alias, ip, baseline, results, print_fn=_web_print)


# --- API ENDPOINTS ---
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT))
    else:
        cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
        cfg.update(request.json)
        oob_monitor.save_config(oob_monitor.CONFIG_FILE_DEFAULT, cfg)
        return jsonify({"status": "success"})

@app.route("/api/device", methods=["POST", "DELETE"])
def api_device():
    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    if request.method == "POST":
        data = request.json
        oob_monitor.add_ip(cfg["ip_list"], data["ip"], data.get("alias"))
        return jsonify({"status": "success"})
    else:
        ip = request.json.get("ip")
        oob_monitor.remove_ip(cfg["ip_list"], ip)
        return jsonify({"status": "success"})

@app.route("/api/action", methods=["POST"])
def api_action():
    action = request.json.get("action")
    target_ip = request.json.get("ip") 
    
    if action == "scan":
        threading.Thread(target=bg_task_scan, args=(target_ip,)).start()
    elif action == "verify":
        threading.Thread(target=bg_task_verify, args=(target_ip,)).start()
    elif action == "push":
        threading.Thread(target=bg_task_push, args=(target_ip,)).start()
        
    return jsonify({"status": "success", "msg": f"Đã đưa lệnh {action.upper()} vào chạy ngầm!"})

@app.route("/api/search")
def api_search():
    """API Tìm kiếm siêu tốc trên Web"""
    query = request.args.get("q", "").strip().lower()
    if not query: return jsonify([])

    cfg = oob_monitor.load_config(oob_monitor.CONFIG_FILE_DEFAULT)
    hosts = oob_monitor.load_ip_list(cfg["ip_list"])
    verify_st = oob_monitor._parse_verify_logs_for_status(max_age_hours=24.0 * 30)

    found = []
    for ip, alias in hosts:
        _mn, dn, source = oob_monitor.get_options_by_host(cfg["baseline_db"], "baseline_menu", ip)
        if source is None: _mn, dn, source = oob_monitor.get_options_by_host(cfg["snapshot_db"], "snapshot_menu", ip)
        if not source: continue
        dn = dn or alias
        
        for key, entry in source.items():
            act_host = verify_st.get((alias, key), {}).get("act_host", "") or ""
            score = 0
            
            # Chấm điểm mức độ khớp ưu tiên
            if query == act_host.lower(): score = 100
            elif query in act_host.lower(): score = 90
            elif query == alias.lower() or query == ip: score = 85
            elif query == dn.lower(): score = 80
            elif query in alias.lower() or query in dn.lower(): score = 75
            elif query in entry.get("description", "").lower(): score = 60
            elif query in entry.get("ip", "").lower(): score = 50
            elif query == key.lower(): score = 40
            
            if score > 0:
                found.append({
                    "score": score, "oob_ip": ip, "oob_alias": alias, "oob_host": dn,
                    "opt_key": key, "desc": entry.get("description", ""),
                    "target_ip": entry.get("ip", ""), "target_port": entry.get("port", 23),
                    "protocol": entry.get("protocol", "telnet"), "act_host": act_host
                })
    
    found.sort(key=lambda x: x["score"], reverse=True)
    return jsonify(found)


# --- COMMON HTML BLOCKS (Dùng chung cho cả 2 trang) ---
COMMON_MODALS_JS = """
    <!-- Modal Cài đặt Hệ thống -->
    <div class="modal fade" id="settingsModal" tabindex="-1" data-bs-theme="dark">
        <div class="modal-dialog">
            <div class="modal-content bg-dark text-light border-secondary">
                <div class="modal-header border-secondary">
                    <h5 class="modal-title">Cài đặt Hệ thống</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <form id="settingsForm">
                        <div class="mb-3"><label>Username</label><input type="text" class="form-control bg-dark text-light" id="cfg_user"></div>
                        <div class="mb-3"><label>Password</label><input type="password" class="form-control bg-dark text-light" id="cfg_pass"></div>
                        <div class="mb-3"><label>Enable Password</label><input type="password" class="form-control bg-dark text-light" id="cfg_en_pass"></div>
                        <div class="mb-3"><label>Vertiv Connect Password</label><input type="password" class="form-control bg-dark text-light" id="cfg_vt_pass"></div>
                        <div class="form-check form-switch mb-3">
                            <input class="form-check-input" type="checkbox" id="cfg_auto_verify">
                            <label class="form-check-label">Bật Tự động Verify Ngầm (Daemon)</label>
                        </div>
                    </form>
                </div>
                <div class="modal-footer border-secondary">
                    <button type="button" class="btn btn-primary" onclick="saveSettings()">Lưu thay đổi</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal Thêm Thiết bị -->
    <div class="modal fade" id="addDeviceModal" tabindex="-1" data-bs-theme="dark">
        <div class="modal-dialog">
            <div class="modal-content bg-dark text-light border-secondary">
                <div class="modal-header border-secondary">
                    <h5 class="modal-title">Thêm Thiết bị OOB</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <div class="mb-3"><label>IP Address</label><input type="text" class="form-control bg-dark text-light" id="new_ip"></div>
                    <div class="mb-3"><label>Alias (Tên gọi)</label><input type="text" class="form-control bg-dark text-light" id="new_alias"></div>
                </div>
                <div class="modal-footer border-secondary">
                    <button type="button" class="btn btn-success" onclick="addDevice()">Thêm mới</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal Popup HIỂN THỊ KẾT QUẢ TÌM KIẾM -->
    <div class="modal fade" id="searchModal" tabindex="-1" data-bs-theme="dark">
        <div class="modal-dialog modal-xl">
            <div class="modal-content bg-dark text-light border-secondary">
                <div class="modal-header border-secondary bg-primary text-white">
                    <h5 class="modal-title"><i class="bi bi-search"></i> Kết quả tìm kiếm cho: "<span id="searchKeyword" class="fw-bold"></span>"</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body p-0">
                    <div class="table-responsive">
                        <table class="table table-dark table-hover table-striped align-middle m-0">
                            <thead>
                                <tr>
                                    <th class="ps-3">Thuộc OOB</th>
                                    <th>Menu/Port</th>
                                    <th>Description</th>
                                    <th>Hostname Thực tế</th>
                                    <th>Kết nối Đích</th>
                                    <th class="pe-3">Hành động</th>
                                </tr>
                            </thead>
                            <tbody id="searchResultsBody">
                                <!-- Dữ liệu API đổ vào đây -->
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Toast Notification (Thông báo góc dưới) -->
    <div class="toast-container position-fixed bottom-0 end-0 p-3">
        <div id="liveToast" class="toast align-items-center text-bg-primary border-0" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body" id="toastMsg"></div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        const toastEl = document.getElementById('liveToast');
        const toast = new bootstrap.Toast(toastEl, {delay: 3000});

        function showToast(msg, isError=false) {
            document.getElementById('toastMsg').innerText = msg;
            toastEl.className = isError ? 'toast align-items-center text-bg-danger border-0' : 'toast align-items-center text-bg-success border-0';
            toast.show();
        }

        async function triggerAction(action, ip) {
            let confirmMsg = ip ? `Xác nhận chạy lệnh ${action.toUpperCase()} cho IP ${ip}?` : `Xác nhận chạy lệnh ${action.toUpperCase()} cho TOÀN BỘ thiết bị?`;
            if(!confirm(confirmMsg)) return;

            let res = await fetch('/api/action', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: action, ip: ip})
            });
            let data = await res.json();
            showToast(data.msg);
        }

        async function openSettings() {
            let res = await fetch('/api/config');
            let cfg = await res.json();
            document.getElementById('cfg_user').value = cfg.username || '';
            document.getElementById('cfg_pass').value = cfg.password || '';
            document.getElementById('cfg_en_pass').value = cfg.enable_password || '';
            document.getElementById('cfg_vt_pass').value = cfg.vertiv_connect_password || '';
            document.getElementById('cfg_auto_verify').checked = cfg.auto_verify;
            new bootstrap.Modal(document.getElementById('settingsModal')).show();
        }

        async function saveSettings() {
            let payload = {
                username: document.getElementById('cfg_user').value,
                password: document.getElementById('cfg_pass').value,
                enable_password: document.getElementById('cfg_en_pass').value,
                vertiv_connect_password: document.getElementById('cfg_vt_pass').value,
                auto_verify: document.getElementById('cfg_auto_verify').checked
            };
            await fetch('/api/config', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            showToast('Đã lưu cấu hình!');
            bootstrap.Modal.getInstance(document.getElementById('settingsModal')).hide();
        }

        function openAddDevice() {
            document.getElementById('new_ip').value = '';
            document.getElementById('new_alias').value = '';
            new bootstrap.Modal(document.getElementById('addDeviceModal')).show();
        }

        async function addDevice() {
            let payload = { ip: document.getElementById('new_ip').value, alias: document.getElementById('new_alias').value };
            if(!payload.ip) return alert("Vui lòng nhập IP");
            await fetch('/api/device', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            location.reload();
        }

        async function deleteDevice(ip) {
            if(!confirm(`Xóa OOB ${ip}?`)) return;
            await fetch('/api/device', { method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ip: ip}) });
            location.reload();
        }

        // --- JS TÌM KIẾM ---
        async function executeSearch() {
            let q = document.getElementById('searchInput').value.trim();
            if(!q) return;
            document.getElementById('searchKeyword').innerText = q;
            
            let res = await fetch('/api/search?q=' + encodeURIComponent(q));
            let data = await res.json();
            
            let tbody = document.getElementById('searchResultsBody');
            tbody.innerHTML = '';
            
            if(data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="text-center text-warning py-4">Không tìm thấy thiết bị nào khớp!</td></tr>';
            } else {
                data.forEach(item => {
                    let actHostHtml = item.act_host ? `<span class="text-success fw-bold">${item.act_host}</span>` : '<span class="text-muted">-</span>';
                    tbody.innerHTML += `
                        <tr>
                            <td class="ps-3"><strong class="text-info">${item.oob_alias}</strong><br><small class="text-muted">${item.oob_ip}</small></td>
                            <td><kbd>${item.opt_key}</kbd></td>
                            <td>${item.desc}</td>
                            <td>${actHostHtml}</td>
                            <td><code>${item.protocol}://${item.target_ip}:${item.target_port}</code></td>
                            <td class="pe-3"><a href="/device/${item.oob_ip}" class="btn btn-sm btn-outline-primary" title="Tới thiết bị OOB"><i class="bi bi-box-arrow-in-right"></i> Đi tới</a></td>
                        </tr>
                    `;
                });
            }
            
            let myModal = new bootstrap.Modal(document.getElementById('searchModal'));
            myModal.show();
        }
    </script>
</body>
</html>
"""

# --- GIAO DIỆN CHÍNH (Dashboard) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OOB Manager Web Panel</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <style>
        body { background-color: #121212; color: #e0e0e0; }
        .card { background-color: #1e1e1e; border: 1px solid #333; }
        .table-dark { background-color: #1e1e1e; }
        .stat-card { border-left: 4px solid #0d6efd; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark border-bottom border-secondary mb-4">
        <div class="container-fluid">
            <a class="navbar-brand fw-bold" href="/"><i class="bi bi-hdd-network text-primary"></i> OOB Web Panel</a>
            
            <!-- THANH TÌM KIẾM TRÊN NAVBAR -->
            <form class="d-flex ms-auto me-4" onsubmit="event.preventDefault(); executeSearch();">
                <input class="form-control me-2 bg-dark text-light border-secondary" type="search" id="searchInput" placeholder="Tìm tên Port, IP, Hostname..." aria-label="Search" style="width: 320px;">
                <button class="btn btn-outline-info" type="submit"><i class="bi bi-search"></i></button>
            </form>

            <div>
                <button class="btn btn-outline-info btn-sm me-2" onclick="openSettings()"><i class="bi bi-gear"></i> Cài đặt</button>
                <button class="btn btn-outline-success btn-sm me-2" onclick="openAddDevice()"><i class="bi bi-plus-lg"></i> Thêm OOB</button>
                <span class="text-muted small"><i class="bi bi-clock"></i> {{ time_now }}</span>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        <!-- Nút Hành động Tổng -->
        <div class="card shadow-sm mb-4 border-secondary">
            <div class="card-body bg-dark d-flex gap-2">
                <button class="btn btn-primary" onclick="triggerAction('scan', null)"><i class="bi bi-search"></i> Scan All (Thu thập Data)</button>
                <button class="btn btn-warning" onclick="triggerAction('verify', null)"><i class="bi bi-lightning"></i> Verify All (Kiểm tra cáp)</button>
                <button class="btn btn-danger" onclick="triggerAction('push', null)"><i class="bi bi-upload"></i> Push Config All (Sửa lỗi Desc)</button>
            </div>
        </div>

        <!-- Bảng danh sách thiết bị -->
        <div class="card shadow mb-4 border-secondary">
            <div class="card-header py-3 bg-dark border-secondary">
                <h6 class="m-0 fw-bold text-primary"><i class="bi bi-list-ul"></i> Danh sách OOB Devices ({{ stats.total }} thiết bị)</h6>
            </div>
            <div class="card-body bg-dark p-0">
                <div class="table-responsive">
                    <table class="table table-dark table-hover table-striped align-middle m-0">
                        <thead>
                            <tr>
                                <th class="ps-4">Alias</th>
                                <th>IP Address</th>
                                <th>Ping</th>
                                <th>Trạng thái Menu</th>
                                <th>Tổng Line</th>
                                <th>Cập nhật lần cuối</th>
                                <th class="pe-4">Hành động OOB</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for dev in devices %}
                            <tr>
                                <td class="fw-bold ps-4">{{ dev.alias }}</td>
                                <td><code>{{ dev.ip }}</code></td>
                                <td>
                                    {% if dev.ping == True %}<span class="badge bg-success">Online</span>
                                    {% elif dev.ping == False %}<span class="badge bg-danger">Offline</span>
                                    {% else %}<span class="badge bg-secondary">-</span>{% endif %}
                                </td>
                                <td>
                                    {% if dev.menu_state == 'ok' %}<span class="badge bg-success">OK</span>
                                    {% elif dev.menu_state == 'conn_failed' %}<span class="badge bg-danger">Lỗi Connect</span>
                                    {% else %}<span class="badge bg-secondary">{{ dev.menu_state or '-' }}</span>{% endif %}
                                </td>
                                <td><span class="text-info fw-bold">{{ dev.opt_count }}</span></td>
                                <td class="text-muted small">{{ dev.checked_at }}</td>
                                <td class="pe-4">
                                    <div class="btn-group btn-group-sm">
                                        <a href="/device/{{ dev.ip }}" class="btn btn-outline-light" title="Xem chi tiết line"><i class="bi bi-eye"></i></a>
                                        <button class="btn btn-outline-primary" onclick="triggerAction('scan', '{{ dev.ip }}')" title="Scan OOB"><i class="bi bi-search"></i></button>
                                        <button class="btn btn-outline-warning" onclick="triggerAction('verify', '{{ dev.ip }}')" title="Verify OOB"><i class="bi bi-lightning"></i></button>
                                        <button class="btn btn-outline-danger" onclick="triggerAction('push', '{{ dev.ip }}')" title="Push OOB"><i class="bi bi-upload"></i></button>
                                        <button class="btn btn-outline-secondary" onclick="deleteDevice('{{ dev.ip }}')" title="Xóa OOB"><i class="bi bi-trash"></i></button>
                                    </div>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
""" + COMMON_MODALS_JS

# --- GIAO DIỆN CHI TIẾT OOB ---
DETAIL_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chi tiết OOB: {{ ip }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <style>body { background-color: #121212; color: #e0e0e0; } .card { background-color: #1e1e1e; border: 1px solid #333; } .table-dark { background-color: #1e1e1e; }</style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark border-bottom border-secondary mb-4">
        <div class="container-fluid">
            <a class="navbar-brand fw-bold" href="/"><i class="bi bi-arrow-left"></i> Trở về Dashboard</a>
            
            <!-- THANH TÌM KIẾM TRÊN NAVBAR -->
            <form class="d-flex ms-auto me-4" onsubmit="event.preventDefault(); executeSearch();">
                <input class="form-control me-2 bg-dark text-light border-secondary" type="search" id="searchInput" placeholder="Tìm tên Port, IP, Hostname..." aria-label="Search" style="width: 320px;">
                <button class="btn btn-outline-info" type="submit"><i class="bi bi-search"></i></button>
            </form>
        </div>
    </nav>

    <div class="container">
        <div class="card shadow mb-4 border-secondary">
            <div class="card-header py-3 bg-dark d-flex justify-content-between align-items-center border-secondary">
                <h4 class="m-0 fw-bold text-primary">Cấu hình Menu OOB: {{ ip }}</h4>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-primary" onclick="triggerAction('scan', '{{ ip }}')" title="Scan"><i class="bi bi-search"></i> Quét Lại Data</button>
                    <button class="btn btn-outline-warning" onclick="triggerAction('verify', '{{ ip }}')" title="Verify"><i class="bi bi-lightning"></i> K.Tra Dây Cắm</button>
                    <button class="btn btn-outline-danger" onclick="triggerAction('push', '{{ ip }}')" title="Push"><i class="bi bi-upload"></i> Sửa Lỗi Desc</button>
                </div>
            </div>
            <div class="card-body bg-dark p-0">
                {% if options %}
                <div class="row p-3 m-0 border-bottom border-secondary">
                    <div class="col-md-6"><p class="mb-0"><strong>Tên thiết bị (Hostname OOB):</strong> {{ options[0]['device_name'] }}</p></div>
                    <div class="col-md-6 text-end"><p class="mb-0"><strong>Hãng sản xuất:</strong> <span class="badge bg-secondary text-uppercase">{{ options[0]['vendor'] }}</span></p></div>
                </div>
                <div class="table-responsive">
                    <table class="table table-dark table-striped table-hover m-0">
                        <thead>
                            <tr>
                                <th class="ps-4">Phím Menu / Cổng</th>
                                <th>Mô tả (Description)</th>
                                <th>Target IP</th>
                                <th>Target Port</th>
                                <th class="pe-4">Giao thức</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for opt in options %}
                            <tr>
                                <td class="ps-4"><kbd>{{ opt['option_key'] }}</kbd></td>
                                <td>{{ opt['description'] }}</td>
                                <td><code>{{ opt['target_ip'] }}</code></td>
                                <td>{{ opt['target_port'] }}</td>
                                <td class="pe-4"><span class="badge {% if opt['protocol'] == 'ssh' %}bg-success{% elif opt['protocol'] == 'serial' %}bg-info text-dark{% else %}bg-warning text-dark{% endif %}">{{ opt['protocol'] | upper }}</span></td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="alert alert-warning m-4">
                    <i class="bi bi-exclamation-circle"></i> Chưa có dữ liệu Baseline cho thiết bị này, hoặc thiết bị chưa được Scan.
                </div>
                {% endif %}
            </div>
        </div>
    </div>
""" + COMMON_MODALS_JS

# --- ROUTING GIAO DIỆN ---
@app.route("/")
def index():
    ips = get_ips()
    dev_status = oob_monitor.load_device_status()
    devices = []
    stats = {"total": len(ips), "online": 0, "offline": 0, "has_baseline": 0, "err_menu": 0}

    for item in ips:
        ip, alias = item["ip"], item["alias"]
        status = dev_status.get(ip, {})
        
        if status.get("ping") is True: stats["online"] += 1
        elif status.get("ping") is False: stats["offline"] += 1
            
        if status.get("menu_state") in ["conn_failed", "fetch_failed"]: stats["err_menu"] += 1

        opts = query_db("SELECT count(*) as cnt FROM baseline_menu WHERE host=?", (ip,))
        opt_count = opts[0]["cnt"] if opts else 0
        if opt_count > 0: stats["has_baseline"] += 1

        devices.append({
            "alias": alias, "ip": ip, "ping": status.get("ping"), "menu_state": status.get("menu_state"),
            "checked_at": status.get("checked_at", "-"), "opt_count": opt_count
        })

    time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template_string(HTML_TEMPLATE, devices=devices, stats=stats, time_now=time_now)

@app.route("/device/<ip>")
def device_detail(ip):
    options = query_db("SELECT * FROM baseline_menu WHERE host=? ORDER BY CAST(option_key AS INTEGER)", (ip,))
    return render_template_string(DETAIL_TEMPLATE, ip=ip, options=[dict(row) for row in options])

if __name__ == "__main__":
    print("=========================================================")
    print("🚀 GIAO DIỆN WEB OOB (CÓ THANH SEARCH) ĐÃ KHỞI ĐỘNG")
    print("👉 Mở trình duyệt và truy cập: http://127.0.0.1:5000")
    print("=========================================================")
    app.run(host="0.0.0.0", port=5000, debug=False)
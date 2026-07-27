import sqlite3
import json
import os
from datetime import datetime
from flask import Flask, render_template_string

app = Flask(__name__)

# --- CONFIG ---
CONFIG_FILE = "oob_config.json"
BASELINE_DB = "baseline.db"
DEVICE_STATUS = "device_status.json"
IP_LIST_FILE = "oob_ips.txt"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def load_device_status():
    if os.path.exists(DEVICE_STATUS):
        with open(DEVICE_STATUS, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def get_ips():
    hosts = []
    if os.path.exists(IP_LIST_FILE):
        with open(IP_LIST_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    parts = line.split()
                    hosts.append({"ip": parts[0], "alias": parts[1] if len(parts) > 1 else parts[0]})
    return hosts

def query_db(query, args=(), fetchall=True):
    if not os.path.exists(BASELINE_DB):
        return []
    try:
        conn = sqlite3.connect(BASELINE_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall() if fetchall else cur.fetchone()
        conn.close()
        return rv
    except Exception as e:
        print(f"DB Error: {e}")
        return []

# --- HTML TEMPLATE (Bootstrap 5) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OOB Network Dashboard</title>
    <!-- Bootstrap 5 CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <style>
        body { background-color: #121212; color: #e0e0e0; }
        .card { background-color: #1e1e1e; border: 1px solid #333; }
        .table-dark { background-color: #1e1e1e; }
        .stat-card { border-left: 4px solid #0d6efd; }
        .stat-card.success { border-left-color: #198754; }
        .stat-card.warning { border-left-color: #ffc107; }
        .stat-card.danger { border-left-color: #dc3545; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark border-bottom border-secondary mb-4">
        <div class="container-fluid">
            <a class="navbar-brand fw-bold" href="/"><i class="bi bi-hdd-network text-primary"></i> OOB Manager Web</a>
            <span class="navbar-text">
                <i class="bi bi-clock"></i> Cập nhật: {{ time_now }}
            </span>
        </div>
    </nav>

    <div class="container-fluid px-4">
        <!-- Metrics -->
        <div class="row mb-4">
            <div class="col-md-3">
                <div class="card stat-card shadow-sm h-100 py-2">
                    <div class="card-body">
                        <div class="row no-gutters align-items-center">
                            <div class="col mr-2">
                                <div class="text-xs font-weight-bold text-primary text-uppercase mb-1">Tổng thiết bị (File)</div>
                                <div class="h3 mb-0 fw-bold text-white">{{ stats.total }}</div>
                            </div>
                            <div class="col-auto"><i class="bi bi-router fa-2x text-secondary fs-1"></i></div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card stat-card success shadow-sm h-100 py-2">
                    <div class="card-body">
                        <div class="row no-gutters align-items-center">
                            <div class="col mr-2">
                                <div class="text-xs font-weight-bold text-success text-uppercase mb-1">Ping Sống (Online)</div>
                                <div class="h3 mb-0 fw-bold text-white">{{ stats.online }}</div>
                            </div>
                            <div class="col-auto"><i class="bi bi-activity fa-2x text-secondary fs-1"></i></div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card stat-card warning shadow-sm h-100 py-2">
                    <div class="card-body">
                        <div class="row no-gutters align-items-center">
                            <div class="col mr-2">
                                <div class="text-xs font-weight-bold text-warning text-uppercase mb-1">Đã có Baseline</div>
                                <div class="h3 mb-0 fw-bold text-white">{{ stats.has_baseline }}</div>
                            </div>
                            <div class="col-auto"><i class="bi bi-database-check fa-2x text-secondary fs-1"></i></div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card stat-card danger shadow-sm h-100 py-2">
                    <div class="card-body">
                        <div class="row no-gutters align-items-center">
                            <div class="col mr-2">
                                <div class="text-xs font-weight-bold text-danger text-uppercase mb-1">Lỗi Ping / Lỗi Menu</div>
                                <div class="h3 mb-0 fw-bold text-white">{{ stats.offline + stats.err_menu }}</div>
                            </div>
                            <div class="col-auto"><i class="bi bi-exclamation-triangle fa-2x text-secondary fs-1"></i></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Bảng danh sách thiết bị -->
        <div class="card shadow mb-4">
            <div class="card-header py-3 d-flex justify-content-between align-items-center bg-dark">
                <h6 class="m-0 fw-bold text-primary"><i class="bi bi-list-ul"></i> Danh sách OOB Devices</h6>
                <a href="/" class="btn btn-sm btn-outline-light"><i class="bi bi-arrow-clockwise"></i> Làm mới</a>
            </div>
            <div class="card-body bg-dark">
                <div class="table-responsive">
                    <table class="table table-dark table-hover table-striped align-middle">
                        <thead>
                            <tr>
                                <th>Alias</th>
                                <th>IP Address</th>
                                <th>Ping</th>
                                <th>Trạng thái Menu</th>
                                <th>Tổng Option (Baseline)</th>
                                <th>Cập nhật lần cuối</th>
                                <th>Hành động</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for dev in devices %}
                            <tr>
                                <td class="fw-bold">{{ dev.alias }}</td>
                                <td><code>{{ dev.ip }}</code></td>
                                <td>
                                    {% if dev.ping == True %}
                                        <span class="badge bg-success">Online</span>
                                    {% elif dev.ping == False %}
                                        <span class="badge bg-danger">Offline</span>
                                    {% else %}
                                        <span class="badge bg-secondary">Chưa quét</span>
                                    {% endif %}
                                </td>
                                <td>
                                    {% if dev.menu_state == 'ok' %}
                                        <span class="badge bg-success">OK</span>
                                    {% elif dev.menu_state == 'conn_failed' %}
                                        <span class="badge bg-danger">Lỗi kết nối</span>
                                    {% elif dev.menu_state == 'no_menu' %}
                                        <span class="badge bg-warning text-dark">Không có Menu</span>
                                    {% elif dev.menu_state == 'fetch_failed' %}
                                        <span class="badge bg-warning text-dark">Lỗi đọc data</span>
                                    {% else %}
                                        <span class="badge bg-secondary">-</span>
                                    {% endif %}
                                </td>
                                <td>
                                    {% if dev.opt_count > 0 %}
                                        <span class="text-info fw-bold">{{ dev.opt_count }}</span> options
                                    {% else %}
                                        <span class="text-muted">Không có</span>
                                    {% endif %}
                                </td>
                                <td class="text-muted small">{{ dev.checked_at }}</td>
                                <td>
                                    <a href="/device/{{ dev.ip }}" class="btn btn-sm btn-primary"><i class="bi bi-eye"></i> Xem Menu</a>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Bootstrap JS -->
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

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
            <a class="navbar-brand fw-bold" href="/"><i class="bi bi-arrow-left"></i> Trở về</a>
        </div>
    </nav>

    <div class="container">
        <div class="card shadow mb-4">
            <div class="card-header py-3 bg-dark">
                <h4 class="m-0 fw-bold text-primary">Cấu hình Menu: {{ ip }}</h4>
            </div>
            <div class="card-body bg-dark">
                {% if options %}
                <div class="row mb-3">
                    <div class="col-md-6"><p><strong>Tên thiết bị (Hostname):</strong> {{ options[0]['device_name'] }}</p></div>
                    <div class="col-md-6"><p><strong>Hãng sản xuất:</strong> <span class="badge bg-secondary text-uppercase">{{ options[0]['vendor'] }}</span></p></div>
                </div>
                <div class="table-responsive">
                    <table class="table table-dark table-striped table-hover">
                        <thead>
                            <tr>
                                <th>Phím Menu</th>
                                <th>Description</th>
                                <th>Target IP</th>
                                <th>Target Port</th>
                                <th>Protocol</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for opt in options %}
                            <tr>
                                <td><kbd>{{ opt['option_key'] }}</kbd></td>
                                <td>{{ opt['description'] }}</td>
                                <td><code>{{ opt['target_ip'] }}</code></td>
                                <td>{{ opt['target_port'] }}</td>
                                <td><span class="badge {% if opt['protocol'] == 'ssh' %}bg-success{% elif opt['protocol'] == 'serial' %}bg-info text-dark{% else %}bg-warning text-dark{% endif %}">{{ opt['protocol'] | upper }}</span></td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="alert alert-warning">
                    <i class="bi bi-exclamation-circle"></i> Chưa có dữ liệu Baseline cho thiết bị này, hoặc thiết bị chưa được quét.
                </div>
                {% endif %}
            </div>
        </div>
    </div>
</body>
</html>
"""

# --- ROUTES ---

@app.route("/")
def index():
    ips = get_ips()
    dev_status = load_device_status()
    
    devices = []
    stats = {"total": len(ips), "online": 0, "offline": 0, "has_baseline": 0, "err_menu": 0}

    for item in ips:
        ip = item["ip"]
        status = dev_status.get(ip, {})
        
        # Thống kê Ping
        if status.get("ping") is True: stats["online"] += 1
        elif status.get("ping") is False: stats["offline"] += 1
            
        # Thống kê Menu
        if status.get("menu_state") in ["conn_failed", "fetch_failed"]:
            stats["err_menu"] += 1

        # Đếm số lượng option từ SQLite
        opts = query_db("SELECT count(*) as cnt FROM baseline_menu WHERE host=?", (ip,))
        opt_count = opts[0]["cnt"] if opts else 0
        if opt_count > 0:
            stats["has_baseline"] += 1

        devices.append({
            "alias": item["alias"],
            "ip": ip,
            "ping": status.get("ping"),
            "menu_state": status.get("menu_state"),
            "checked_at": status.get("checked_at", "-"),
            "opt_count": opt_count
        })

    time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template_string(HTML_TEMPLATE, devices=devices, stats=stats, time_now=time_now)

@app.route("/device/<ip>")
def device_detail(ip):
    options = query_db("SELECT * FROM baseline_menu WHERE host=? ORDER BY option_key", (ip,))
    # Format DB rows to dict
    opts_list = [dict(row) for row in options]
    return render_template_string(DETAIL_TEMPLATE, ip=ip, options=opts_list)

if __name__ == "__main__":
    print("=========================================================")
    print("🚀 GIAO DIỆN WEB OOB ĐÃ KHỞI ĐỘNG")
    print("👉 Mở trình duyệt và truy cập: http://127.0.0.1:5000")
    print("=========================================================")
    # Chạy trên tất cả IP mạng LAN (0.0.0.0) với port 5000
    app.run(host="0.0.0.0", port=5000, debug=False)
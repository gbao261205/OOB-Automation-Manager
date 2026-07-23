"""
oob_verify.py

Module kiem tra tinh toan ven cua menu OOB:
  - Moi option trong menu co dang ket noi dung thiet bi ma no mo ta khong?
  - Dung hostname (lay truc tiep tu thiet bi dich) so sanh voi description
    cua option, theo quy tac word-boundary.

TRANG THAI: TAM THOI KHONG HOAT DONG
  Chuc nang nay da duoc tach ra day de phat trien doc lap.
  Chua duoc goi tu monitor_loop. Se kich hoat lai sau khi
  implement ket noi qua phien OOB (nested telnet / invoke menu).

Ke hoach phat trien tiep theo:
  - Them invoke_menu_and_check(): mo menu IOS tren OOB va chon
    tung option de kiem tra thiet bi dich (thay the check_option_hostnames).
  - Auto-detect ten menu tu thiet bi (fetch_menu_name).
  - Luu ten menu per-device vao DB (device_info table).
"""

from oob_lib import fetch_hostname_via_auto, _hostname_matches_desc


# ---------------------------------------------------------------------------
# Kiem tra hostname tung option (mo ket noi RIENG den tung thiet bi dich)
# ---------------------------------------------------------------------------

def check_option_hostnames(alias: str, snapshot: dict, cfg: dict) -> bool:
    """Ket noi SSH (hoac Telnet du phong) vao tung IP dich trong option,
    kiem tra hostname khop description theo word-boundary.

    Tham so:
        alias    : Ten hien thi cua OOB device (vi du "R0")
        snapshot : Dict options tu parse_menu (key -> {ip, port, description})
        cfg      : Cau hinh hien tai (username, password, ssh_port, ...)

    Tra ve True neu tat ca option hop le, False neu co bat ky van de nao.

    CANH BAO: Ham nay mo ket noi RIENG den tung IP dich.
    Phien ban moi se dung lai phien SSH dang co toi OOB va di qua menu IOS.
    """
    ssh_port = cfg.get("ssh_port", 22)
    all_ok   = True

    print(f"    [*] Kiem tra hostname tung option cua {alias}:")
    for key in sorted(snapshot):
        entry  = snapshot[key]
        desc   = entry.get("description", "")
        t_ip   = entry["ip"]
        t_port = entry.get("port", 23)

        print(f"        [{key}] {desc:<20} -> {t_ip}  ", end="", flush=True)
        try:
            actual = fetch_hostname_via_auto(
                t_ip, ssh_port, t_port,
                cfg["username"], cfg["password"], cfg["enable_password"],
            )
            if actual is None:
                print("[CANH BAO] Khong lay duoc hostname tu thiet bi")
                all_ok = False
            elif _hostname_matches_desc(actual, desc):
                print(f"[OK] hostname={actual}")
            else:
                print(
                    f"[CANH BAO] hostname thuc te='{actual}' "
                    f"KHONG KHOP description='{desc}'"
                )
                all_ok = False
        except Exception as exc:
            print(f"[LOI KET NOI] {exc}")
            all_ok = False

    if not all_ok:
        print(f"    [!] Co option cua {alias} KHONG khop hostname — kiem tra lai!")
    return all_ok


# ---------------------------------------------------------------------------
# (Chua implement) Kiem tra qua menu IOS — se thay the ham o tren
# ---------------------------------------------------------------------------

def invoke_menu_and_check(tn, menu_name: str, snapshot: dict,
                          username: str, password: str,
                          enable_password: str) -> dict:
    """[CHUA IMPLEMENT] Mo menu IOS tren OOB device (phien SSH dang mo)
    va chon tung option de kiem tra hostname thiet bi dich.

    Uu diem so voi check_option_hostnames():
      - Tai su dung phien SSH hien tai, khong can mo them ket noi moi
      - Dung dung co che cua admin thuc te (chon option trong menu)
      - Phat hien duoc ca truong hop loi lenh (khong phai telnet)

    Tham so:
        tn           : Session SSH/Telnet dang mo toi OOB device
        menu_name    : Ten menu can mo (da auto-detect hoac lay tu DB)
        snapshot     : Dict options tu parse_menu
        username/password/enable_password: Thong tin dang nhap thiet bi dich

    Tra ve:
        dict {option_key: actual_hostname_or_None}
    """
    raise NotImplementedError(
        "invoke_menu_and_check() chua duoc implement. "
        "Xem ke hoach trong implementation_plan.md."
    )

# OOB-Automation-Manager — Full Source Code Architecture Audit

**Scope confirmed:** This is a genuinely small, flat repository — 3 Python files, no packages, no tests, no CI/CD, no Docker, no migrations directory. Nothing was excluded from inspection; every file in `git ls-files` was read in full (`oob_lib.py` 783 lines, `oob_monitor.py` 2070 lines, `oob_web.py` 1814 lines, `README.md`, `api_documentation.md`, `requirements.txt`). The `.docx` report was not opened (binary, not source) — noted as **not inspected** (out of scope for code audit).

---

## 1. Executive Summary

This is a single-operator network-automation tool that monitors Cisco IOS "menu"-based OOB console servers and Vertiv ACS8000 serial console servers over SSH (fallback Telnet), verifies that the device physically wired into each console port is the one the menu description claims it is ("Deep Verify" / pivot verification), and can optionally auto-correct wrong descriptions. It ships a Rich-based CLI/daemon (`oob_monitor.py`) and a Flask web dashboard (`oob_web.py`) that directly imports and reuses the CLI module's functions rather than going through a service layer.

The single most important finding, confirmed by exact code inspection, is that **the "auto-heal / push-fix" and "Revert" features are permanently mock/dry-run** — every call site in the codebase hardcodes `dry_run=True`, so no code path ever sends a real configuration change to a device, yet the system logs these as successes and silently rewrites its own baseline to match, masking the fact that nothing was actually pushed. Beyond that, the architecture is a workable, honestly-documented (the README is unusually candid about its own gaps) but monolithic script-based system with no test coverage, no separation of layers, credentials stored in plaintext by default, and several other confirmed correctness/security issues detailed below.

## 2. Current Project Structure

```
OOB-Automation-Manager/
├── oob_lib.py              # Low-level SSH/Telnet transport + Cisco menu parsing + push primitives
├── oob_monitor.py          # CLI, daemon, business logic, SQLite persistence, Excel I/O, Deep Verify engine
├── oob_web.py              # Flask app: imports oob_monitor as a library, adds web auth + HTML/JS UI + task runner
├── requirements.txt        # paramiko, rich, flask, werkzeug, openpyxl (cryptography NOT listed)
├── README.md               # Very thorough, unusually honest operator documentation (Vietnamese)
├── api_documentation.md    # Endpoint reference for oob_web.py
└── BAO-CAO-HUONG-DAN-SU-DUNG.docx   # Not inspected (binary)
```

Runtime-generated (not in git, documented in README §3): `oob_config.json`, `oob_ips.txt`, `baseline.db`, `snapshot.db`, `device_status.json`, `task_history.json`, `working_creds.json`, `.oob_secret.key`, `verify-logs/`, `push-logs/`, `debug-logs/`, `baseline-logs/`, `alarms/`, `reports/`, `daemon.pid`.

There is no `src/`, no package boundaries, no `tests/`. All three files sit flat at repo root and are meant to be run directly (`python oob_monitor.py`, `python oob_web.py`).

## 3. Architecture Overview

Three-file layered-in-name-only design:

- **`oob_lib.py`** — transport layer: `MiniTelnet` / `MiniSSH` (identical interface, paramiko-backed SSH with Telnet fallback), regex-based Cisco `menu` config parsing, and push primitives (`push_menu_descriptions`, `push_vertiv_port_names`).
- **`oob_monitor.py`** — everything else: config/credential management, SQLite access (hand-rolled, ad-hoc schema migration via `ALTER TABLE IF NOT EXISTS`), the Deep Verify state machine, the two daemon threads, the Rich CLI, and Excel import/export. This one file mixes presentation (Rich `Table`/`Panel` printing), business logic (diffing, verify semantics), and persistence (raw SQL) with no internal module boundaries — it is the closest thing to a "god module" in the codebase.
- **`oob_web.py`** — Flask routes calling straight into `oob_monitor` functions (`import oob_monitor; oob_monitor.poll_host_multi(...)`), plus its own auth (separate `web_users` table bolted onto `baseline.db`), its own background-task manager, and a ~1000-line inline HTML/CSS/JS string (`HTML = r"""..."""`) serving the entire single-page dashboard. There is no template file, no static asset pipeline, no API schema/serializer layer — routes hand-build `dict`s and call `jsonify()` directly.

There is effectively **one shared layer** across CLI and Web: `oob_monitor.py`'s module-level functions and global state (locks, `_ip_list_cache`, `_DB_INIT_CACHE`). The web process and the daemon process do not share memory (separate OS processes) — they only share files on disk (`oob_config.json`, the two SQLite DBs, `oob_ips.txt`, `device_status.json`). This is explicitly and correctly documented in README §8/§12.

## 4. Main Components

| File | Responsibility | Depends On | Used By | Assessment |
|---|---|---|---|---|
| `oob_lib.py::MiniSSH`/`MiniTelnet` | Raw protocol clients with a shared `read_until`/`write`/`close` interface | paramiko (optional), socket | `oob_lib.connect_auto`, `oob_monitor` (imports `MiniTelnet`, `connect_auto`, `fetch_hostname`) | Well-commented, thoughtful prompt-detection regex (`PROMPT_TAIL_RE`) that correctly avoids false-positive `#`/`>` matches inside description text — a genuinely good piece of engineering. |
| `oob_lib.py::parse_menu` | Regex-parses Cisco `menu <name> text/command ...` lines into option dicts | none | `oob_lib.poll_host` (legacy path), not used by `oob_monitor`'s main scan path (which has its own duplicate parsing, see Technical Debt) | Duplicated logic — see Technical Debt. |
| `oob_lib.py::push_menu_descriptions` / `push_vertiv_port_names` | Sends (or, currently, only *simulates*) config-fixing commands | `connect_auto` | `oob_monitor.process_push_and_reverify`, `oob_web.api_revert` | **Always invoked with `dry_run=True`** — see Critical Finding. |
| `oob_monitor.py::load_config`/`save_config` | Flat JSON config persistence, no schema validation | `json` | everywhere | Silently swallows JSON decode errors (`except ... pass`), falling back to defaults with no warning to the operator. |
| `oob_monitor.py::_encrypt_cred`/`_decrypt_cred` | Optional Fernet (or base64 fallback) encryption for the *working-credential cache only* | `cryptography` (not in requirements.txt) | `save_working_credential`, `get_all_credentials` | Inconsistent: protects `working_creds.json` but **not** the primary `oob_config.json`, which stores all passwords in plaintext. |
| `oob_monitor.py::poll_host_multi` | Vendor-aware scan: Cisco menu vs Vertiv ACS `access/` listing | `oob_lib.connect_auto` | daemon scan loop, web `_run_scan`, CLI scan | Duplicates the Cisco text/command regex parsing already in `oob_lib.parse_menu` instead of reusing it. |
| `oob_monitor.py::run_deep_verify` | ~400-line function: pivots into every option's target device, extracts real hostname from banner/prompt text, classifies OK/CANH BAO/TIMEOUT/KHONG PIVOT/YEU CAU DANG NHAP | `connect_auto`, extensive vendor-specific state machine for Vertiv password/menu prompts | daemon verify loop, web `_run_verify`/`_run_push`/live-debug, CLI verify | The most complex and most carefully-tuned function in the codebase (extensive inline commentary explaining real production edge cases like FreeBSD banner races, double-Enter drain semantics). Also the single largest maintainability risk due to size and branching depth. |
| `oob_monitor.py::process_push_and_reverify` | Confirms no IP is claimed by >1 OOB, then "pushes" fixed description and re-verifies | `push_menu_descriptions` (dry_run hardcoded True) | daemon (`auto_push_desc`), CLI `[p]`, web push button | **Never actually changes device state**. |
| `oob_web.py::_run_scan/_run_verify/_run_push` | Background `ThreadPoolExecutor(max_workers=5)` wrappers around `oob_monitor` functions, streamed via SSE | `oob_monitor.*` | `/api/action` | Reasonable on-demand execution model; hardcoded worker count. |
| `oob_web.py` inline `HTML` string | Entire SPA (dashboard, device view, verify/scan, logs, import/export, settings) | none (vanilla JS, `fetch`, `EventSource`) | `/` route | No XSS review found needed beyond what's checked below (client escapes with `esc()` consistently before interpolating into `innerHTML`) — a genuinely careful detail for hand-rolled JS. |

## 5. Data Flow

**Scan / baseline drift detection** (identical logic path in CLI daemon, CLI manual scan, and Web `/api/action?scan`):
```
oob_ips.txt → poll_host_multi() [SSH/Telnet → vendor-specific parse] → snapshot dict
    → save to snapshot_menu (SQLite)
    → compare against baseline_menu (SQLite) via options_equal()
    → IF DIFFERENT: baseline_menu is OVERWRITTEN with the new snapshot automatically,
      with only a one-line entry in baseline-logs/baseline_updates.log (alias/ip/action —
      no diff content persisted).
```

**Deep Verify / self-healing**:
```
baseline_menu (per-option target IP/port/protocol/description)
    → connect to OOB console, "connect <port>"/"telnet"/"ssh" pivot per option
    → extract_hostname() parses banner/prompt text
    → compare extracted hostname vs stored description (word-boundary match)
    → OK | CANH BAO (mismatch, logged + alarm) | TIMEOUT | KHONG PIVOT | YEU CAU DANG NHAP
    → verify-logs/Verify_<alias>_<ts>.log (+ .json) written
    → IF CANH BAO and auto_push_desc/manual confirm: process_push_and_reverify()
        → push_menu_descriptions(..., dry_run=True)  <- ALWAYS simulated, never touches device
        → baseline_menu's local description field is rewritten to the "fixed" value anyway
        → push-logs/Push_<alias>_<ts>.log written (looks identical whether or not anything really happened)
        → re-verify runs against the now-self-updated local baseline
```

**Web read path**: `/api/devices`, `/api/stats`, `/api/search` all reconstruct state on every request by re-reading `oob_ips.txt`, `device_status.json`, the SQLite baseline, and re-parsing every `verify-logs/*.json` file (`_parse_verify_logs_for_status` — see Performance).

There is no "Audit operation → Audit Service → Repository" layer as such — `save_options()` doubles as the only persistence hook, and there is no structured audit table recording who ran what action and when; the closest thing is the flat-file logs plus `task_history.json` (task id/action/ip/status/timestamps only, no user attribution beyond the fact that all web actions require `login_required`).

## 6. Dependency Analysis

- **`oob_web.py` → `oob_monitor.py`**: whole-module import, not an interface. `oob_web.py` reaches into `oob_monitor`'s private-by-convention helpers (`oob_monitor._parse_verify_logs_for_status`) and module-level regexes/constants directly. This is a hard coupling.
- **No circular imports** were found.
- **Global mutable state**: `oob_monitor.py` has ~10 module-level globals used across both the CLI and (via import) the web process's threads: `db_lock`, `action_lock`, `file_lock`, `ui_print_lock`, `_device_status_lock`, `_ip_list_cache`, `_DB_INIT_CACHE`, `oob_logs`/`verify_logs` deques, `_live_ui`.
- **Duplicate implementation**: Cisco menu-line regexes (`TEXT_RE`, `CMD_TELNET_RE`, `CMD_SSH_RE`, `MENU_NAME_RE`) and a full parsing loop exist **twice** — once in `oob_lib.py` (`parse_menu`, used only by the legacy `poll_host`) and once inline inside `oob_monitor.py::poll_host_multi` (the actual code path used everywhere). They have already drifted slightly.
- **Direct infrastructure coupling**: business logic (`run_deep_verify`, `poll_host_multi`) directly constructs SQL and Rich-markup print statements in the same function body.

## 7. Network Automation Architecture

- **Device abstraction**: `MiniSSH`/`MiniTelnet` share an interface, and `connect_auto()` gives a single "connect, SSH-first-Telnet-fallback" entry point — genuinely a good abstraction, reused consistently everywhere connections are made.
- **Multi-vendor support**: Cisco IOS and Vertiv ACS8000 are both handled, but through **if/else vendor branches scattered through `poll_host_multi`, `run_deep_verify`, and `process_push_and_reverify`**, not through a vendor-driver interface.
- **Connection lifecycle**: timeouts are passed as plain function args everywhere with no central retry/backoff policy.
- **Configuration push**: exists in code but is **permanently disabled via a hardcoded `dry_run=True`** at every call site. What ships today is Scan (read) and Verify (read) only, functionally — Push and Revert are simulations.
- **Baseline/drift comparison**: `options_equal`/`diff_options` do simple dict comparison; drift auto-heals the baseline to the latest scan with **no confirmation gate at all** in daemon/web/CLI-batch mode, only a printed/logged warning.
- **Parsing**: regex-based, not TextFSM — appropriate choice given the ad-hoc banner/prompt/menu-config text being parsed (not tabular `show`-command output).
- **Inventory**: no NetBox or other IPAM integration; flat `oob_ips.txt` (`ip alias` per line).
- **Audit**: logs exist (`verify-logs/`, `push-logs/`, `baseline-logs/`) but are unstructured, and the baseline-drift log records only that a change happened, not what changed.

## 8. Feature Inventory

| Feature | Status | Evidence | Notes |
|---|---|---|---|
| Cisco menu scan / baseline diff | **IMPLEMENTED** | `oob_monitor.poll_host_multi`, `options_equal`, `diff_options` | Works as documented; baseline auto-overwrites on drift. |
| Vertiv ACS scan (port listing) | **IMPLEMENTED** | `poll_host_multi` "cli->" branch | |
| Deep Verify (physical pivot check) | **IMPLEMENTED** | `run_deep_verify` | Extensively hand-tuned for real device quirks. |
| Auto-push / self-heal wrong description | **BROKEN / effectively PLACEHOLDER** | `dry_run=True` hardcoded at every call site (`oob_monitor.py:1259,1262`, `oob_web.py:674`) | Documented in README as a working feature; it is not, in the shipped code. |
| Revert from push-log | **BROKEN / effectively PLACEHOLDER** | same `dry_run=True` at `oob_web.py:674` | Same issue — revert never touches the device. |
| CLI daemon (2 independent scheduled threads) | **PARTIALLY IMPLEMENTED** | Both threads exist and run on independent schedules, but share `action_lock` so their *execution* isn't actually concurrent | Contradicts README §5's "hoàn toàn độc lập, chạy song song" claim. |
| Web dashboard (on-demand scan/verify/push) | **IMPLEMENTED** | `oob_web.py` routes + task runner | Push button is a no-op in effect, per above. |
| Web auth (Admin/Guest), password-leak check (HIBP) | **IMPLEMENTED** | `login_required`, `check_pwned_password` | Functions as documented. |
| Multi-account credential fallback | **IMPLEMENTED** | `get_all_credentials` | |
| Excel import/export (CLI + Web) | **IMPLEMENTED** | `import_from_excel`, `export_menu_report`, `/api/import`, `/api/export/excel` | |
| Credential encryption at rest | **PARTIALLY IMPLEMENTED** | `_encrypt_cred` protects only `working_creds.json`; `oob_config.json` is plaintext; `cryptography` isn't in `requirements.txt` | Base64 "obfuscation" fallback is the real-world default. |
| Daemon health status in Web UI | **MISSING** (self-documented) | README §8.2 | |
| Device alias edit (Web) | **MISSING** | README §8.4; `/api/device` only supports POST/DELETE | |

## 9. Security Findings

**CRITICAL**
1. **Push/Revert are simulated everywhere, but reported and persisted as successful.** `process_push_and_reverify()` (`oob_monitor.py:1216-1283`) and `api_revert()` (`oob_web.py:641-682`) call `push_menu_descriptions`/`push_vertiv_port_names` with `dry_run=True` unconditionally. On a "successful" (simulated) push, the code updates `baseline_menu` in-place to the corrected description and writes a push-log that reads identically to a real push. Confidence: **CONFIRMED**.

**HIGH**
2. **Plaintext credential storage in the primary config file.** `oob_config.json` stores `password`, `enable_password`, `vertiv_connect_password`, `vertiv_admin_password`, and all `credentials[]` entries in cleartext on disk. Confidence: **CONFIRMED**.
3. **Weak fallback "encryption" is the real-world default.** `requirements.txt` does not list `cryptography`; `_get_cipher()` falls back to base64 (`"B64:"` prefix), trivially reversible. Confidence: **CONFIRMED**.
4. **Admin API leaks device secrets verbatim.** `GET /api/config` (`oob_web.py:582-586`) filters out only `credentials`, returning `password`, `enable_password`, `vertiv_connect_password`, `vertiv_admin_password` unmasked to any authenticated Admin session. Confidence: **CONFIRMED**.
5. **SSH host keys are auto-trusted.** `MiniSSH.__init__` sets `paramiko.AutoAddPolicy()` (`oob_lib.py:214`), silently accepting/persisting any host key — MITM exposure. Confidence: **CONFIRMED**.

**MEDIUM**
6. **No CSRF protection and no rate limiting** on `/login` or state-changing endpoints. Confidence: **LIKELY**.
7. **Flask `secret_key` regenerates on every restart by default** (`os.urandom(24)` fallback, `oob_web.py:15`) — self-documented limitation. Confidence: **CONFIRMED**.
8. **Web `/api/device` POST does not validate IP format** before writing to `oob_ips.txt` (`oob_web.py:615-624`), unlike Excel-import/CLI-add paths. Confidence: **CONFIRMED**.

**LOW / INFORMATIONAL**
9. Log/file endpoints correctly use `os.path.basename()` — **path traversal properly mitigated**. Confidence: **CONFIRMED (no vulnerability)**.
10. `subprocess.run(["ping", ...])` uses argument lists, never `shell=True` — **not vulnerable to shell injection**. Confidence: **CONFIRMED (no vulnerability)**.
11. SQL queries parameterize all values correctly; only table names are f-string-interpolated, always internal literals — currently safe. Confidence: **CONFIRMED (currently safe)**.

## 10. Reliability & Error Handling

- Broad `try/except Exception` around connection attempts, with credential fallback — reasonably robust, but error surfacing is inconsistent.
- **`max_verify_duration`/`_verify_deadline` is dead code**: computed once (`oob_monitor.py:795`) and never checked again inside `run_deep_verify`'s loop. A hang in one option's pivot logic is bounded only by the sum of individual step timeouts, not by the configured overall deadline.
- No rollback path exists (moot today since push is simulated).
- `load_config` swallows `JSONDecodeError`/`OSError` silently, falling back to defaults with no operator warning.
- Baseline auto-overwrite on drift with no durable diff record — an audit gap as much as a reliability one.

## 11. Testing Assessment

**No test files exist anywhere in the repository.** No `pytest`/`unittest` dependency, no `tests/` directory, no CI configuration, no linting configuration. 0% coverage on parsing logic, diffing logic, scheduling math, or the Deep Verify state machine. This is the single largest quality-process gap in the project given the codebase's size and the operational risk of the domain.

## 12. Performance Assessment

- **CONFIRMED bottleneck**: `_parse_verify_logs_for_status()` (`oob_monitor.py:1632-1676`) re-reads and re-parses **every** `verify-logs/*.json` file on every call, with no caching, and is called on nearly every read-only web API.
- **CONFIRMED bottleneck**: `db_lock` is one single global lock guarding all SQLite reads/writes across *all* hosts and *both* tables — every DB operation is fully serialized.
- **CONFIRMED**: SSH/Telnet connections are opened fresh per operation with no connection pooling/reuse across cycles.
- **POTENTIAL bottleneck**: fixed `ThreadPoolExecutor(max_workers=10/5)` — adequacy depends entirely on fleet size.

## 13. Scalability Assessment

- **10–100 devices**: current design works adequately — almost certainly the intended scale.
- **1,000 devices**: fixed worker pools + `action_lock` serializing scan/verify threads would likely cause cycles to overlap or fall behind schedule; no guard against overlapping runs; log re-parsing would slow dashboard loads noticeably.
- **10,000 devices**: single-SQLite-file + single-global-lock + fixed-thread-pool design would not scale without structural change.

This assessment does **not** recommend microservices or a rewrite — the current design is appropriately simple for the apparent actual scale.

## 14. Technical Debt

**MUST FIX**
- Push/Revert hardcoded `dry_run=True`.
- `max_verify_duration`/`_verify_deadline` dead code.
- Plaintext credentials in `oob_config.json`.

**SHOULD FIX**
- Duplicate Cisco menu-parsing regex/logic in `oob_lib.parse_menu` vs. `oob_monitor.poll_host_multi`.
- `action_lock` serializing the two "independent" daemon threads.
- Baseline auto-overwrite with no diff persisted.
- `load_config` silently swallowing corrupt-JSON errors.

**NICE TO HAVE**
- `oob_web.py`'s inline HTML/JS string could move to a real template file.
- `connect_and_login()` marked `Deprecated` in its own docstring but still present, apparently unused.

## 15. What Is Already Good (Preserve, Don't Rewrite)

- `connect_auto()`'s SSH-first/Telnet-fallback design and the shared `MiniSSH`/`MiniTelnet` interface.
- `PROMPT_TAIL_RE` and the "don't stop on the first bare `#`" reasoning — hard-won, comment-explained production fix.
- `run_deep_verify`'s Vertiv password-retry, multi-session-menu, and banner-race handling — encodes real field-debugging knowledge; should be extracted into tested units before being touched, not rewritten.
- README.md's candor about the system's own limitations.
- Path-traversal mitigation on log-file-serving endpoints and argument-list (non-shell) subprocess calls.

## 16. Problems & Risks (Consolidated)

1. Push/Revert are silent no-ops that report success (CRITICAL, CONFIRMED).
2. Plaintext/weakly-obfuscated credential storage as the default deployment path (HIGH, CONFIRMED).
3. Baseline silently self-heals to drift with no durable diff record (MEDIUM-HIGH, CONFIRMED).
4. Zero automated test coverage (MEDIUM-HIGH, CONFIRMED as process gap).
5. The two daemon threads are not actually concurrent, contradicting documentation (MEDIUM, CONFIRMED).
6. `max_verify_duration` is dead code (MEDIUM, CONFIRMED).
7. Auto-trusted SSH host keys (MEDIUM, CONFIRMED, likely accepted risk for internal networks).
8. No CSRF/rate-limiting on the web admin surface (MEDIUM, LIKELY).

## 17. Improvement Recommendations

See `docs/upgrade-proposals.md` and `docs/implementation-plan.md` for the full, actionable breakdown (Immediate / Short Term / Medium Term / Long Term), derived directly from this audit.

## 18. Proposed Target Architecture

**CURRENT**: 3 flat scripts; CLI and Web both import the same monolithic module and share only on-disk state; no interface boundaries between transport/parsing/persistence/presentation.

**PROPOSED (evolutionary, not a rewrite)**:
```
oob_lib.py            (unchanged: transport layer — already well-isolated)
oob_core/
  parsing.py           # single source of truth for menu parsing
  diff.py              # options_equal / diff_options, with a persisted-diff variant
  verify.py            # run_deep_verify, extracted with the vendor branches behind
                        #   a small VendorHandler interface (CiscoHandler / VertivHandler)
  push.py              # push orchestration, with an explicit, single, well-tested
                        #   dry_run/live switch instead of one hardcoded at every call site
  config.py            # load/save + encryption applied uniformly to the one config store
  db.py                # SQLite access, still hand-rolled but centralized
oob_monitor.py          # thin CLI/daemon entrypoint over oob_core
oob_web.py              # thin Flask entrypoint over oob_core (keeps the inline HTML)
tests/                  # unit tests for oob_core, using captured device-output fixtures
```

## 19. Migration Roadmap

See `docs/implementation-plan.md` §22 (Final Implementation Roadmap) for the fully detailed, phased roadmap (Phase 0 → Phase 7) that operationalizes this target architecture incrementally.

## 20. Recommended Agent Skills

- **`python-pro`** — applicable for extracting testable pure functions, adding type hints/dataclasses. **Not actually available** in this repo (`.agent/skills/` does not exist) nor in this session's skill list — noted as a recommendation for future tooling, not a skill actually invoked here.
- **`network-config-diff`** / **`baseline-comparison`** — applicable to persisting real diffs on baseline updates. **Not actually available** in this session.
- **`production-audit`** — applicable if/when this tool is deployed against a live production OOB fleet for a runtime-state check. **Not actually available** in this session.
- **`textfsm-parser`** — evaluated and **not recommended**: the device output parsed here (menu config lines, login banners) is not tabular `show`-command output; hand-written regex is appropriate.
- **`git-commit`** — not applicable to this audit task itself.
- **`poyntail`** — no skill definition/content was available to evaluate; noted as **not evaluated** rather than guessed.
- Skills actually available and used in this engagement: `security-review`, `code-review`, `simplify` (see `docs/implementation-plan.md` for where each applies).

## 21. Final Assessment

This is a well-intentioned, unusually honestly-documented, single-operator network tool whose hardest engineering problem — reliably pivoting into a live console session and correctly extracting the real connected device's identity across two very different vendor CLIs — has clearly received serious, field-tested attention. The architecture is a flat, monolithic script structure appropriate to its actual scale, and should be evolved incrementally, not rewritten.

The one finding that changes the overall risk picture is **not** an architecture or scalability concern — it's that **the feature the tool is named for using to "self-heal" is, in the code as it exists today, always a simulation**, regardless of configuration, regardless of CLI vs. Web, regardless of vendor. Fixing or accurately re-documenting that single hardcoded `dry_run=True` is the top-priority action arising from this audit (tracked as WP-B in `docs/implementation-plan.md`).

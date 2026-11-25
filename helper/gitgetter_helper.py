#!/usr/bin/env python3
"""Git Getter Helper (Mac) by Urushihara.

Watches the Downloads folder and iCloud Drive/Git Getter. When a new file
arrives it is checked with ClamAV and (optionally) VirusTotal. Dangerous files
are moved to a quarantine folder. Safe zip/dmg/pkg files can be unpacked and
installed after a confirmation dialog.

Standard library only. Runs as a LaunchAgent installed by install.sh.
"""
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

VERSION = "1.0.0"
HOME = os.path.expanduser("~")
APP_DIR = os.path.join(HOME, "GitGetterHelper")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
STATE_PATH = os.path.join(APP_DIR, "state.json")
RESULTS_PATH = os.path.join(APP_DIR, "results.json")
LOG_PATH = os.path.join(APP_DIR, "log.txt")
ICLOUD = os.path.join(HOME, "Library", "Mobile Documents", "com~apple~CloudDocs")
QUARANTINE = os.path.join(HOME, "Git Getter 隔離")

DEFAULT_CONFIG = {
    "vt_key": "",
    "watch": [os.path.join(HOME, "Downloads"), os.path.join(ICLOUD, "Git Getter")],
    "auto_unzip": True,
    "ask_install": True,
    "poll_seconds": 10,
}
PARTIAL_EXT = (".download", ".crdownload", ".part", ".partial", ".tmp", ".icloud")
TOOL_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]


# ---------- small utilities ----------
def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(LOG_PATH) > 2_000_000:  # keep the log small
            with open(LOG_PATH, encoding="utf-8") as f:
                tail = f.readlines()[-2000:]
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(tail)
    except OSError:
        pass


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def tool(name):
    found = shutil.which(name)
    if found:
        return found
    for d in TOOL_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


def run(args, timeout=600):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


def as_str(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def notify(title, msg):
    run(["osascript", "-e", f'display notification "{as_str(msg)}" with title "Git Getter Helper" subtitle "{as_str(title)}"'], 15)


def dialog(msg, buttons, default=None, icon="note"):
    """Show a dialog and return the pressed button text (or None on timeout/cancel)."""
    btns = "{" + ", ".join(f'"{as_str(b)}"' for b in buttons) + "}"
    d = f' default button "{as_str(default)}"' if default else ""
    script = (f'display dialog "{as_str(msg)}" with title "Git Getter Helper" buttons {btns}{d} '
              f'with icon {icon} giving up after 600')
    code, out, _ = run(["osascript", "-e", script], 700)
    if code != 0 or "gave up:true" in out:
        return None
    for b in buttons:
        if f"button returned:{b}" in out:
            return b
    return None


# ---------- checks ----------
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def vt_lookup(sha, key):
    if not key:
        return {"state": "nokey"}
    req = urllib.request.Request(f"https://www.virustotal.com/api/v3/files/{sha}", headers={"x-apikey": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            stats = json.load(r)["data"]["attributes"]["last_analysis_stats"]
        total = sum(stats.get(k, 0) for k in ("malicious", "suspicious", "undetected", "harmless"))
        return {"state": "ok", "mal": stats.get("malicious", 0), "sus": stats.get("suspicious", 0), "total": total}
    except urllib.error.HTTPError as e:
        return {"state": {404: "unknown", 429: "limit", 401: "badkey", 403: "badkey"}.get(e.code, "error")}
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return {"state": "error"}


def clam_scan(path):
    exe = tool("clamscan")
    if not exe:
        return {"state": "missing"}
    code, out, err = run([exe, "--no-summary", "--infected", "--scan-archive=yes", path], 1800)
    if code == 0:
        return {"state": "clean"}
    if code == 1:
        names = [l.split(":", 1)[1].replace("FOUND", "").strip() for l in out.splitlines() if l.endswith("FOUND")]
        return {"state": "infected", "names": names[:5]}
    return {"state": "error", "detail": (err or out).strip()[:200]}


def update_signatures(state):
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("freshclam_day") == today:
        return
    exe = tool("freshclam")
    if not exe:
        return
    code, out, err = run([exe, "--quiet"], 900)
    log(f"freshclam finished (code {code}) {err.strip()[:120]}")
    state["freshclam_day"] = today


def verdict(clam, vt):
    """Return (level, reasons). level: red / yellow / green."""
    reasons = []
    level = "green"
    if clam["state"] == "infected":
        level = "red"
        reasons.append("ClamAVがウイルスを検出：" + "、".join(clam.get("names", [])))
    elif clam["state"] == "clean":
        reasons.append("ClamAV：検出なし")
    else:
        level = "yellow"
        reasons.append("ClamAVで検査できませんでした" if clam["state"] == "error" else "ClamAVが入っていません")
    if vt["state"] == "ok":
        if vt["mal"] >= 3:
            level = "red"
            reasons.append(f"VirusTotal：{vt['mal']}/{vt['total']}のソフトが検出")
        elif vt["mal"] >= 1 or vt["sus"] >= 2:
            level = "yellow" if level != "red" else level
            reasons.append(f"VirusTotal：{vt['mal'] + vt['sus']}/{vt['total']}のソフトが反応（誤検知のこともあります）")
        else:
            reasons.append(f"VirusTotal：検出なし（0/{vt['total']}）")
    elif vt["state"] == "unknown":
        reasons.append("VirusTotal：未登録の新しいファイル")
    elif vt["state"] != "nokey":
        reasons.append("VirusTotal：確認できませんでした")
    return level, reasons


# ---------- after the check ----------
def quarantine(path):
    os.makedirs(QUARANTINE, exist_ok=True)
    dest = os.path.join(QUARANTINE, os.path.basename(path))
    base, ext = os.path.splitext(dest)
    n = 1
    while os.path.exists(dest):
        dest = f"{base} ({n}){ext}"
        n += 1
    shutil.move(path, dest)
    try:
        os.chmod(dest, 0o400)  # remove execute permission
    except OSError:
        pass
    return dest


def notarized(app_path):
    code, out, err = run(["spctl", "--assess", "--type", "execute", "-v", app_path], 120)
    text = (out + err).lower()
    if code == 0:
        return True, "Appleの審査（公証）済み"
    if "rejected" in text:
        return False, "Appleの審査（公証）を受けていません"
    return False, "Appleの審査（公証）を確認できませんでした"


def install_app(app_path):
    dest_dir = "/Applications" if os.access("/Applications", os.W_OK) else os.path.join(HOME, "Applications")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(app_path))
    if os.path.exists(dest):
        if dialog(f"「{os.path.basename(dest)}」はすでにあります。新しいものに置き換えますか？", ["やめる", "置き換える"], "置き換える") != "置き換える":
            return None
        shutil.rmtree(dest, ignore_errors=True)
    code, _, err = run(["ditto", app_path, dest], 900)
    if code != 0:
        log(f"install failed: {err.strip()[:200]}")
        return None
    return dest


def find_apps(folder, depth=2):
    found = []
    for root, dirs, _ in os.walk(folder):
        if root[len(folder):].count(os.sep) >= depth:
            dirs[:] = []
        for d in list(dirs):
            if d.endswith(".app"):
                found.append(os.path.join(root, d))
                dirs.remove(d)
    return found


def offer_install(apps, level, source_name):
    for app in apps[:3]:
        ok, note = notarized(app)
        warn = "\n\n⚠ 注意が必要な結果が出ています。" if level == "yellow" else ""
        msg = (f"「{os.path.basename(app)}」をアプリケーションフォルダにインストールしますか？\n\n"
               f"元のファイル：{source_name}\n{note}{warn}")
        if dialog(msg, ["しない", "インストール"], "インストール", "note" if ok and level == "green" else "caution") == "インストール":
            dest = install_app(app)
            if dest:
                notify("インストールしました", os.path.basename(dest))
                log(f"installed {dest}")


def handle_safe(path, level, cfg):
    name = os.path.basename(path)
    low = name.lower()
    if low.endswith(".zip") and cfg.get("auto_unzip", True):
        out_dir = os.path.splitext(path)[0]
        n = 1
        while os.path.exists(out_dir):
            out_dir = f"{os.path.splitext(path)[0]} ({n})"
            n += 1
        code, _, err = run(["ditto", "-x", "-k", path, out_dir], 900)
        if code == 0:
            log(f"unzipped to {out_dir}")
            apps = find_apps(out_dir)
            if apps and cfg.get("ask_install", True):
                offer_install(apps, level, name)
            else:
                notify("解凍しました", os.path.basename(out_dir))
        else:
            log(f"unzip failed: {err.strip()[:200]}")
    elif low.endswith(".dmg") and cfg.get("ask_install", True):
        code, out, err = run(["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", path], 300)
        if code != 0:
            log(f"dmg attach failed: {err.strip()[:200]}")
            return
        mounts = []
        try:
            for ent in plistlib.loads(out.encode()).get("system-entities", []):
                if ent.get("mount-point"):
                    mounts.append(ent["mount-point"])
        except Exception:  # noqa: BLE001 - unexpected plist shape
            pass
        try:
            apps = [a for m in mounts for a in find_apps(m, 1)]
            if apps:
                offer_install(apps, level, name)
            else:
                notify("中にアプリが見つかりませんでした", name)
        finally:
            for m in mounts:
                run(["hdiutil", "detach", m, "-quiet"], 120)
    elif low.endswith(".pkg") and cfg.get("ask_install", True):
        if dialog(f"「{name}」はインストーラーです。開きますか？\n（このあとはMacのインストーラーの案内に従ってください）", ["開かない", "開く"], "開く") == "開く":
            run(["open", path], 30)


def record(entry):
    results = load_json(RESULTS_PATH, [])
    results.insert(0, entry)
    save_json(RESULTS_PATH, results[:300])


def process(path, cfg):
    name = os.path.basename(path)
    log(f"checking {path}")
    notify("ウイルスチェック中…", name)
    digest = sha256(path)
    clam = clam_scan(path)
    vt = vt_lookup(digest, cfg.get("vt_key", ""))
    level, reasons = verdict(clam, vt)
    entry = {"at": datetime.now().isoformat(timespec="seconds"), "file": path, "sha256": digest,
             "level": level, "reasons": reasons}
    if level == "red":
        dest = quarantine(path)
        entry["moved_to"] = dest
        log(f"RED {name} -> {dest} | {' / '.join(reasons)}")
        notify("🔴 危険なファイルを隔離しました", name)
        dialog(f"🔴 危険なファイルを見つけたので隔離しました。\n\n{name}\n\n" + "\n".join(reasons) +
               f"\n\n隔離先：{QUARANTINE}\n開かずに削除してください。", ["OK"], "OK", "stop")
    else:
        mark = "🟢 安全でした" if level == "green" else "🟡 注意"
        log(f"{level.upper()} {name} | {' / '.join(reasons)}")
        notify(mark, f"{name}：" + " / ".join(reasons))
        if level == "yellow" and not name.lower().endswith((".zip", ".dmg", ".pkg")):
            dialog(f"🟡 注意が必要です。\n\n{name}\n\n" + "\n".join(reasons) + "\n\n開く前に、本当に必要なファイルか確かめてください。",
                   ["OK"], "OK", "caution")
        try:
            handle_safe(path, level, cfg)
        except Exception as e:  # noqa: BLE001 - never stop the watcher
            log(f"post-processing error: {e}")
    record(entry)


# ---------- watcher ----------
def candidate(entry):
    n = entry.name
    if n.startswith(".") or n.endswith(PARTIAL_EXT) or not entry.is_file(follow_symlinks=False):
        return False
    return True


def snapshot(folders):
    snap = {}
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        try:
            for e in os.scandir(folder):
                if candidate(e):
                    st = e.stat(follow_symlinks=False)
                    snap[e.path] = [st.st_size, int(st.st_mtime)]
        except PermissionError:
            log(f"no permission to read {folder} (Full Disk Access is needed)")
    return snap


def fetch_icloud_placeholders(folder):
    """Ask iCloud to download files that are still placeholders (*.icloud)."""
    brctl = tool("brctl")
    if not brctl or not os.path.isdir(folder):
        return
    try:
        for e in os.scandir(folder):
            if e.name.startswith(".") and e.name.endswith(".icloud"):
                real = os.path.join(folder, e.name[1:-len(".icloud")])
                run([brctl, "download", real], 60)
    except OSError:
        pass


def main():
    os.makedirs(APP_DIR, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_PATH, {}))
    if not os.path.exists(CONFIG_PATH):
        save_json(CONFIG_PATH, cfg)
    state = load_json(STATE_PATH, {})
    seen = state.get("seen", {})
    folders = cfg["watch"]
    if os.path.isdir(ICLOUD):
        os.makedirs(os.path.join(ICLOUD, "Git Getter"), exist_ok=True)
    if not seen:  # first run: do not scan files that were already there
        seen = snapshot(folders)
    log(f"Git Getter Helper v{VERSION} started. watching: {', '.join(folders)}")
    pending = {}
    while True:
        cfg.update(load_json(CONFIG_PATH, {}))
        update_signatures(state)
        for f in folders:
            if f.startswith(ICLOUD):
                fetch_icloud_placeholders(f)
        now = snapshot(folders)
        for path, sig in now.items():
            if seen.get(path) == sig:
                continue
            if pending.get(path) == sig:  # unchanged for one poll -> finished writing
                pending.pop(path, None)
                seen[path] = sig
                try:
                    process(path, cfg)
                except Exception as e:  # noqa: BLE001 - never stop the watcher
                    log(f"error while checking {path}: {e}")
            else:
                pending[path] = sig
        seen = {p: s for p, s in seen.items() if p in now}
        state["seen"] = seen
        save_json(STATE_PATH, state)
        time.sleep(max(3, int(cfg.get("poll_seconds", 10))))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--check":
        # manual check of one file: python3 gitgetter_helper.py --check <file>
        c = dict(DEFAULT_CONFIG)
        c.update(load_json(CONFIG_PATH, {}))
        process(os.path.abspath(sys.argv[2]), c)
    else:
        main()

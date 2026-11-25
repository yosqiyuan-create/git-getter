#!/usr/bin/env python3
"""Git Getter Helper (Mac) by Urushihara.

Watches iCloud Drive/Git Getter (files sent from iPhone) and the Downloads
folder. When a new .zip / .dmg / .pkg arrives it asks what to do, then
unzips it, installs the app inside into the Applications folder, or opens
the installer.

Standard library only. Runs as a LaunchAgent installed by install.sh.
"""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from datetime import datetime

VERSION = "2.0.0"
HOME = os.path.expanduser("~")
APP_DIR = os.path.join(HOME, "GitGetterHelper")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
STATE_PATH = os.path.join(APP_DIR, "state.json")
HISTORY_PATH = os.path.join(APP_DIR, "history.json")
LOG_PATH = os.path.join(APP_DIR, "log.txt")
ICLOUD = os.path.join(HOME, "Library", "Mobile Documents", "com~apple~CloudDocs")
ICLOUD_GG = os.path.join(ICLOUD, "Git Getter")

DEFAULT_CONFIG = {
    "watch": [ICLOUD_GG, os.path.join(HOME, "Downloads")],
    "ask_first": True,        # ask before unzipping / installing
    "delete_after_install": False,
    "poll_seconds": 10,
}
HANDLED_EXT = (".zip", ".dmg", ".pkg")
PARTIAL_EXT = (".download", ".crdownload", ".part", ".partial", ".tmp", ".icloud")


# ---------- small utilities ----------
def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(LOG_PATH) > 2_000_000:
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


def dialog(msg, buttons, default=None):
    """Show a dialog and return the pressed button (None on timeout or cancel)."""
    btns = "{" + ", ".join(f'"{as_str(b)}"' for b in buttons) + "}"
    d = f' default button "{as_str(default)}"' if default else ""
    script = f'display dialog "{as_str(msg)}" with title "Git Getter Helper" buttons {btns}{d} giving up after 600'
    code, out, _ = run(["osascript", "-e", script], 700)
    if code != 0 or "gave up:true" in out:
        return None
    for b in buttons:
        if f"button returned:{b}" in out:
            return b
    return None


def unique(path):
    base, ext = os.path.splitext(path)
    n, cand = 1, path
    while os.path.exists(cand):
        cand = f"{base} ({n}){ext}"
        n += 1
    return cand


# ---------- actions ----------
def notarized(app_path):
    code, out, err = run(["spctl", "--assess", "--type", "execute", "-v", app_path], 120)
    if code == 0:
        return "Appleの審査（公証）済みのアプリです"
    if "rejected" in (out + err).lower():
        return "Appleの審査（公証）を受けていないアプリです。初めて開くときにMacが確認を求めます"
    return ""


def install_app(app_path):
    dest_dir = "/Applications" if os.access("/Applications", os.W_OK) else os.path.join(HOME, "Applications")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(app_path))
    if os.path.exists(dest):
        if dialog(f"「{os.path.basename(dest)}」はすでにあります。新しいものに置き換えますか？",
                  ["やめる", "置き換える"], "置き換える") != "置き換える":
            return None
        shutil.rmtree(dest, ignore_errors=True)
    code, _, err = run(["ditto", app_path, dest], 900)
    if code != 0:
        log(f"install failed: {err.strip()[:200]}")
        notify("インストールできませんでした", os.path.basename(app_path))
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


def offer_install(apps, source_name, cfg):
    installed = []
    for app in apps[:3]:
        note = notarized(app)
        msg = f"「{os.path.basename(app)}」をアプリケーションフォルダに入れますか？\n\n元のファイル：{source_name}"
        if note:
            msg += f"\n{note}"
        ans = dialog(msg, ["入れない", "インストール"], "インストール") if cfg.get("ask_first", True) else "インストール"
        if ans == "インストール":
            dest = install_app(app)
            if dest:
                installed.append(dest)
                notify("インストールしました", os.path.basename(dest))
                log(f"installed {dest}")
                if dialog(f"「{os.path.basename(dest)}」を今すぐ開きますか？", ["あとで", "開く"], "開く") == "開く":
                    run(["open", dest], 30)
    return installed


def handle_zip(path, cfg):
    name = os.path.basename(path)
    if cfg.get("ask_first", True):
        if dialog(f"「{name}」が届きました。解凍しますか？", ["しない", "解凍する"], "解凍する") != "解凍する":
            return "skipped", []
    out_dir = unique(os.path.splitext(path)[0])
    code, _, err = run(["ditto", "-x", "-k", path, out_dir], 900)
    if code != 0:
        log(f"unzip failed: {err.strip()[:200]}")
        notify("解凍できませんでした", name)
        return "error", []
    log(f"unzipped to {out_dir}")
    apps = find_apps(out_dir)
    if apps:
        return "unzipped", offer_install(apps, name, cfg)
    notify("解凍しました", os.path.basename(out_dir))
    run(["open", out_dir], 30)
    return "unzipped", []


def handle_dmg(path, cfg):
    name = os.path.basename(path)
    code, out, err = run(["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", path], 300)
    if code != 0:
        log(f"dmg attach failed: {err.strip()[:200]}")
        notify("開けませんでした", name)
        return "error", []
    mounts = []
    try:
        for ent in plistlib.loads(out.encode()).get("system-entities", []):
            if ent.get("mount-point"):
                mounts.append(ent["mount-point"])
    except Exception:  # noqa: BLE001 - unexpected plist shape
        pass
    try:
        apps = [a for m in mounts for a in find_apps(m, 1)]
        if not apps:
            notify("中にアプリが見つかりませんでした", name)
            return "noapp", []
        return "opened", offer_install(apps, name, cfg)
    finally:
        for m in mounts:
            run(["hdiutil", "detach", m, "-quiet"], 120)


def handle_pkg(path, cfg):
    name = os.path.basename(path)
    if dialog(f"「{name}」はインストーラーです。開きますか？\n（このあとはMacのインストーラーの案内に従ってください）",
              ["開かない", "開く"], "開く") == "開く":
        run(["open", path], 30)
        return "opened", []
    return "skipped", []


def process(path, cfg):
    name = os.path.basename(path)
    low = name.lower()
    log(f"arrived {path}")
    notify("ファイルが届きました", name)
    if low.endswith(".zip"):
        result, installed = handle_zip(path, cfg)
    elif low.endswith(".dmg"):
        result, installed = handle_dmg(path, cfg)
    else:
        result, installed = handle_pkg(path, cfg)
    if installed and cfg.get("delete_after_install"):
        try:
            os.remove(path)
            log(f"deleted {path}")
        except OSError:
            pass
    history = load_json(HISTORY_PATH, [])
    history.insert(0, {"at": datetime.now().isoformat(timespec="seconds"), "file": path,
                       "result": result, "installed": installed})
    save_json(HISTORY_PATH, history[:300])


# ---------- watcher ----------
def candidate(entry):
    n = entry.name.lower()
    return (not n.startswith(".") and n.endswith(HANDLED_EXT) and not n.endswith(PARTIAL_EXT)
            and entry.is_file(follow_symlinks=False))


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
    """Ask iCloud to download files that are still placeholders (.name.icloud)."""
    if not os.path.isdir(folder):
        return
    try:
        for e in os.scandir(folder):
            if e.name.startswith(".") and e.name.endswith(".icloud"):
                real = os.path.join(folder, e.name[1:-len(".icloud")])
                run(["brctl", "download", real], 60)
    except OSError:
        pass


def main():
    os.makedirs(APP_DIR, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_PATH, {}))
    if not os.path.exists(CONFIG_PATH):
        save_json(CONFIG_PATH, cfg)
    if os.path.isdir(ICLOUD):
        os.makedirs(ICLOUD_GG, exist_ok=True)
    state = load_json(STATE_PATH, {})
    seen = state.get("seen")
    folders = cfg["watch"]
    if seen is None:  # first run: leave files that were already there alone
        seen = snapshot(folders)
    log(f"Git Getter Helper v{VERSION} started. watching: {', '.join(folders)}")
    pending = {}
    while True:
        cfg.update(load_json(CONFIG_PATH, {}))
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
                    log(f"error with {path}: {e}")
            else:
                pending[path] = sig
        seen = {p: s for p, s in seen.items() if p in now}
        state["seen"] = seen
        save_json(STATE_PATH, state)
        time.sleep(max(3, int(cfg.get("poll_seconds", 10))))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--open":
        # handle one file by hand: python3 gitgetter_helper.py --open <file>
        c = dict(DEFAULT_CONFIG)
        c.update(load_json(CONFIG_PATH, {}))
        process(os.path.abspath(sys.argv[2]), c)
    else:
        main()

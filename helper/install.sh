#!/bin/bash
# Git Getter Helper installer (Mac) by Urushihara
# Usage (Terminal):  curl -fsSL "<site>/helper/install.sh" | GG_BASE="<site>" bash
set -u

say(){ printf "\n\033[1m%s\033[0m\n" "$1"; }
DIR="$HOME/GitGetterHelper"
PLIST="$HOME/Library/LaunchAgents/com.urushihara.gitgetter.helper.plist"
LABEL="com.urushihara.gitgetter.helper"
BASE="${GG_BASE:-}"

say "Git Getter Helper（Mac補助アプリ）をインストールします"
if [ "$(uname)" != "Darwin" ]; then echo "このインストーラーはMac専用です。"; exit 1; fi
mkdir -p "$DIR" "$HOME/Library/LaunchAgents"

# 1. Download the helper program
say "1/4 プログラムを取り込んでいます…"
if [ -n "$BASE" ]; then
  if ! curl -fsSL "$BASE/helper/gitgetter_helper.py" -o "$DIR/gitgetter_helper.py.new"; then
    echo "ダウンロードできませんでした。インターネット接続を確認してください。"; exit 1
  fi
  mv "$DIR/gitgetter_helper.py.new" "$DIR/gitgetter_helper.py"
  curl -fsSL "$BASE/helper/uninstall.sh" -o "$DIR/uninstall.sh" 2>/dev/null && chmod +x "$DIR/uninstall.sh"
else
  SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
  cp "$SRC/gitgetter_helper.py" "$DIR/" || { echo "gitgetter_helper.py が見つかりません。"; exit 1; }
  [ -f "$SRC/uninstall.sh" ] && cp "$SRC/uninstall.sh" "$DIR/" && chmod +x "$DIR/uninstall.sh"
fi

# 2. Python
say "2/4 Pythonを確認しています…"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 がありません。表示される画面で「インストール」を押し、終わったらもう一度この1行を実行してください。"
  xcode-select --install 2>/dev/null
  exit 1
fi
PY="$(python3 -c 'import sys; print(sys.executable)')"
echo "使うPython：$PY"

# 3. Folders and settings
say "3/4 フォルダと設定を用意しています…"
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs"
if [ -d "$ICLOUD" ]; then
  mkdir -p "$ICLOUD/Git Getter" && echo "iCloud Drive に「Git Getter」フォルダを用意しました。"
else
  echo "iCloud Drive が見つかりません（「ダウンロード」フォルダだけを見張ります）。"
fi
if [ ! -f "$DIR/config.json" ]; then
  "$PY" - "$DIR/config.json" <<'PYEOF'
import json, os, sys
home = os.path.expanduser("~")
icloud = os.path.join(home, "Library", "Mobile Documents", "com~apple~CloudDocs", "Git Getter")
cfg = {"watch": [icloud, os.path.join(home, "Downloads")], "ask_first": True,
       "delete_after_install": False, "poll_seconds": 10}
json.dump(cfg, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PYEOF
  echo "設定を保存しました：$DIR/config.json"
else
  echo "前の設定をそのまま使います：$DIR/config.json"
fi

# 4. Start automatically at login (LaunchAgent)
say "4/4 Macの起動時に自動で動くようにしています…"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/gitgetter_helper.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StandardOutPath</key><string>$DIR/stdout.txt</string>
  <key>StandardErrorPath</key><string>$DIR/stderr.txt</string>
</dict></plist>
PLISTEOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "起動しました。"

say "✅ インストールが終わりました"
cat <<EOF
最後に1つだけ、Macの許可が必要です（「ダウンロード」と「iCloud Drive」を見るため）：
  1. このあと開く「フルディスクアクセス」の画面で、下の「＋」を押す
  2. Command + Shift + G を押して、次の場所を貼り付けてEnter：
       $PY
  3. 「開く」を押して、スイッチをオンにする
許可したあと、ターミナルで次の1行を実行すると、補助アプリが再起動します：
       launchctl kickstart -k gui/$(id -u)/$LABEL

記録：$DIR/log.txt
アンインストール：bash "$DIR/uninstall.sh"
EOF
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null
exit 0

#!/bin/bash
# Git Getter Helper uninstaller (Mac) by Urushihara
LABEL="com.urushihara.gitgetter.helper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$PLIST"
echo "Git Getter Helper を止めて、自動起動を解除しました。"
echo "記録と設定は $HOME/GitGetterHelper に残しています（不要ならフォルダごとゴミ箱へ）。"
echo "隔離したファイルは $HOME/Git Getter 隔離 にあります。"
echo "ClamAV も消す場合：brew uninstall clamav"

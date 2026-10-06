#!/bin/bash

PATH=/sbin/opt/homebrew/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin

# このスクリプトが存在するディレクトリに移動
CWD_DIR="$(dirname "$0")"
cd "$CWD_DIR"

# 前日の情報を出力
python3 sleepwatch.py --no-legend --date `date -v-1d "+%Y-%m-%d"`
echo ""

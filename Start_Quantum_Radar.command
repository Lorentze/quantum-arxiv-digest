#!/usr/bin/env bash
set -euo pipefail

QRADAR_PROJECT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$QRADAR_PROJECT_DIR"
if ! command -v python3 >/dev/null 2>&1; then
  echo '需要 Python 3.11 或更新版本。安装地址：https://www.python.org/downloads/'
  read -r -p '按回车关闭…' QRADAR_CLOSE
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo '当前 Python 版本过旧，请安装 Python 3.11 或更新版本。'
  read -r -p '按回车关闭…' QRADAR_CLOSE
  exit 1
fi
export PYTHONPATH="$QRADAR_PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
echo '正在启动量子论文雷达。保留这个终端窗口；按 Ctrl+C 停止。'
exec python3 -m qradar serve --open "$@"

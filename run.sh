#!/usr/bin/env bash
# 扫描版人文书 -> 阅读版 Markdown / EPUB 本地 harness 统一入口
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
# 优先项目 venv（Windows 的 venv 带 Scripts/，POSIX 带 bin/）
if [ -x "$ROOT/.venv/Scripts/python" ]; then
  PY="$ROOT/.venv/Scripts/python"
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi
exec "$PY" -m book_harness.cli "$@"

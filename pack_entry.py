"""PyInstaller 打包入口：无参数启动 GUI，带参数则透传给 cli。"""
import sys

from book_harness.cli import main

if len(sys.argv) == 1:
    sys.argv.append("gui")

raise SystemExit(main())

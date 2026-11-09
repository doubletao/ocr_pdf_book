from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .clean import run_clean
from .export import build_epub, verify_epub
from .parse import parse_pdf
from .selftest import run_selftest
from .util import emit_progress, load_json_any, parse_page_range

# PyInstaller frozen 时 ROOT = exe 所在目录，config/work/dist 都落在用户可见处
ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent)


def log(*a):
    print(*a, file=sys.stderr)


def load_cfg(path: str | None) -> dict:
    p = Path(path) if path else ROOT / "config.json"
    cfg = load_json_any(p)
    cfg["_config_path"] = str(p)
    return cfg


def _patch_fuse_rms_norm() -> str | None:
    """关闭 PaddleOCR-VL 的 fuse_rms_norm（官方 wheel 缺 `fused_rms_norm_ext`
    kernel，开着会直接崩：Windows 报 kernel not registered，Linux 段错误）。

    对精度无影响，只损失一点性能。best-effort：找不到目标文件就跳过。
    路径用 sysconfig 解析，venv 下 Windows/Linux 通用。
    """
    import sysconfig

    site = Path(sysconfig.get_path("purelib")) / "paddlex"
    if not site.is_dir():
        return None
    for f in site.rglob("_config.py"):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        if "self.fuse_rms_norm = True" in text:
            f.write_text(
                text.replace("self.fuse_rms_norm = True", "self.fuse_rms_norm = False"),
                encoding="utf-8",
            )
            return str(f)
    return None


def _has_nvidia_gpu() -> bool:
    """setup 阶段还没装 paddle，靠 nvidia-smi 探测硬件决定装哪个 wheel。

    有的驱动把 nvidia-smi 放在 Program Files 下且不入 PATH，
    which 找不到时再按常见绝对路径找一次。
    """
    from shutil import which

    exe = which("nvidia-smi")
    if not exe:
        for c in (r"C:\Windows\System32\nvidia-smi.exe",
                  r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if Path(c).is_file():
                exe = c
                break
    if not exe:
        log("[setup] 未找到 nvidia-smi（PATH 及常见路径均无）-> 判定为无 NVIDIA GPU")
        return False
    try:
        r = subprocess.run([exe, "-L"], capture_output=True, timeout=10)
        ok = r.returncode == 0 and b"GPU" in r.stdout
    except Exception:
        ok = False
    log(f"[setup] nvidia-smi -> {'GPU ' + exe if ok else '不可用/无 GPU (' + exe + ')'}")
    return ok


def cmd_setup(args, cfg) -> dict:
    ver = cfg["paddle_version"]
    dev = str(cfg.get("device", "auto")).lower()
    use_gpu = dev == "gpu" or (dev == "auto" and _has_nvidia_gpu())
    if use_gpu:
        pkg, index = f"paddlepaddle-gpu=={ver}", cfg["python_index_gpu"]
    else:
        pkg, index = f"paddlepaddle=={ver}", cfg["python_index_cpu"]
    log(f"[setup] device={dev} -> {'GPU wheel' if use_gpu else 'CPU wheel'}")
    cmds = [
        [sys.executable, "-m", "pip", "install", pkg, "-i", index],
        [sys.executable, "-m", "pip", "install", "-U", cfg["paddleocr_spec"]],
        # sentencepiece 0.2.3 的 pb2 是用 protobuf 7.36 gencode 生成的，
        # paddle 装完常把运行时留在 6.x，产线初始化会报 gencode/runtime 不兼容
        [sys.executable, "-m", "pip", "install", "-U", "protobuf>=7.36.1"],
    ]
    # PaddleOCR-VL 1.6 需要配套 safetensors；Windows 只有这个 nightly wheel 可用
    if sys.platform == "win32" and cfg.get("safetensors_wheel_win"):
        cmds.append([sys.executable, "-m", "pip", "install", cfg["safetensors_wheel_win"]])
    if args.dry_run:
        return {"ok": True, "stage": "setup", "commands": [" ".join(c) for c in cmds], "executed": False}
    for c in cmds:
        log("[setup] " + " ".join(c))
        subprocess.run(c, check=True)

    patched = _patch_fuse_rms_norm()
    if patched:
        log(f"[setup] 已关闭 fuse_rms_norm：{patched}")
    return {"ok": True, "stage": "setup", "executed": True, "fuse_rms_norm_patched": bool(patched)}


def cmd_probe(args, cfg, work: Path, dist: Path) -> dict:
    if not args.pdf:
        raise SystemExit("probe 需要 --pdf book.pdf")
    start, end = parse_page_range(args.pages or "1-3")
    info = parse_pdf(args.pdf, cfg, work, start, end)
    payload = load_json_any(work / "pages.json")
    labels: dict[str, int] = {}
    samples: dict[str, str] = {}
    for p in payload["pages"]:
        for b in p["blocks"]:
            l = b["label"]
            labels[l] = labels.get(l, 0) + 1
            samples.setdefault(l, b["content"][:60].replace("\n", " "))
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "probe_labels.json").write_text(
        json.dumps({"labels": labels, "samples": samples}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log("[probe] 实际 block_label 分布：" + json.dumps(labels, ensure_ascii=False))
    log("[probe] 各标签样例见 dist/probe_labels.json，请据此校正 config.json 的 drop_labels / title_level")
    return {"ok": True, "stage": "probe", **info, "labels": labels,
            "probe_labels_json": str(dist / "probe_labels.json")}


def cmd_parse(args, cfg, work: Path) -> dict:
    if not args.pdf:
        raise SystemExit("parse 需要 --pdf book.pdf")
    start, end = parse_page_range(args.pages)
    info = parse_pdf(args.pdf, cfg, work, start, end)
    return {"ok": True, "stage": "parse", **info}


def cmd_epub(args, cfg, dist: Path) -> dict:
    md_path = dist / "阅读版.md"
    if not md_path.is_file():
        raise FileNotFoundError(f"缺少 {md_path}，先执行 ./run.sh clean")
    out = dist / "阅读版.epub"
    emit_progress(stage="epub")
    title = getattr(args, "title", None) or cfg.get("book_title", "扫描书阅读版")
    build_epub(md_path.read_text(encoding="utf-8"), out, title)
    ok = verify_epub(out)
    return {"ok": ok, "stage": "epub", "outputs": {"epub": str(out)}, "verified": ok}


def cmd_all(args, cfg, work: Path, dist: Path) -> dict:
    if not args.pdf:
        raise SystemExit("all 需要 --pdf book.pdf")
    start, end = parse_page_range(args.pages)
    p = parse_pdf(args.pdf, cfg, work, start, end)
    c = run_clean(work, dist, cfg)
    e = cmd_epub(args, cfg, dist)
    return {"ok": bool(c["ok"] and e["ok"]), "stage": "all",
            "pages": p["pages"], "stats": {k: v for k, v in c.items() if k not in ("outputs",)},
            "outputs": {**p, **c["outputs"], **e["outputs"]}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="run.sh",
        description="扫描版人文书 -> 阅读版 Markdown/EPUB 本地 harness",
    )
    ap.add_argument("--config", help="配置文件路径，默认 ./config.json")
    ap.add_argument("--work", default=str(ROOT / "work"), help="中间产物目录")
    ap.add_argument("--dist", default=str(ROOT / "dist"), help="最终产物目录")
    ap.add_argument("--device", choices=("auto", "cpu", "gpu"),
                    help="推理设备，覆盖 config.json 的 device（auto=优先GPU不可用则CPU）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="按 config.json 安装 PaddlePaddle + PaddleOCR")
    s.add_argument("--dry-run", action="store_true", help="只打印安装命令")

    s = sub.add_parser("selftest", help="零依赖合成数据自测（不需要 GPU / Paddle）")

    for name, help_ in [("probe", "只跑前几页，打印实际 block_label"),
                        ("parse", "整本/指定页解析成 work/pages.json"),
                        ("all", "parse + clean + epub 一条龙")]:
        s = sub.add_parser(name, help=help_)
        s.add_argument("--pdf", help="输入 PDF 路径")
        s.add_argument("--pages", help="页范围，如 '1-3'、'20'；不填为全本")
        s.add_argument("--title", help="EPUB 书名，默认取 config.json 的 book_title")

    s = sub.add_parser("clean", help="从 work/pages.json 生成阅读版 Markdown")
    s = sub.add_parser("epub", help="从阅读版 Markdown 生成 EPUB")
    sub.add_parser("gui", help="图形界面：选文件 / 任务队列 / 进度")

    args = ap.parse_args(argv)
    if args.cmd == "gui":  # GUI 自己管事件循环，不走 JSON 报告协议
        from .gui import run_gui

        run_gui()
        return 0

    cfg = load_cfg(args.config)
    if args.device:
        cfg["device"] = args.device
    work, dist = Path(args.work), Path(args.dist)

    try:
        if args.cmd == "setup":
            report = cmd_setup(args, cfg)
        elif args.cmd == "selftest":
            report = run_selftest(ROOT, cfg)
        elif args.cmd == "probe":
            report = cmd_probe(args, cfg, work, dist)
        elif args.cmd == "parse":
            report = cmd_parse(args, cfg, work)
        elif args.cmd == "clean":
            report = run_clean(work, dist, cfg)
        elif args.cmd == "epub":
            report = cmd_epub(args, cfg, dist)
        else:
            report = cmd_all(args, cfg, work, dist)
    except Exception as e:
        report = {"ok": False, "stage": args.cmd, "error": f"{type(e).__name__}: {e}"}
        log("[harness] " + report["error"])

    # stdout 只留一行纯 JSON，方便外层 harness 直接 json.loads
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

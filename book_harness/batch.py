"""字符界面批量模式：扫描 book/ 下的 PDF，逐本串行转换，结果写回 book/。

- 每本书一个子进程（paddle 段错误只影响当前这本，不会拖垮整个队列）；
- 每本书独立 work/<书名>/，逐页落盘，中断后重跑自动续跑；
- 成功后写 work/<书名>/done.json，下次启动 PDF 未变化且产物仍在就直接跳过；
- 产物：book/<书名>.epub、book/<书名>.md；完整日志 work/<书名>/console.log。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .util import get_pdf_page_count, parse_progress

ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent)

# 终端只显示 harness 关键行；paddle 的告警/模型加载刷屏写进 console.log
LOG_KEEP = re.compile(
    r"^\[(?:clean|epub|harness|device)\]|^Traceback|^[A-Za-z_]*(?:Error|Exception):"
    r"|Segmentation fault"
)


def say(msg: str = "") -> None:
    print(msg, flush=True)


def _fmt_secs(s: float) -> str:
    s = int(s)
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s % 60:02d}s"


def _pdf_sig(pdf: Path) -> dict:
    st = pdf.stat()
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def _outputs(book_dir: Path, stem: str) -> dict[str, Path]:
    return {"epub": book_dir / f"{stem}.epub", "md": book_dir / f"{stem}.md"}


def is_done(pdf: Path, work: Path, book_dir: Path) -> bool:
    """done.json 记录的 PDF 签名一致、且产物都还在，才算已完成。"""
    marker = work / "done.json"
    if not marker.is_file():
        return False
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return False
    if info.get("pdf") != _pdf_sig(pdf):
        return False
    return all(p.is_file() for p in _outputs(book_dir, pdf.stem).values())


def _resume_pages(work: Path, pdf: Path) -> int:
    src = work / "pages.json"
    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if payload.get("source") != str(pdf.resolve()):
        return 0
    return len(payload.get("pages") or [])


def _child_cmd(args: list[str]) -> list[str]:
    # frozen 的 exe 本身就是入口，不能再带 "-m book_harness.cli"
    base = ([sys.executable] if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "book_harness.cli"])
    return base + args


def run_one(pdf: Path, work: Path, book_dir: Path, extra: list[str]) -> dict:
    out_dir = work / "out"
    cmd = _child_cmd(extra + ["--work", str(work), "--dist", str(out_dir),
                              "all", "--pdf", str(pdf), "--title", pdf.stem])
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    t0 = time.time()
    state = {"first": None, "first_t": None, "last_err": ""}

    def pump() -> None:
        assert proc.stderr is not None
        with open(work / "console.log", "a", encoding="utf-8") as console:
            console.write(f"\n===== {time.strftime('%F %T')} {' '.join(cmd)}\n")
            for line in proc.stderr:
                console.write(line)
                console.flush()
                ev = parse_progress(line)
                if ev:
                    _show_progress(ev, state, t0)
                    continue
                s = line.rstrip("\n")
                if re.match(r"^[A-Za-z_]*(?:Error|Exception):|.*Segmentation fault", s):
                    state["last_err"] = s
                if LOG_KEEP.search(s):
                    say(f"    {s}")

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    try:
        assert proc.stdout is not None
        out = proc.stdout.read()
        code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    th.join(timeout=5)

    report: dict = {}
    for ln in reversed(out.splitlines()):
        if ln.strip().startswith("{"):
            try:
                report = json.loads(ln)
            except Exception:
                pass
            break
    if code != 0 or not report.get("ok"):
        err = report.get("error") or state["last_err"] or f"子进程退出码 {code}"
        if code == -11 or code == 139:
            err = f"段错误（退出码 {code}）：{err}"
        raise RuntimeError(err)

    dst = _outputs(book_dir, pdf.stem)
    shutil.copyfile(out_dir / "阅读版.epub", dst["epub"])
    shutil.copyfile(out_dir / "阅读版.md", dst["md"])
    (work / "done.json").write_text(json.dumps({
        "pdf": _pdf_sig(pdf), "source": str(pdf.resolve()),
        "finished": time.strftime("%F %T"), "pages": report.get("pages"),
        "outputs": {k: str(v) for k, v in dst.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"pages": report.get("pages"), "outputs": dst, "secs": time.time() - t0}


def _show_progress(ev: dict, state: dict, t0: float) -> None:
    stage = ev.get("stage")
    if stage != "parse":
        say(f"    [{stage}]")
        return
    page, total = int(ev.get("page") or 0), ev.get("total") or 0
    now = time.time()
    if state["first"] is None:  # 续跑起点：用它来算本次的单页速度
        state["first"], state["first_t"] = page, now
        if ev.get("resumed"):
            say(f"    续跑：已有 {page} 页，从第 {page + 1} 页继续")
        return
    if ev.get("done"):
        say(f"    识别完成 {page}/{total} 页，用时 {_fmt_secs(now - t0)}")
        return
    done_now = page - state["first"]
    eta = ""
    if done_now > 0 and total:
        per = (now - state["first_t"]) / done_now
        eta = f"  {per:.0f}s/页  剩余约 {_fmt_secs(per * (total - page))}"
    say(f"    第 {page}/{total} 页{eta}")


def _acquire_lock(path: Path):
    """防重复启动：对锁文件加独占锁，拿不到说明已有批量任务在跑。

    锁由操作系统随进程释放（含段错误、kill -9、断电），不会残留死锁；
    锁文件本身留在磁盘上无害。返回打开的文件对象，需在整个批量期间持有。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:  # Windows 下被锁的字节读不了，读不到就不显示 pid
            f.seek(0)
            holder = f.read().strip() or "未知"
        except OSError:
            holder = "未知"
        f.close()
        return None, holder
    f.seek(0)
    f.truncate()
    f.write(f"pid={os.getpid()} started={time.strftime('%F %T')}")
    f.flush()
    return f, None


def run_batch(book_dir: Path, extra: list[str]) -> int:
    lock, holder = _acquire_lock(ROOT / "work" / "batch.lock")
    if lock is None:
        say(f"[batch] 已有批量任务在运行（{holder}），不重复启动。")
        say("        如需重启，先在原终端按 Ctrl+C 结束它。")
        return 2
    try:
        return _run_batch(book_dir, extra)
    finally:
        lock.close()


def _run_batch(book_dir: Path, extra: list[str]) -> int:
    book_dir = book_dir.resolve()
    if not book_dir.is_dir():
        say(f"[batch] 找不到书目录 {book_dir}，请新建并放入 PDF")
        return 1
    pdfs = sorted(p for p in book_dir.iterdir()
                  if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdfs:
        say(f"[batch] {book_dir} 下没有 PDF")
        return 1

    jobs = []
    say(f"[batch] 扫描 {book_dir}：{len(pdfs)} 本")
    for i, pdf in enumerate(pdfs, 1):
        work = ROOT / "work" / pdf.stem
        if is_done(pdf, work, book_dir):
            mark = "已完成，跳过"
        else:
            n = _resume_pages(work, pdf)
            mark = f"待续跑（已识别 {n} 页）" if n else "待处理"
            jobs.append((pdf, work))
        total = get_pdf_page_count(pdf)
        say(f"  {i}. {pdf.name}  {total or '?'} 页  {mark}")

    ok, failed = [], []
    try:
        for i, (pdf, work) in enumerate(jobs, 1):
            work.mkdir(parents=True, exist_ok=True)
            say(f"\n[batch] ({i}/{len(jobs)}) {pdf.name}")
            try:
                r = run_one(pdf, work, book_dir, extra)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                failed.append((pdf, str(e)))
                say(f"    失败：{e}")
                say(f"    完整日志：{work / 'console.log'}；重新启动会从断点续跑")
                continue
            ok.append(pdf)
            say(f"    完成 {r['pages']} 页，用时 {_fmt_secs(r['secs'])}"
                f" -> {r['outputs']['epub'].name}")
    except KeyboardInterrupt:
        say("\n[batch] 已中断。已识别的页都已落盘，重新启动即从断点续跑。")
        return 130

    say(f"\n[batch] 结束：本次成功 {len(ok)} 本，失败 {len(failed)} 本，"
        f"跳过 {len(pdfs) - len(jobs)} 本")
    for pdf, err in failed:
        say(f"  失败 {pdf.name}：{err}")
    return 1 if failed else 0

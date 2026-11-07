from __future__ import annotations

import itertools
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .util import get_pdf_page_count, parse_progress

ROOT = Path(__file__).resolve().parent.parent

# 只把 harness 自己的关键日志喂给界面；paddle 的下载进度/告警刷屏且无用，
# 全量原始日志仍写进 work/<key>/console.log 供排错
LOG_KEEP = re.compile(
    r"^\[(?:parse|clean|epub|probe|harness|setup)\]|^Traceback|^[A-Za-z_]*(?:Error|Exception):"
)
MAX_LOG_LINES = 400

QUEUED, RUNNING, DONE, FAILED, STOPPED = "排队中", "处理中", "完成", "失败", "已停止"


class Task:
    _seq = itertools.count(1)

    def __init__(self, path: Path, key: str, total: int | None):
        self.id = f"t{next(Task._seq)}"
        self.path = path
        self.key = key            # work/<key>/ 与 dist/<key>/，每本书一个目录互不覆盖
        self.total = total
        self.page = 0
        self.stage = ""
        self.status = QUEUED
        self.enqueued = False
        self.proc: subprocess.Popen | None = None
        self.stop_requested = False


class Worker(threading.Thread):
    """串行消费任务队列：CPU 模式并行跑多本书只会互相抢核，没有任何好处。"""

    def __init__(self, app: "App"):
        super().__init__(daemon=True, name="task-worker")
        self.app = app

    def run(self) -> None:
        while True:
            task = self.app.pending.get()
            if task is None or self.app.stop_all:
                break
            self.app.current = task
            self.app.events.put(("started", task))
            try:
                report = self._run(task)
                self.app.events.put(("stopped" if task.stop_requested else "done",
                                     task, report))
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                self.app.events.put(("stopped", task) if task.stop_requested
                                    else ("failed", task, msg))
            finally:
                self.app.current = None
        self.app.events.put(("idle", None))

    def _run(self, task: Task) -> dict:
        work = ROOT / "work" / task.key
        dist = ROOT / "dist" / task.key
        work.mkdir(parents=True, exist_ok=True)

        cmd = [sys.executable, "-m", "book_harness.cli",
               "--work", str(work), "--dist", str(dist),
               "all", "--pdf", str(task.path), "--title", task.path.stem]
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace")
        task.proc = proc
        console = open(work / "console.log", "a", encoding="utf-8")

        def pump() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                console.write(line)
                console.flush()
                ev = parse_progress(line)
                if ev:
                    self.app.events.put(("progress", task, ev))
                elif LOG_KEEP.match(line):
                    self.app.events.put(("log", task, line.rstrip("\n")))

        threading.Thread(target=pump, daemon=True).start()

        assert proc.stdout is not None
        out = proc.stdout.read()      # 只有最后一行 JSON，进程退出即 EOF
        code = proc.wait()
        console.close()
        task.proc = None

        report = {}
        for ln in reversed(out.splitlines()):
            if ln.strip().startswith("{"):
                try:
                    report = json.loads(ln)
                except Exception:
                    pass
                break
        if code != 0:
            raise RuntimeError(_last_error(work, report))
        if not report.get("ok", False):
            raise RuntimeError(f"子进程未报告成功：{out[-300:]}")
        return report


def _last_error(work: Path, report: dict) -> str:
    """stdout 只有最终 JSON，失败原因去 console.log 捞最后一条错误行。"""
    if report.get("error"):
        return str(report["error"])[:300]
    try:
        lines = (work / "console.log").read_text(
            encoding="utf-8", errors="replace").splitlines()
        for ln in reversed(lines):
            if re.match(r"^[A-Za-z_]*(?:Error|Exception):", ln):
                return ln[:300]
    except Exception:
        pass
    return "子进程异常退出（详情见 work/<目录>/console.log）"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("扫描书 → EPUB 批量转换")
        self.geometry("940x660")
        self.minsize(760, 480)

        self.tasks: list[Task] = []
        self.pending: queue.Queue = queue.Queue()
        self.events: queue.Queue = queue.Queue()
        self.current: Task | None = None
        self.stop_all = False

        self._build_ui()
        Worker(self).start()
        self.after(100, self._poll)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="添加 PDF…", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="移除选中", command=self.remove_selected).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="重新入队", command=self.retry_selected).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="开始", command=self.start_queue).pack(side="left", padx=(18, 0))
        ttk.Button(bar, text="停止当前", command=self.stop_current).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="打开输出目录", command=self.open_dist).pack(side="right")

        cols = ("pages", "progress", "status", "result")
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=False, padx=8)
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="tree headings",
                                 selectmode="extended", height=8)
        self.tree.heading("#0", text="PDF 文件")
        self.tree.column("#0", width=330, anchor="w")
        for c, txt, w, anchor in (("pages", "总页数", 70, "e"),
                                  ("progress", "进度", 160, "w"),
                                  ("status", "状态", 85, "center"),
                                  ("result", "结果", 175, "w")):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor=anchor)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.retry_selected())

        cur = ttk.Frame(self, padding=(8, 6, 8, 2))
        cur.pack(fill="x")
        self.cur_label = ttk.Label(cur, text="空闲：点「添加 PDF…」加入队列，再点「开始」")
        self.cur_label.pack(fill="x")
        self.pbar = ttk.Progressbar(cur, maximum=100)
        self.pbar.pack(fill="x", pady=(4, 0))

        logf = ttk.LabelFrame(self, text="日志", padding=4)
        logf.pack(fill="both", expand=True, padx=8, pady=(6, 4))
        self.log = tk.Text(logf, height=12, wrap="word", state="disabled",
                           font=("Consolas", 9), background="#1e1e1e",
                           foreground="#d4d4d4")
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var, relief="sunken",
                  anchor="w", padding=(6, 2)).pack(fill="x", side="bottom")

    # ---------------- 操作 ----------------
    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择扫描版 PDF（可多选）",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
        for p in paths:
            self._add_task(Path(p))

    def _add_task(self, path: Path) -> None:
        if not path.is_file():
            return
        task = Task(path, self._unique_key(path.stem), get_pdf_page_count(path))
        self.tasks.append(task)
        total = str(task.total) if task.total else "?"
        hint = self._resume_hint(task.key)
        self.tree.insert("", "end", iid=task.id, text=str(path),
                         values=(total, hint or "—", QUEUED, ""))
        msg = f"[gui] 已加入队列：{path.name}（{total} 页）"
        if hint:
            msg += f"，{hint}"
        self._append_log(msg)

    def _unique_key(self, stem: str) -> str:
        """每本书独立的 work/dist 子目录名，避免多本书互相覆盖 pages.json。"""
        base = re.sub(r"[^\w\-.]+", "_", stem).strip("._") or "book"
        used = {t.key for t in self.tasks}
        key, n = base, 1
        while key in used:
            n += 1
            key = f"{base}_{n}"
        return key

    @staticmethod
    def _resume_hint(key: str) -> str:
        src = ROOT / "work" / key / "pages.json"
        if not src.is_file():
            return ""
        try:
            done = len(json.loads(src.read_text(encoding="utf-8")).get("pages", []))
        except Exception:
            return ""
        return f"历史 {done} 页，可续跑" if done else ""

    def remove_selected(self) -> None:
        for iid in self.tree.selection():
            task = self._task(iid)
            if task and task.status == RUNNING:
                continue
            if task:
                self.tasks.remove(task)
            self.tree.delete(iid)

    def retry_selected(self) -> None:
        """双击/点按钮：把失败或已停止的任务重新入队，续跑不重跑。"""
        n = 0
        for iid in self.tree.selection():
            task = self._task(iid)
            if not task or task.status in (QUEUED, RUNNING):
                continue
            task.status, task.stage, task.stop_requested = QUEUED, "", False
            self.tree.set(iid, "status", QUEUED)
            self.tree.set(iid, "result", "")
            self.pending.put(task)
            n += 1
        if n:
            self._append_log(f"[gui] 重新入队 {n} 个任务")

    def start_queue(self) -> None:
        n = 0
        for task in self.tasks:
            if task.status == QUEUED and not task.enqueued:
                task.enqueued = True
                self.pending.put(task)
                n += 1
        self._append_log(f"[gui] 启动 {n} 个任务" if n else "[gui] 没有待启动的任务")

    def stop_current(self) -> None:
        cur = self.current
        if cur and cur.proc:
            cur.stop_requested = True
            cur.proc.terminate()
            self._append_log(f"[gui] 已请求停止：{cur.path.name}（进度已落盘，重新入队可续跑）")
        else:
            self._append_log("[gui] 当前没有正在处理的任务")

    def open_dist(self) -> None:
        os.startfile(str(ROOT / "dist"))

    # ---------------- 事件 ----------------
    def _poll(self) -> None:
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle(ev)
            except Exception as e:  # 单个事件处理失败不能卡死整个轮询
                print(f"[gui] 事件处理异常: {e}", file=sys.stderr)
        self.after(100, self._poll)

    def _handle(self, ev) -> None:
        kind = ev[0]
        if kind == "started":
            task = ev[1]
            task.status = RUNNING
            self.tree.set(task.id, "status", RUNNING)
            self.cur_label.configure(text=f"正在处理：{task.path.name}")
            self._append_log(f"[gui] 开始：{task.path.name}")
        elif kind == "progress":
            self._on_progress(ev[1], ev[2])
        elif kind == "log":
            self._append_log(ev[1] if isinstance(ev[1], str) else ev[2])
        elif kind == "done":
            task, report = ev[1], ev[2]
            task.status = DONE
            pages = report.get("pages", "")
            chars = (report.get("stats") or {}).get("chars", "")
            result = " · ".join(x for x in (f"{pages} 页" if pages else "",
                                            f"{chars} 字" if chars else "") if x) or "OK"
            self.tree.set(task.id, "status", DONE)
            self.tree.set(task.id, "result", result)
            self.tree.set(task.id, "progress",
                          f"{task.total}/{task.total} (100%)" if task.total else "100%")
            self._append_log(f"[gui] 完成：{task.path.name}　{result}")
        elif kind == "failed":
            task, msg = ev[1], ev[2]
            task.status, task.enqueued = FAILED, False
            self.tree.set(task.id, "status", FAILED)
            self.tree.set(task.id, "result", msg[:60])
            self._append_log(f"[gui] 失败：{task.path.name}：{msg}")
        elif kind == "stopped":
            task = ev[1]
            task.status, task.enqueued = STOPPED, False
            self.tree.set(task.id, "status", STOPPED)
            self.tree.set(task.id, "result", "已停止，可重新入队续跑")
            self._append_log(f"[gui] 已停止：{task.path.name}")
        elif kind == "idle":
            self.cur_label.configure(text="空闲：队列已跑完")
            self.status_var.set("就绪")

    def _on_progress(self, task: Task, p: dict) -> None:
        total = p.get("total") or task.total
        page = p.get("page") or 0
        if total:
            task.total, task.page = total, page
            pct = 100.0 * page / total
            self.pbar.configure(value=pct)
            self.tree.set(task.id, "pages", str(total))
            if p.get("resumed"):
                self.tree.set(task.id, "progress", f"历史 {page}/{total}，续跑中")
            elif not p.get("done"):
                self.tree.set(task.id, "progress", f"{page}/{total} ({pct:.0f}%)")
            self.cur_label.configure(
                text=f"正在处理：{task.path.name}　第 {page}/{total} 页（{pct:.0f}%）")
        stage = p.get("stage")
        if stage in ("clean", "epub") and stage != task.stage:
            task.stage = stage
            label = "清洗文本" if stage == "clean" else "打包 EPUB"
            self.pbar.configure(value=99 if stage == "clean" else 100)
            self.cur_label.configure(text=f"正在处理：{task.path.name}　{label}")

    # ---------------- 工具 ----------------
    def _task(self, iid: str) -> Task | None:
        return next((t for t in self.tasks if t.id == iid), None)

    def _append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        count = int(self.log.index("end-1c").split(".")[0])
        if count > MAX_LOG_LINES:
            self.log.delete("1.0", f"{count - MAX_LOG_LINES}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_close(self) -> None:
        if self.current and self.current.proc:
            if not messagebox.askokcancel(
                    "退出", "仍有任务在处理中，进度已落盘。\n确定退出？（重新打开后可续跑）"):
                return
            self.stop_all = True
            self.current.stop_requested = True
            try:
                self.current.proc.terminate()
            except Exception:
                pass
        self.destroy()


def run_gui() -> None:
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()

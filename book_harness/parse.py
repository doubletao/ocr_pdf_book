from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .util import (
    blocks_from_markdown,
    blocks_from_object,
    emit_progress,
    extract_blocks,
    get_pdf_page_count,
    load_json_any,
)


def resolve_device(choice: str) -> str:
    """auto：paddle 为 CUDA 编译版且有可用卡 → gpu，否则回落 cpu。

    CPU 版 wheel（is_compiled_with_cuda 为 False）即使插着 N 卡也只能 cpu；
    GPU 版 wheel 但无卡 / 探测异常同样回落 cpu（原因打到 stderr）。
    cpu/gpu 为人工强制。
    """
    choice = (choice or "auto").lower()
    if choice in ("cpu", "gpu"):
        if choice == "gpu":
            try:
                import paddle

                if not paddle.device.is_compiled_with_cuda():
                    raise RuntimeError(
                        "当前 paddlepaddle 是 CPU 版，无法使用 GPU；"
                        "请执行 run.cmd setup 安装 GPU 版，或改用 --device cpu"
                    )
            except ImportError:
                pass  # paddle 尚未安装，交给 create_pipeline 报缺依赖
        return choice
    # auto：每一步回落都打印原因，避免"静默变 cpu"没法排查
    try:
        import paddle
    except Exception as e:
        print(f"[device] auto -> cpu（import paddle 失败: {type(e).__name__}: {e}）",
              file=sys.stderr, flush=True)
        return "cpu"
    try:
        compiled_gpu = paddle.device.is_compiled_with_cuda()
    except Exception as e:
        print(f"[device] auto -> cpu（查询编译类型失败: {type(e).__name__}: {e}）",
              file=sys.stderr, flush=True)
        return "cpu"
    if not compiled_gpu:
        print("[device] auto -> cpu（当前环境是 paddle CPU 版；"
              "exe 需在 GPU 机上用 build_exe.bat 重打包，源码需在本机重跑 run.cmd setup）",
              file=sys.stderr, flush=True)
        return "cpu"
    try:
        n = int(paddle.device.cuda.device_count())
    except Exception as e:
        print(f"[device] auto -> cpu（CUDA 设备探测失败: {type(e).__name__}: {e}）",
              file=sys.stderr, flush=True)
        return "cpu"
    if n <= 0:
        print("[device] auto -> cpu（CUDA 编译版但看不到可用设备）",
              file=sys.stderr, flush=True)
        return "cpu"
    print(f"[device] auto -> gpu（CUDA 设备 {n} 个）", file=sys.stderr, flush=True)
    return "gpu"


def _patch_cuda_probe() -> None:
    """GPU 版 paddle 在无 NVIDIA 驱动的机器上，CUDA 查询会抛 OSError。

    paddlex 的 is_cuda_available() 只判断「编译带 CUDA」（上游留有
    TODO: Check runtime availability），于是 device=cpu 时模型构建
    （SigLIP 查 compute capability，Windows 上本就用不到）也崩。
    这里把能力探测包成「失败返回 None」，_siglip 据此禁用 SDPA 优化；
    有卡的机器探测成功，行为不变。须在产线模块首次导入前调用，
    并替换已 from-import 的旧引用。
    """
    try:
        from paddlex.utils import env

        if getattr(env, "_harness_safe_probe", False):
            return
        orig = env.get_gpu_compute_capability

        def safe():
            try:
                return orig()
            except Exception:
                return None

        env.get_gpu_compute_capability = safe
        env._harness_safe_probe = True
        import sys as _sys

        for mod in list(_sys.modules.values()):
            if mod is not None and getattr(
                mod, "get_gpu_compute_capability", None
            ) is orig:
                mod.get_gpu_compute_capability = safe
    except Exception:
        pass  # paddlex 未安装则无需 patch，后续报缺依赖


def _set_cpu_threads(cfg: dict) -> None:
    """限制 Paddle Inference 的 CPU 数学库线程数。

    paddlex 默认 10 线程（PADDLE_PDX_CPU_NUM_THREADS），在核数少于 10 的
    ARM 机（实测飞腾 D2000/8 核）上，内置 OpenBLAS 会在 sgemm 里段错误；
    不超过核数就正常。config 的 cpu_threads：auto=min(10, 核数)，或正整数。
    用户已在环境里设置 PADDLE_PDX_CPU_NUM_THREADS 时以环境为准。
    """
    if os.environ.get("PADDLE_PDX_CPU_NUM_THREADS"):
        n = os.environ["PADDLE_PDX_CPU_NUM_THREADS"]
    else:
        want = cfg.get("cpu_threads", "auto")
        if isinstance(want, int) and want >= 1:
            n = str(want)
        else:
            n = str(min(10, os.cpu_count() or 1))
        os.environ["PADDLE_PDX_CPU_NUM_THREADS"] = n
    print(f"[parse] cpu_threads={n}", file=sys.stderr)


def build_pipeline(cfg: dict):
    """优先 PaddleX（产线名带版本），退化到 paddleocr 顶层 PaddleOCRVL。

    device 由 config.json / --device 给出（auto|cpu|gpu），
    auto 在此探测实际可用设备后透传给产线。
    """
    requested = str(cfg.get("device", "auto")).lower()
    device = resolve_device(requested)
    print(f"[parse] device={device} (requested={requested})", file=sys.stderr)
    _patch_cuda_probe()
    _set_cpu_threads(cfg)
    errors = []
    try:
        from paddlex import create_pipeline

        try:
            pipe = create_pipeline(pipeline=cfg["pipeline"], device=device)
        except TypeError:  # 老版本 paddlex 不认 device
            pipe = create_pipeline(pipeline=cfg["pipeline"])
            errors.append("paddlex 不接受 device 参数，按其默认设备执行（建议升级 paddlex）")
        return "paddlex", pipe, errors
    except Exception as e:  # 未安装 / 产线名不存在
        errors.append(f"paddlex.create_pipeline: {type(e).__name__}: {e}")
    try:
        from paddleocr import PaddleOCRVL

        try:
            pipe = PaddleOCRVL(pipeline_version=cfg["pipeline_version"], device=device)
        except TypeError:  # 老版本 paddleocr 不认 device
            pipe = PaddleOCRVL(pipeline_version=cfg["pipeline_version"])
            errors.append("PaddleOCRVL 不接受 device 参数，按其默认设备执行（建议升级 paddleocr）")
        return "paddleocr", pipe, errors
    except Exception as e:
        errors.append(f"paddleocr.PaddleOCRVL: {type(e).__name__}: {e}")
    raise RuntimeError(
        "无法初始化 PaddleOCR-VL 产线。先执行 ./run.sh setup 安装依赖。\n"
        + "\n".join(errors)
    )


def _predict(pipeline, backend: str, pdf: str):
    try:
        return pipeline.predict(input=pdf)
    except TypeError:
        return pipeline.predict(pdf)


def _collect_saved_json(before: set[Path], page_dir: Path, raw_dir: Path) -> list[Path]:
    after = set(raw_dir.rglob("*.json"))
    new = sorted(after - before)
    return new or sorted(page_dir.rglob("*.json"))


def _load_history(work_dir: Path, pdf_path: Path) -> list[dict]:
    """读上次落盘的 pages.json；只有 source 是同一份 PDF 才算有效历史。"""
    src = work_dir / "pages.json"
    if not src.is_file():
        return []
    try:
        payload = load_json_any(src)
    except Exception as e:
        print(f"[parse] 忽略损坏的历史 {src}: {e}", file=sys.stderr)
        return []
    if str(pdf_path) != str(payload.get("source")):
        return []
    pages = payload.get("pages") or []
    return [p for p in pages if isinstance(p, dict) and p.get("page") and p.get("blocks")]


def _save_pages(work_dir: Path, payload: dict, pages: list[dict]) -> None:
    """逐页原子落盘：中途崩溃/关程序只丢最后一页，重启即可续跑。"""
    out = work_dir / "pages.json"
    tmp = work_dir / "pages.json.tmp"
    payload["pages"] = pages
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)


def _extract_page_blocks(res, page_dir: Path, raw_dir: Path, page_no: int) -> list[dict]:
    before = set(raw_dir.rglob("*.json"))  # 只认本次 save 新增的文件，避免读到别页结果
    try:
        res.save_to_json(save_path=str(page_dir))
    except Exception as e:
        print(f"[parse] p{page_no} save_to_json 失败: {e}", file=sys.stderr)
    try:
        res.save_to_markdown(save_path=str(page_dir))
    except Exception as e:
        print(f"[parse] p{page_no} save_to_markdown 失败: {e}", file=sys.stderr)

    for f in _collect_saved_json(before, page_dir, raw_dir):
        try:
            blocks = extract_blocks(load_json_any(f))
        except Exception as e:
            print(f"[parse] p{page_no} 解析 {f.name} 失败: {e}", file=sys.stderr)
            continue
        if blocks:
            return blocks
    blocks = blocks_from_object(res)
    if blocks:
        return blocks
    md_files = sorted(page_dir.rglob("*.md"))
    if md_files:
        return blocks_from_markdown(md_files[0].read_text(encoding="utf-8"))
    return []


def _render_pages(pdf_path: Path, page_nos: list[int], out_dir: Path, dpi: int) -> list[Path]:
    """把指定页渲染成 PNG（离线工具用；parse 主流程走 _render_one 逐页渲染）。"""
    import pypdfium2 as pdfium

    if out_dir.exists():
        for f in out_dir.glob("*.png"):
            f.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        paths = [_render_one(doc, n, out_dir, dpi) for n in page_nos]
    finally:
        doc.close()
    return paths


def _render_one(doc, page_no: int, out_dir: Path, dpi: int) -> Path:
    import pypdfium2  # noqa: F401  (doc 类型即 PdfDocument，仅作依赖自检)

    page = doc[page_no - 1]
    try:
        img = page.render(scale=dpi / 72.0).to_pil()
    finally:
        page.close()
    out = out_dir / f"page_{page_no:04d}.png"
    img.save(out)
    return out


def parse_pdf(pdf: str, cfg: dict, work_dir: Path,
              start_page: int = 1, end_page: int | None = None) -> dict:
    """逐页跑产线，把各版本输出归一成 harness 自己的 pages.json 中间格式。

    人文书几乎纯文字，因此不调用 restructure_pages()：跨页表格/标题重建由
    clean 阶段自己做。每处理完一页立即原子写盘，配合断点恢复可随时中断重跑。
    """
    pdf_path = Path(pdf).expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"找不到 PDF：{pdf_path}")

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = work_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    total = get_pdf_page_count(pdf_path)
    last = end_page or total or 0
    target = list(range(start_page, last + 1)) if last >= start_page else []
    if not target:
        raise RuntimeError(f"页范围无效：start={start_page} end={end_page} total={total}")

    history = _load_history(work_dir, pdf_path)
    hist_by_page = {int(p["page"]): p for p in history}
    covered: list[int] = []
    for n in target:  # 只认目标范围开头的连续历史，中间的洞一律重跑，避免页序错乱
        if n in hist_by_page:
            covered.append(n)
        else:
            break
    need = [n for n in target if n not in set(covered)]
    print(f"[parse] 目标 {start_page}-{last}，历史覆盖 {len(covered)} 页，待处理 {len(need)} 页",
          file=sys.stderr)
    emit_progress(stage="parse", page=len(covered), total=total, resumed=bool(covered))

    payload = {
        "source": str(pdf_path),
        "backend": "",
        "pipeline": cfg["pipeline"],
        "notes": [],
        "total_pages": total,
        "pages": [],
    }
    pages: list[dict] = [{"page": n, "blocks": hist_by_page[n]["blocks"]} for n in covered]
    if need:
        backend, pipeline, notes = build_pipeline(cfg)
        print(f"[parse] backend={backend} pipeline={cfg['pipeline']}", file=sys.stderr)
        payload["backend"], payload["notes"] = backend, notes

        def handle(res, page_no: int) -> None:
            page_dir = raw_dir / f"page_{page_no:04d}"
            page_dir.mkdir(parents=True, exist_ok=True)
            blocks = _extract_page_blocks(res, page_dir, raw_dir, page_no)
            pages.append({"page": page_no, "blocks": blocks})
            pages.sort(key=lambda x: x["page"])
            _save_pages(work_dir, payload, pages)
            emit_progress(stage="parse", page=page_no, total=total)
            print(f"[parse] p{page_no}: {len(blocks)} blocks", file=sys.stderr)

        # 实测（paddlex 3.7.2）：predict() 对 PDF 和多图目录都是一次性把整批
        # 推理完才开始 yield，中途 kill 一页都留不下，进度条也会最后才动。
        # 而复用同一 pipeline 对象逐页 predict 没有额外初始化开销
        #（单页 304s vs 批量 606s/2页，等价），所以全程固定为
        # 「渲染这一页 → predict 这一张图 → 落盘」，进度、断点、跳过已处理
        # 三件事同时成立，probe 传 --pages 1-3 也才真的只跑前 3 页。
        import pypdfium2 as pdfium

        dpi = int(cfg.get("render_dpi", 200))
        render_dir = work_dir / "rendered"
        render_dir.mkdir(parents=True, exist_ok=True)
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            for page_no in need:
                png = _render_one(doc, page_no, render_dir, dpi)
                try:
                    res = next(iter(_predict(pipeline, backend, str(png))))
                except StopIteration:
                    print(f"[parse] p{page_no} 产线无输出，按空页处理", file=sys.stderr)
                    res = None
                if res is not None:
                    handle(res, page_no)
                else:
                    pages.append({"page": page_no, "blocks": []})
                    _save_pages(work_dir, payload, pages)
                    emit_progress(stage="parse", page=page_no, total=total)
                png.unlink(missing_ok=True)
        finally:
            doc.close()

    _save_pages(work_dir, payload, pages)
    if not pages:
        raise RuntimeError("产线没有返回任何页；检查 PDF 是否可渲染、显存是否足够。")
    emit_progress(stage="parse", page=len(pages), total=total, done=True)
    return {"pages_json": str(work_dir / "pages.json"), "pages": len(pages),
            "blocks": sum(len(p["blocks"]) for p in pages)}

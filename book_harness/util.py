from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

BLOCK_LIST_KEYS = ("parsing_res_list", "blocks", "result", "res", "parsing_result")

# 进度事件协议：stderr 上带此前缀的一行 JSON，stdout 仍只保留最终一行报告
PROGRESS_PREFIX = "[progress] "


def emit_progress(**kw: Any) -> None:
    print(PROGRESS_PREFIX + json.dumps(kw, ensure_ascii=False), file=sys.stderr, flush=True)


def parse_progress(line: str) -> dict | None:
    if not line.startswith(PROGRESS_PREFIX):
        return None
    try:
        return json.loads(line[len(PROGRESS_PREFIX):])
    except Exception:
        return None


def get_pdf_page_count(path: str | Path) -> int | None:
    """读 PDF 总页数；pypdfium2 是 paddleocr 的既有依赖，失败返回 None 不阻断流程。"""
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(path))
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return None


def load_json_any(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_dicts(obj: Any) -> Iterable[dict]:
    """递归遍历任意嵌套结构中的所有 dict。"""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from iter_dicts(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from iter_dicts(v)


def _first(d: dict, keys: tuple[str, ...], default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def coerce_block(d: dict, idx: int) -> dict | None:
    """把 PaddleOCR-VL / PaddleX / PaddleOCR 各种版本的块字段归一成统一格式。"""
    label = _first(d, ("block_label", "label", "type", "category"), "text")
    content = _first(d, ("block_content", "content", "text", "markdown"), "")
    if isinstance(content, (list, dict)):
        content = json.dumps(content, ensure_ascii=False)
    content = str(content).strip()
    if not content:
        return None
    order = _first(d, ("block_order", "order", "index"), idx)
    return {"label": str(label).strip(), "content": content, "order": order}


def extract_blocks(obj: Any) -> list[dict]:
    """从任意形状的产线 JSON 中抽出按阅读顺序排列的块列表。

    官方 PaddleOCR-VL 结果的核心字段是 parsing_res_list[].block_label /
    block_content / block_order；这里额外兼容其他版本名，避免换版本就崩。
    """
    for d in iter_dicts(obj):
        for k in BLOCK_LIST_KEYS:
            v = d.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                out = []
                for i, b in enumerate(v):
                    cb = coerce_block(b, i)
                    if cb:
                        out.append(cb)
                if out:
                    return _dedupe(out)

    out, seen = [], set()
    for d in iter_dicts(obj):
        if any(k in d for k in ("block_content", "block_label")):
            cb = coerce_block(d, len(out))
            if cb and cb["content"] not in seen:
                seen.add(cb["content"])
                out.append(cb)
    return out


def _dedupe(blocks: list[dict]) -> list[dict]:
    out, seen = [], set()
    for b in blocks:
        if b["content"] not in seen:
            seen.add(b["content"])
            out.append(b)
    return out


def blocks_from_object(res: Any) -> list[dict]:
    """save_to_json 没落盘时的兜底：直接扒结果对象上的 dict 属性。"""
    for attr in ("json", "_json", "result", "_result", "res"):
        try:
            v = getattr(res, attr)
            if isinstance(v, (dict, list)):
                blocks = extract_blocks(v)
                if blocks:
                    return blocks
        except Exception:
            pass
    try:
        return extract_blocks(vars(res))
    except Exception:
        return []


def blocks_from_markdown(md: str) -> list[dict]:
    """最后兜底：JSON 结构完全变了时，从逐页 Markdown 按空行切块。"""
    blocks: list[dict] = []
    for chunk in re.split(r"\n\s*\n", md.strip()):
        c = chunk.strip()
        if not c:
            continue
        label = "text"
        if c.startswith("# "):
            label = "doc_title"
        elif c.startswith("## "):
            label = "paragraph_title"
        elif c.lstrip().startswith("|"):
            label = "table"
        blocks.append({"label": label, "content": c, "order": len(blocks)})
    return blocks


def parse_page_range(spec: str | None) -> tuple[int, int | None]:
    """'1-3' -> (1, 3)；'7' -> (1, 7)；None -> (1, None) 表示全本。"""
    if not spec:
        return 1, None
    spec = spec.strip()
    if "-" in spec:
        a, b = spec.split("-", 1)
        return int(a or 1), int(b)
    return 1, int(spec)

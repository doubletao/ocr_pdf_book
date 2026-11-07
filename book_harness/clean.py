from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

from .util import emit_progress, load_json_any

# 独立成块的纯数字 / 罗马数字 / 中文数字，几乎必然是页码
PAGE_NUMBER_RE = re.compile(
    r"^[\s\-—–·•\[\(]*(\d{1,4}|[IVXLCDM]{1,7}|[〇零一二三四五六七八九十百]{1,5})[\s\-—–·•\]\)]*$"
)
# 行尾属于「句子结束」，不应与下一行硬拼接
END_PUNCT = set("。！？；：…”’』」）】》.!?;:)\"']}")
CHAPTER_RE_DEFAULT = r"^\s*(第[一二三四五六七八九十百千零〇0-9]+[章节篇卷回部]|前言|序|附录)"


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _strip_heading(s: str) -> str:
    return re.sub(r"^#{1,6}\s*", "", s).strip()


def _is_table(label: str, content: str) -> bool:
    l = label.lower()
    if any(k in l for k in ("table", "表格")):
        return True
    return content.lstrip().startswith("|") and content.count("|") >= 3


def join_hard_wraps(content: str) -> str:
    """块内硬换行拼接：扫描版常把一个自然段按视觉行切断。

    只在同一 block 内部处理，绝不跨 block 合并——版面分析的 block 通常已是
    段落级，跨块拼接容易把两段黏成一段。
    """
    lines = [x.strip() for x in content.splitlines() if x.strip()]
    if len(lines) <= 1:
        return content.strip()
    out = lines[0]
    for nxt in lines[1:]:
        if not out:
            out = nxt
            continue
        last = out[-1]
        if last in END_PUNCT or re.match(r"^#{1,6}\s|^\s*[-*]\s|^\|", nxt):
            out += "\n" + nxt
        elif re.search(r"[a-zA-Z0-9]$", last) and re.match(r"^[a-zA-Z0-9]", nxt):
            out += " " + nxt
        else:
            out += nxt
    return out


def _detect_repeated_headers(pages: list[dict], cfg: dict) -> set[str]:
    """自动识别跨页重复出现的页眉（书名 / 章名），无需硬编码标签名。"""
    n_pages = len(pages)
    if n_pages < int(cfg.get("min_pages_for_auto_detect", 5)):
        return set()
    max_chars = int(cfg.get("max_header_chars", 40))
    counter: Counter = Counter()
    for p in pages:
        seen = set()
        for b in p["blocks"]:
            n = _norm(b["content"])
            if 2 <= len(n) <= max_chars:
                seen.add(n)
        for n in seen:
            counter[n] += 1
    threshold = max(2, math.ceil(n_pages * float(cfg.get("repeated_header_ratio", 0.6))))
    return {n for n, c in counter.items() if c >= threshold}


def clean_pages(payload: dict, cfg: dict) -> dict:
    pages = payload.get("pages", [])
    drop_labels = {x.lower() for x in cfg.get("drop_labels", [])}
    drop_tables = bool(cfg.get("drop_tables", True))
    drop_numbers = bool(cfg.get("drop_standalone_numbers", True))
    merge_lines = bool(cfg.get("merge_lines_within_block", True))
    title_level = {k.lower(): int(v) for k, v in cfg.get("title_level", {}).items()}
    chapter_re = re.compile(cfg.get("chapter_pattern", CHAPTER_RE_DEFAULT))
    repeated = _detect_repeated_headers(pages, cfg)

    lines: list[str] = []
    stats = {
        "pages": len(pages),
        "blocks_in": 0,
        "blocks_out": 0,
        "dropped_label": 0,
        "dropped_page_number": 0,
        "dropped_repeated": 0,
        "dropped_table": 0,
        "headings": 0,
        "repeated_patterns": sorted(repeated),
    }

    for p in pages:
        page_lines: list[str] = []
        for b in p.get("blocks", []):
            stats["blocks_in"] += 1
            label = str(b.get("label", "text")).lower()
            content = str(b.get("content", "")).strip()
            if not content:
                continue
            norm = _norm(content)

            if label in drop_labels:
                stats["dropped_label"] += 1
                continue
            if drop_numbers and PAGE_NUMBER_RE.match(content) and len(norm) <= 12:
                stats["dropped_page_number"] += 1
                continue
            if norm in repeated:
                stats["dropped_repeated"] += 1
                continue
            if drop_tables and _is_table(label, content):
                stats["dropped_table"] += 1
                continue

            plain = _strip_heading(content)
            level = title_level.get(label)
            if not level and len(plain) <= 40 and chapter_re.match(plain):
                level = 1

            if merge_lines:
                plain = join_hard_wraps(plain)
            plain = plain.strip()
            if not plain:
                continue

            if level:
                page_lines.append("#" * min(level, 6) + " " + plain.replace("\n", " "))
                stats["headings"] += 1
            else:
                page_lines.append(plain)
            stats["blocks_out"] += 1

        if page_lines:
            lines.append("\n\n".join(page_lines))

    md = re.sub(r"\n{3,}", "\n\n", "\n\n".join(lines)).strip() + "\n"
    stats["chars"] = len(md)
    return {"markdown": md, "stats": stats}


def run_clean(work_dir: Path, dist_dir: Path, cfg: dict) -> dict:
    src = Path(work_dir) / "pages.json"
    if not src.is_file():
        raise FileNotFoundError(f"缺少 {src}，先执行 ./run.sh parse --pdf ...")
    dist_dir = Path(dist_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)

    payload = load_json_any(src)
    emit_progress(stage="clean")
    result = clean_pages(payload, cfg)
    md_path = dist_dir / "阅读版.md"
    md_path.write_text(result["markdown"], encoding="utf-8")

    report = {"ok": True, "stage": "clean", **result["stats"],
              "outputs": {"reading_md": str(md_path)}}
    (dist_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[clean] {md_path}  {result['stats']['chars']} 字", file=sys.stderr)
    return report

from __future__ import annotations

import json
from pathlib import Path

from .clean import clean_pages, run_clean
from .export import build_epub, verify_epub


def fake_pages(n: int = 6) -> dict:
    pages = []
    for i in range(1, n + 1):
        blocks = [
            {"label": "header", "content": "人文丛书·第三辑", "order": 0},
            # 故意标成 text：验证「标签没给页码时，纯数字规则也能兜住」
            {"label": "text", "content": str(11 + i), "order": 99},
        ]
        if i == 1:
            blocks += [
                {"label": "doc_title", "content": "中国近世思想史讲义", "order": 1},
                {"label": "text", "content": "第一章 绪论", "order": 2},
                {"label": "paragraph_title", "content": "一、材料与方法", "order": 3},
                {"label": "table", "content": "| 年份 | 事件 |\n| --- | --- |\n| 1900 | 某事 |", "order": 4},
                {"label": "footnote", "content": "① 参见某书卷三，页十二。", "order": 5},
            ]
        blocks.append({
            "label": "text",
            "content": f"这段文字被扫描\n硬换行切断了，第{i}页仍应读作一个自然段。",
            "order": 10,
        })
        pages.append({"page": i, "blocks": blocks})
    return {"source": "fake.pdf", "backend": "selftest", "pipeline": "fake", "pages": pages}


def run_selftest(root: Path, cfg: dict) -> dict:
    work = root / "work"
    dist = root / "dist"
    work.mkdir(exist_ok=True)
    dist.mkdir(exist_ok=True)

    (work / "pages.json").write_text(
        json.dumps(fake_pages(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    cleaned = clean_pages(fake_pages(), cfg)
    md = cleaned["markdown"]
    st = cleaned["stats"]

    checks = {
        "repeated_header_detected": "人文丛书·第三辑" in st["repeated_patterns"],
        "header_removed": "人文丛书" not in md,
        "page_numbers_removed": all(str(11 + i) not in md for i in range(1, 7)),
        "page_number_count": st["dropped_page_number"] == 6,
        "tables_dropped": st["dropped_table"] == 1 and "1900" not in md,
        "footnote_kept": "参见某书卷三" in md,
        "chapter_heading": "# 第一章 绪论" in md,
        "title_heading": "# 中国近世思想史讲义" in md,
        "sub_heading": "## 一、材料与方法" in md,
        "hard_wrap_joined": "被扫描硬换行切断了" in md,
        "headings_count": st["headings"] >= 3,
    }

    report = run_clean(work, dist, cfg)
    md_path = dist / "阅读版.md"
    epub_path = dist / "selftest.epub"
    build_epub(md_path.read_text(encoding="utf-8"), epub_path, "自测用书")
    checks["epub_valid"] = verify_epub(epub_path)
    checks["report_ok"] = report.get("ok") is True and report["chars"] > 0

    failed = [k for k, v in checks.items() if not v]
    return {
        "ok": not failed,
        "stage": "selftest",
        "checks": checks,
        "failed": failed,
        "stats": st,
        "outputs": {"reading_md": str(md_path), "epub": str(epub_path)},
        "note": "selftest 使用合成数据，验证的是除模型推理外的全链路；模型侧需本地 ./run.sh probe 验证。",
    }

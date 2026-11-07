from __future__ import annotations

import html
import re
import sys
import zipfile
from pathlib import Path

EPUB_MIMETYPE = "application/epub+zip"

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


def _split_sections(md_text: str) -> list[tuple[str, str]]:
    """按一级/二级标题把 Markdown 切成若干篇章，返回 (标题, 正文)。"""
    sections: list[tuple[str, list[str]]] = []
    title, buf = "正文", []
    for line in md_text.splitlines():
        m = re.match(r"^(#{1,2})\s+(.+)$", line)
        if m and any(x.strip() for x in buf):
            sections.append((title, buf))
            title, buf = m.group(2).strip(), [line]
        elif m:
            title, buf = m.group(2).strip(), [line]
        else:
            buf.append(line)
    if buf:
        sections.append((title, buf))
    if not sections:
        sections = [("正文", [""])]
    return [(t, "\n".join(b)) for t, b in sections]


def _md_to_xhtml(md: str) -> str:
    out = []
    for line in md.splitlines():
        s = line.rstrip()
        if not s:
            continue
        m = re.match(r"^(#{1,6})\s+(.+)$", s)
        if m:
            lvl = min(len(m.group(1)), 6)
            out.append(f"<h{lvl}>{html.escape(m.group(2).strip())}</h{lvl}>")
        elif re.match(r"^\s*[-*]\s+", s):
            bullet = re.sub(r"^\s*[-*]\s+", "", s)
            out.append(f"<p>{html.escape(bullet)}</p>")
        else:
            out.append(f"<p>{html.escape(s)}</p>")
    return "\n".join(out) or "<p></p>"


def _xhtml_doc(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN">\n'
        f"<head><title>{html.escape(title)}</title>"
        '<meta charset="utf-8"/>'
        "<style>body{font-family:serif;line-height:1.75;margin:1em;}"
        "h1,h2{margin:1.2em 0 .6em;}p{margin:0;text-indent:2em;}</style></head>\n"
        f"<body>\n{body}\n</body></html>"
    )


def build_epub(md_text: str, out_path: Path, book_title: str) -> str:
    """零依赖生成最小 EPUB2（mimetype / container / opf / ncx / xhtml）。

    不依赖 pandoc，因此 selftest 在没有 GPU、没有外部工具的机器上也能验证。
    想用 pandoc 得到更成熟的排版可自行替换本函数。
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sections = _split_sections(md_text)

    manifest, spine, navpoints = [], [], []
    files: dict[str, str] = {}
    for i, (title, body) in enumerate(sections, start=1):
        name = f"chap_{i:03d}.xhtml"
        files[f"OEBPS/{name}"] = _xhtml_doc(title, _md_to_xhtml(body))
        manifest.append(f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')
        navpoints.append(
            f'<navPoint id="n{i}" playOrder="{i}">'
            f"<navLabel><text>{html.escape(title)}</text></navLabel>"
            f'<content src="{name}"/></navPoint>'
        )

    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{html.escape(book_title)}</dc:title>"
        '<dc:language>zh-CN</dc:language>'
        '<dc:identifier id="id">urn:uuid:ocr-book-harness-0001</dc:identifier>'
        "</metadata>\n"
        "<manifest>"
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        + "".join(manifest)
        + "</manifest>\n"
        '<spine toc="ncx">' + "".join(spine) + "</spine>\n</package>"
    )
    ncx = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "<head><meta name=\"dtb:uid\" content=\"urn:uuid:ocr-book-harness-0001\"/></head>\n"
        f"<docTitle><text>{html.escape(book_title)}</text></docTitle>\n"
        "<navMap>" + "".join(navpoints) + "</navMap>\n</ncx>"
    )

    with zipfile.ZipFile(out_path, "w") as z:
        # EPUB 规范：mimetype 必须是第一个条目且不压缩
        z.writestr("mimetype", EPUB_MIMETYPE, compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER_XML)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        for name, content in files.items():
            z.writestr(name, content)
    return str(out_path)


def verify_epub(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            return (
                names and names[0] == "mimetype"
                and z.read("mimetype").decode() == EPUB_MIMETYPE
                and "META-INF/container.xml" in names
                and any(n.endswith(".opf") for n in names)
            )
    except Exception as e:
        print(f"[epub] 校验失败: {e}", file=sys.stderr)
        return False

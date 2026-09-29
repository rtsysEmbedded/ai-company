"""Structure-aware chunking: Markdown by heading, code by function/class, PDF by page."""
import os
import re
from dataclasses import dataclass
from typing import List

from .config import CHUNK_MAX, CHUNK_MIN, CHUNK_OVERLAP

CODE_LANGS = {
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".swift": "swift", ".sh": "bash",
}
TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".yaml", ".yml", ".json", ".toml", ".ini", ".csv"}
# Top-level node types treated as one unit each.
CODE_UNITS = {
    "function_definition", "class_definition", "decorated_definition", "struct_specifier",
    "enum_specifier", "function_declaration", "method_definition", "class_declaration",
    "function_item", "impl_item", "struct_item", "method_declaration", "type_declaration",
    "export_statement", "lexical_declaration", "declaration", "preproc_function_def",
}


@dataclass
class Chunk:
    section: str  # heading path, function name, or "page N"
    text: str
    kind: str     # doc | code | pdf
    lang: str


def _split_long(text: str) -> List[str]:
    """Split text longer than CHUNK_MAX on paragraph/line boundaries with overlap."""
    if len(text) <= CHUNK_MAX:
        return [text]
    parts, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_MAX, len(text))
        if end < len(text):
            cut = max(text.rfind("\n\n", start, end), text.rfind("\n", start, end))
            if cut > start + CHUNK_MAX // 2:
                end = cut
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [p for p in parts if p]


def _merge_small(chunks: List[Chunk]) -> List[Chunk]:
    """Merge tiny neighbouring chunks so each carries enough context."""
    out: List[Chunk] = []
    for c in chunks:
        if out and len(out[-1].text) < CHUNK_MIN and len(out[-1].text) + len(c.text) <= CHUNK_MAX:
            prev = out[-1]
            out[-1] = Chunk(prev.section, prev.text + "\n\n" + c.text, prev.kind, prev.lang)
        else:
            out.append(c)
    return out


def chunk_markdown(text: str) -> List[Chunk]:
    heads: List[str] = []
    sections, buf, cur = [], [], ""
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            if buf:
                sections.append((cur, "\n".join(buf).strip()))
            level = len(m.group(1))
            heads = heads[: level - 1] + [m.group(2).strip()]
            cur, buf = " > ".join(heads), [line]
        else:
            buf.append(line)
    if buf:
        sections.append((cur, "\n".join(buf).strip()))
    chunks = [Chunk(s or "(top)", p, "doc", "markdown")
              for s, body in sections if body for p in _split_long(body)]
    return _merge_small(chunks)


def chunk_code(text: str, lang: str) -> List[Chunk]:
    try:
        from tree_sitter_language_pack import get_parser
        tree = get_parser(lang).parse(text.encode())
    except Exception:
        return [Chunk("(file)", p, "code", lang) for p in _split_long(text)]
    src = text.encode()
    chunks, pending = [], []
    for node in tree.root_node.children:
        body = src[node.start_byte:node.end_byte].decode(errors="replace")
        if node.type in CODE_UNITS:
            name_node = node.child_by_field_name("name") or node.child_by_field_name("declarator")
            name = src[name_node.start_byte:name_node.end_byte].decode(errors="replace") if name_node else node.type
            name = name.split("(")[0].strip()[:120]
            # Leading comments / includes stay attached to the next unit.
            unit = "\n".join(pending + [body]); pending = []
            chunks += [Chunk(name, p, "code", lang) for p in _split_long(unit)]
        else:
            pending.append(body)
    if pending:
        chunks += [Chunk("(file)", p, "code", lang) for p in _split_long("\n".join(pending))]
    return _merge_small(chunks)


def chunk_pdf(path: str) -> List[Chunk]:
    import pymupdf
    chunks = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, 1):
            text = page.get_text().strip()
            chunks += [Chunk(f"page {i}", p, "pdf", "pdf") for p in _split_long(text)] if text else []
    return _merge_small(chunks)


def supported(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in CODE_LANGS or ext in TEXT_EXT or ext == ".pdf"


def chunk_file(path: str) -> List[Chunk]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return chunk_pdf(path)
    text = open(path, encoding="utf-8", errors="replace").read()
    if ext in CODE_LANGS:
        return chunk_code(text, CODE_LANGS[ext])
    if ext in (".md", ".markdown"):
        return chunk_markdown(text)
    return _merge_small([Chunk("(file)", p, "doc", ext.lstrip(".")) for p in _split_long(text)])

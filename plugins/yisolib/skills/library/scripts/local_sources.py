"""Inspect user-selected local files and look up existing Library records. Never writes Library."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from contextlib import closing, contextmanager
from unicodedata import normalize
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

SKILL = Path(__file__).resolve().parents[1]
LIBRARY = SKILL.parents[2] / "Research-Info-Hub" / "Library"
DATABASE = LIBRARY / "Company-Profile-local.sqlite"


def safe_source(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        # Cloud placeholders are local files. Name-surrogate tags redirect traversal.
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) & 0x20000000:
            raise ValueError(f"跳过符号链接或目录联接: {part}")
    return path.resolve(strict=True)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


@contextmanager
def open_source(path):
    path = safe_source(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("需要普通本地文件")
    with path.open("rb") as src:
        if identity(os.fstat(src.fileno())) != identity(before):
            raise ValueError("打开时文件已更改")
        yield src, before.st_size
        if identity(os.fstat(src.fileno())) != identity(before):
            raise ValueError("读取时文件已更改")
    if identity(safe_source(path).stat()) != identity(before):
        raise ValueError("读取后文件已更改")


def read_source(path):
    with open_source(path) as (stream, size):
        digest = hashlib.file_digest(stream, "sha256")
    return digest.hexdigest(), size


def read_bytes(path, limit, expected_hash=None):
    with open_source(path) as (stream, size):
        if size > limit:
            raise ValueError(f"源文件超过本地读取限制 ({limit} 字节): {path}")
        raw = stream.read(size + 1)
        if len(raw) != size:
            raise ValueError(f"读取时文件长度已更改: {path}")
    if expected_hash is not None and hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError(f"source changed since review: {path}")
    return raw


def read_db():
    db = sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


SOURCE_JOIN = """
SELECT s.id AS source_id, s.namespace, s.record_key, s.content_hash, s.processing_status,
       m.id AS memo_id, m.title, m.lifecycle
FROM cp_source_record AS s
LEFT JOIN cp_memo_source AS ms ON ms.source_id=s.id
LEFT JOIN cp_memo AS m ON m.id=ms.memo_id
"""


def source_matches(db, path, digest):
    same_bytes = db.execute(SOURCE_JOIN + " WHERE s.content_hash=? ORDER BY s.id,m.id", (digest,))
    changed = db.execute(
        SOURCE_JOIN + """ WHERE (s.private_locator=? COLLATE NOCASE
                        OR (s.namespace='library-local-file' AND s.record_key=? COLLATE NOCASE))
                        AND s.content_hash IS NOT ? ORDER BY s.id,m.id""",
        (str(path), path.as_uri(), digest),
    )
    return [dict(r) for r in same_bytes], [dict(r) for r in changed]


def scan(paths, db):
    files, issues = [], []
    seen = set()
    first_path = {}
    pending = [(Path(raw), True) for raw in reversed(paths)]
    while pending:
        raw, explicit = pending.pop()
        try:
            path = safe_source(raw)
            if path in seen:
                continue
            if not explicit and path == LIBRARY:
                issues.append({"path": str(path), "reason": "跳过嵌套在材料目录中的目标 Library"})
                continue
            seen.add(path)
            if path.is_dir():
                children = sorted(path.iterdir(), key=lambda p: (p.name.casefold(), p.name))
                pending.extend((child, False) for child in reversed(children))
                continue
            digest, size = read_source(path)
            existing, changed = source_matches(db, path, digest)
            files.append({"path": str(path), "size_bytes": size, "sha256": digest,
                          "same_input_bytes_as": first_path.get(digest),
                          "existing_same_bytes": existing, "previous_at_path": changed})
            first_path.setdefault(digest, str(path))
        except (OSError, ValueError, RuntimeError) as error:
            issues.append({"path": str(raw), "reason": str(error)})
    return {"files": files, "issues": issues}


def folded(value):
    return normalize("NFKC", value).casefold().strip()


def subjects(db, query, kind, limit, *, children=False):
    needle = folded(query)
    if not needle or limit < 1:
        raise ValueError("请指定非空主体检索词和正数 limit")
    if children:
        if kind == "company":
            raise ValueError("--children 仅适用于主题")
        kind = "theme"
    results = []
    for tag in ("company", "theme"):
        if kind and kind != tag:
            continue
        scope = "s.scope" if tag == "theme" else "NULL"
        theme_type = "s.theme_type" if children else "NULL"
        rows = db.execute(f"""SELECT s.id,s.name,{scope} AS scope,{theme_type} AS theme_type,a.alias FROM cp_{tag} s
                              LEFT JOIN cp_{tag}_alias a ON a.{tag}_id=s.id
                              WHERE s.status='active' ORDER BY s.id,a.alias""")
        matches = {}
        for row in rows:
            names = [folded(x) for x in (row["name"], row["id"], row["alias"]) if x]
            if not any(needle in value for value in names):
                continue
            subject = matches.setdefault(row["id"], {"id": row["id"], "kind": tag,
                                         "name": row["name"], "scope": row["scope"],
                                         "matching_aliases": [], "exact_match": False})
            if children:
                subject["theme_type"] = row["theme_type"]
            if row["alias"] and needle in folded(row["alias"]):
                subject["matching_aliases"].append(row["alias"])
            if needle in names:
                subject["exact_match"] = True
        results.extend(matches.values())
    results.sort(key=lambda row: (not row["exact_match"], row["name"], row["id"]))
    selected = results[:limit]
    if children:
        for subject in selected:
            rows = db.execute("""
                SELECT t.id,t.name,t.scope,t.theme_type
                FROM cp_theme_parent h JOIN cp_theme t ON t.id=h.child_id
                WHERE h.parent_id=? AND t.status='active'
                ORDER BY t.name,t.id
            """, (subject["id"],))
            subject["children"] = [dict(row) for row in rows]
    return {"total": len(results), "subjects": selected}


def industry_catalog(db):
    """Index the actual SW2021 nodes and paths, including historical third-level codes."""
    rows = db.execute("""
        SELECT n.id, n.code, n.name, n.level, p.code
        FROM cp_industry_node n JOIN cp_taxonomy_version v ON v.id=n.taxonomy_id
        LEFT JOIN cp_industry_node p ON p.id=n.parent_id AND p.taxonomy_id=n.taxonomy_id
        WHERE v.scheme='SW' AND v.version='SW2021'
        ORDER BY n.level, n.code
    """)
    nodes = {}
    for node_id, code, name, level, parent in rows:
        path = nodes[parent]["path"] if parent in nodes else []
        if len(path) != level - 1:
            raise ValueError(f"invalid SW2021 parent path for {code}")
        nodes[code] = {"id": node_id, "code": code, "level": level, "path": [*path, {"name": name, "code": code}]}
    return nodes


def industries(db, query):
    """Offer levels one and two. A match on a third-level name also finds its parent."""
    needle = folded(query)
    matches = {}
    for node in industry_catalog(db).values():
        path = node["path"]
        if needle and not any(needle in folded(value) for part in path for value in part.values()):
            continue
        path = path[:2]
        code = path[-1]["code"]
        matches[code] = {"code": code, "level": len(path), "path": [part["name"] for part in path]}
    return {"scheme": "SW2021", "total": len(matches), "industries": [matches[code] for code in sorted(matches)]}


def word_content(stream):
    sys.dont_write_bytecode = True
    runtime = str(SKILL / "production" / "runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    from image_assets import _BoundedXMLTree

    def xml(package, name):
        if package.getinfo(name).file_size > 16 * 1024 * 1024:
            raise ValueError(f"Word XML 超过本地读取限制: {name}")
        return ET.fromstring(package.read(name), parser=ET.XMLParser(target=_BoundedXMLTree()))

    with ZipFile(stream) as package:
        names = package.namelist()
        if len(names) > 10000 or len(names) != len(set(names)):
            raise ValueError("Word ZIP 包含过多或同名部件")
        doc = xml(package, "word/document.xml")
        word = doc.tag.rpartition("}")[0] + "}"
        body = doc.find(word + "body")
        if body is None:
            raise ValueError("Word 文件缺少正文")
        def text(node):
            # Preserve the pinned extractor's visible text, including math and DrawingML text runs.
            return "".join(run.text or "" for run in node.iter() if run.tag.endswith("}t"))

        chunks, tables = [], []
        for block in body:
            if block.tag == word + "p":
                chunks.append(text(block))
            elif block.tag == word + "tbl":
                rows = [[text(cell) for cell in row.findall(word + "tc")] for row in block.findall(word + "tr")]
                tables.append(rows)
                chunks.append("\n".join("\t".join(row) for row in rows))

        extra = ("word/media/", "word/charts/", "word/diagrams/", "word/embeddings/", "word/header",
                 "word/footer", "word/footnotes", "word/endnotes", "word/comments", "word/afchunk")
        parts = sorted(n for n in names if n.lower().startswith(extra) and not n.endswith("/"))
        if sum(package.getinfo(name).file_size for name in parts) > 128 * 1024 * 1024:
            raise ValueError("Word 附件超出本地解压读取限制，请分开审阅")
        fingerprints, part_texts = {}, {}
        for name in parts:
            with package.open(name) as stream:
                fingerprints[name] = hashlib.file_digest(stream, "sha256").hexdigest()
            if (name.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes", "word/comments"))
                    and name.endswith(".xml")):
                paragraphs = [text(p) for p in xml(package, name).iter(word + "p")]
                visible = "\n".join(p for p in paragraphs if p.strip())
                if visible:
                    part_texts[name] = visible

        # Editing dates and application metadata change on an ordinary save. Only the title matters here.
        if "docProps/core.xml" in names:
            title = xml(package, "docProps/core.xml").findtext("{http://purl.org/dc/elements/1.1/}title")
            if title and title.strip():
                key = "docProps/core.xml#title"
                part_texts[key] = title
                fingerprints[key] = hashlib.sha256(title.encode()).hexdigest()

        # Detect a changed image target even when the two embedded media files are both retained.
        rels = "word/_rels/document.xml.rels"
        if rels in names:
            links = sorted((rel.get("Id"), rel.get("Type"), rel.get("Target"), rel.get("TargetMode", "Internal"))
                           for rel in xml(package, rels)
                           if rel.get("Type", "").rsplit("/", 1)[-1] in {
                               "image", "chart", "diagramData", "oleObject", "aFChunk", "hyperlink",
                               "header", "footer", "footnotes", "endnotes", "comments"})
            if links:
                fingerprints[rels] = hashlib.sha256(json.dumps(links, ensure_ascii=False).encode()).hexdigest()

        # Visible text, cells, review markup and embedded drawings have separate change signals.
        special = {"drawing", "pict", "del", "ins", "moveFrom", "moveTo", "txbxContent", "altChunk",
                   "object", "sdt", "fldSimple", "instrText"}
        inline = hashlib.sha256()
        found = False
        for node in body.iter():
            if node.tag.startswith(word) and node.tag[len(word):] in special:
                inline.update(ET.tostring(node, encoding="utf-8"))
                found = True
        if found:
            fingerprints["word/document.xml#review-or-embedded-content"] = inline.hexdigest()

    return {"body": "\n".join(chunks), "native_tables": tables, "supplemental_parts": parts,
            "supplemental_hashes": fingerprints, "supplemental_texts": part_texts,
            "limitations": ["Word 附件、图片与修订标记按原文另行审阅"]}


def extract(raw):
    path = safe_source(raw)
    suffix = path.suffix.lower()
    if suffix == ".doc":
        from legacy_word_text import extract_doc
        return extract_doc(path)
    if suffix not in {".txt", ".md", ".docx", ".docm"}:
        raise ValueError("该格式请用相应的本地阅读工具核验正文、原生表格和图像")
    if suffix in {".docx", ".docm"}:
        try:
            with open_source(path) as (stream, _):
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                stream.seek(0)
                result = word_content(stream)
        except (BadZipFile, KeyError, ET.ParseError, TypeError) as error:
            raise ValueError(f"无法按 Word 正文结构读取: {error}") from error
    else:
        content = read_bytes(path, 32 * 1024 * 1024)
        digest = hashlib.sha256(content).hexdigest()
        result = {"body": content.decode("utf-8-sig"), "native_tables": [], "limitations": []}
    body = result["body"]
    if "\x00" in body or not (body.strip() or result.get("supplemental_hashes")):
        raise ValueError("empty/invalid body")
    return {"path": str(path), "source_sha256": digest, **result,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="读取指定文件或递归盘点指定目录").add_argument("paths", nargs="+")
    query = commands.add_parser("subjects", help="按名称、别名或 ID 查询在用公司和主题")
    query.add_argument("query")
    query.add_argument("--kind", choices=("company", "theme"))
    query.add_argument("--limit", type=int, default=50)
    query.add_argument("--children", action="store_true", help="仅查主题，列出各匹配项的在用直接子主题、类型和范围")
    industry = commands.add_parser("industries", help="查询可归档的申万 2021 一级、二级行业及路径")
    industry.add_argument("query", nargs="?", default="", help="按名称或代码查询。三级检索词指向其二级行业")
    commands.add_parser("extract", help="读取 Word 正文、可提取的原生表格或 UTF-8 TXT/MD").add_argument("path")
    compare = commands.add_parser("compare-word", help="用库内原文比对 Word 的文档身份、新增段落和修订")
    compare.add_argument("path")
    compare.add_argument("--choice", help="使用者已确认的候选文档 ID 或 new")
    args = parser.parse_args()
    if args.command == "extract":
        result = extract(args.path)
    else:
        with closing(read_db()) as db:
            if args.command == "scan":
                result = scan(args.paths, db)
            elif args.command == "compare-word":
                import local_word
                proposal = local_word.propose(db, safe_source(args.path), choice=args.choice)
                result = {key: value for key, value in proposal.items()
                          if key != "version" and not key.startswith("_")}
            elif args.command == "industries":
                result = industries(db, args.query)
            else:
                result = subjects(db, args.query, args.kind, args.limit, children=args.children)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("issues") else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError, UnicodeError) as failure:
        print(json.dumps({"error": str(failure)}, ensure_ascii=False))
        sys.exit(1)


"""Exercise direct DOCX media proofs and preview storage in an isolated Library."""
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from zipfile import ZipFile
import zlib


MASTER = Path(__file__).resolve().parents[2]
LIBRARY = MASTER.parent / "Research-Info-Hub" / "Library"
WHEN = "2026-11-19T10:00:00+08:00"


def digest(raw):
    return sha256(raw).hexdigest()


def png(red):
    def chunk(tag, value):
        return struct.pack(">I", len(value)) + tag + value + struct.pack(">I", zlib.crc32(tag + value))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes((0, red, 31, 93, red, 55, 101)))) + chunk(b"IEND", b""))


def emf():
    header = bytearray(88)
    struct.pack_into("<II8iIIII", header, 0, 1, 88, 0, 0, 2, 1, 0, 0, 2, 1, 0x464d4520, 0x10000, 120, 3)
    return header + struct.pack("<III5I", 18, 12, 1, 14, 20, 0, 0, 20)


def verify(root):
    scripts = root / "Research-Master" / "Skills" / "library" / "scripts"
    shutil.copytree(MASTER / "Skills" / "library", scripts.parent)
    target = root / "Research-Info-Hub" / "Library"
    target.mkdir(parents=True)
    database = target / "Company-Profile-local.sqlite"
    with closing(sqlite3.connect((LIBRARY / database.name).as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(database)) as db:
            original.backup(db)
            theme_id, theme, scope = db.execute("SELECT id,name,scope FROM cp_theme WHERE status='active' LIMIT 1").fetchone()
    shutil.copytree(LIBRARY / "images", target / "images", ignore=shutil.ignore_patterns("image-input"))
    doc = root / "chart-evidence.docx"
    preview = root / "reviewed-preview.png"
    preview.write_bytes(png(39))
    body = "独立演练材料记录了铜线机台的合成测试指标及本次取样条件。"
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    images = {"rId1": ("plot.png", png(27)), "rId2": ("plot.emf", emf()), "rId3": ("decoration.png", png(51))}
    with ZipFile(doc, "w") as z:
        blips = "".join(f'<w:r><w:drawing><a:blip r:embed="{rid}"/></w:drawing></w:r>' for rid in images)
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}" xmlns:r="{rel}" xmlns:a="{a}">'
                   f'<w:body><w:p><w:r><w:t>{body}</w:t></w:r>{blips}</w:p></w:body></w:document>')
        children = "".join(f'<Relationship Id="{rid}" Type="{rel}/image" Target="media/{name}"/>'
                           for rid, (name, _) in images.items())
        z.writestr("word/_rels/document.xml.rels",
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{children}</Relationships>')
        z.writestr("word/footnotes.xml",f'<w:footnotes xmlns:w="{w}"><w:footnote><w:p><w:r>'
                   '<w:t>Fixture footnote is read but not archived.</w:t></w:r></w:p></w:footnote></w:footnotes>')
        for name, content in images.values():
            z.writestr("word/media/" + name, content)

    source_hash = digest(doc.read_bytes())
    entry = dict(source_quote="synthetic chart scale", region=[0.0, 0.0, 1.0, 1.0], reason="synthetic image fixture")
    research = dict(policy="forward-only-material-increment-v1", source_context="standalone_chart",
                    decision="retain_original", whole_body_reviewed=True, covered_text=[],
                    text_coverage_assessment="刻度在图中", material_increment="保留刻度", retention_reason="独立证据",
                    search=[dict(entry, kind="theme", id=theme_id, name=theme, scope_sha256=digest(scope.encode()),
                                 unit="", period=""), dict(entry, kind="metric", id=None, name="合成测试刻度",
                                 scope_sha256=None, unit="单位", period="测试")])

    def claim(rid):
        return dict(origin=dict(kind="docx_media", part="word/document.xml", relationship_id=rid),
                    image_sha256=digest(images[rid][1]), source_sha256=source_hash,
                    reviewed_body_sha256=digest(body.encode()), research=research, role="standalone_chart",
                    classification_reason="test fixture", reviewed_by="fixture", reviewed_at=WHEN)

    meta = dict(original_sha256=digest(images["rId2"][1]), preview_sha256=digest(preview.read_bytes()),
                kind="derived_render", renderer="fixture", renderer_version="1", renderer_binary_sha256="0"*64,
                render_options={}, visual_review="rendered_preview_reviewed_with_original_provenance",
                reviewed_by="fixture", reviewed_at=WHEN)
    item = dict(file=str(doc), sha256=source_hash, category="internal", title="合成图片验证", body=body,
                institution=None, material_date=None, companies=[], themes=[dict(theme_id=theme_id,
                    reason="fixture", evidence=dict(quote=body, claim_kind="fact"))],
                primary_subject=dict(kind="theme", subject_id=theme_id, reason="fixture"),
                review=dict(reviewer="fixture", reviewed_at=WHEN, reason="fixture", dedup_checked=True),
                original_images=[claim("rId1")], original_vector_images=[dict(original=claim("rId2"), preview=meta)])
    review, batch = root / "review.json", root / "batch.json"
    review.write_text(json.dumps(dict(batch_id="direct-photos", items=[item])), encoding="utf-8")

    def run(script, *args):
        p = subprocess.run([sys.executable, "-X", "utf8", "-B", str(scripts / script), *map(str, args)],
                           capture_output=True, encoding="utf-8")
        assert p.returncode == 0, (p.stdout, p.stderr)
        return p.stdout

    assert json.loads(run("local_sources.py", "extract", doc))["supplemental_texts"]["word/footnotes.xml"] == (
        "Fixture footnote is read but not archived.")
    run("prepare_batch.py", review, "--out", batch)
    args = ("--batch", batch, "--source", doc, "--preview", preview)
    assert json.loads(run("local_archive.py", "check", *args))["status"] == "checked"
    assert json.loads(run("local_archive.py", "apply", *args))["created"] == 1
    assert json.loads(run("local_archive.py", "apply", *args))["created"] == 0
    with ZipFile(doc) as z:
        members = {name: z.read(name) for name in z.namelist()}

    def change_part(part, raw):
        members[part] = raw
        with ZipFile(doc, "w") as z:
            for name, value in members.items():
                z.writestr(name, value)
        proposal = json.loads(run("local_sources.py", "compare-word", doc))
        assert proposal["status"] == "matched" and not proposal["changes"], proposal
        assert part in proposal["supplemental"]["modified"], proposal

    def submit_delta(batch_id, part, delta, image_claim=None):
        revised = deepcopy(item)
        revised.pop("original_vector_images")
        revised.pop("original_images")
        revised.update(body=delta, sha256=digest(doc.read_bytes()), word_review={"parts": [part]})
        revised["themes"][0]["evidence"]["quote"] = delta
        if image_claim:
            revised["original_images"] = [image_claim]
        review_path, batch_path = root / (batch_id + "-review.json"), root / (batch_id + ".json")
        review_path.write_text(json.dumps(dict(batch_id=batch_id, items=[revised])), encoding="utf-8")
        run("prepare_batch.py", review_path, "--out", batch_path)
        arguments = ("--batch", batch_path, "--source", doc)
        assert json.loads(run("local_archive.py", "check", *arguments))["status"] == "checked"
        result = json.loads(run("local_archive.py", "apply", *arguments))
        mid = result["items"][0]["memo_id"]
        assert result["created"] == 1
        assert json.loads(run("local_archive.py", "receipt", "--batch-id", batch_id))["memo_ids"] == [mid]
        with closing(sqlite3.connect(database)) as db:
            stored = db.execute("SELECT b.body FROM cp_memo m JOIN cp_body b ON b.id=m.body_id WHERE m.id=?",
                                (mid,)).fetchone()[0]
            assert stored == delta and body not in stored
            return [row[0] for row in db.execute("SELECT image_sha256 FROM cp_memo_original_image WHERE memo_id=?",
                                                 (mid,))]

    changed_chart = png(73)
    png_part = "word/media/plot.png"
    change_part(png_part, changed_chart)
    chart_quote = "红色通道 73"
    chart_delta = "【图中摘录】" + chart_quote
    revised_claim = deepcopy(item["original_images"][0])
    revised_claim.update(image_sha256=digest(changed_chart), source_sha256=digest(doc.read_bytes()),
                         reviewed_body_sha256=digest(chart_delta.encode()))
    revised_claim["research"]["search"][1]["source_quote"] = chart_quote
    assert submit_delta("direct-chart-only", png_part, chart_delta, revised_claim) == [digest(changed_chart)]

    note_part = "word/footnotes.xml"
    note = "Revised synthetic sampling threshold is eight copper counters."
    change_part(note_part, members[note_part].replace(b"Fixture footnote is read but not archived.", note.encode()))
    assert submit_delta("direct-footnote-only", note_part, note) == []
    doc.unlink()
    preview.unlink()
    for batch_id in ("direct-photos", "direct-chart-only", "direct-footnote-only"):
        assert json.loads(run("local_archive.py", "receipt", "--batch-id", batch_id))["status"] == "verified"
    objects = {p.name.split(".")[0] for p in (target / "images" / "objects").rglob("*") if p.is_file()}
    assert {digest(images["rId1"][1]), digest(images["rId2"][1])} <= objects
    assert digest(images["rId3"][1]) not in objects
    assert any(p.stem == meta["preview_sha256"] for p in (target / "images" / "derived").rglob("*.png"))
    assert not (target / "images" / "image-input").exists() and not list(target.rglob("*.bin"))
    for record in (target / "archive-history" / "requests").glob("*.json"):
        assert "Fixture footnote is read but not archived." not in record.read_text(encoding="utf-8")
    print("Verified direct DOCX image and vector proofs, chart-only/footnote-only deltas, and source-independent receipts.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="li-") as tmp:
        verify(Path(tmp).resolve())

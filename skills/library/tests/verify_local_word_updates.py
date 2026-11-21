"""Run the public Word archive workflow on a disposable copy of the local Library."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape
from zipfile import ZipFile


MASTER = Path(__file__).resolve().parents[2]
LIBRARY = MASTER.parent / "Research-Info-Hub" / "Library"
REVIEWED_AT = "2026-11-19T12:00:00+08:00"


def write_docx(path, paragraphs, *, formatted=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    style = "<w:rPr><w:b/></w:rPr>" if formatted else ""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r>{style}<w:t>{escape(line)}</w:t></w:r></w:p>" for line in paragraphs)
    with ZipFile(path, "w") as doc:
        doc.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                     'wordprocessingml.document.main+xml"/></Types>')
        doc.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                     'officeDocument" Target="word/document.xml"/></Relationships>')
        doc.writestr("word/document.xml", f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>')


def seed_legacy(database, root, label, paragraphs):
    """Represent an old partial Word memo; the historical file itself is unavailable."""
    key = "m-test-word-" + label
    body = "\n".join(paragraphs)
    source = root / "unavailable" / (label + ".docx")
    with closing(sqlite3.connect(database)) as db:
        with db:
            db.execute("INSERT INTO cp_body VALUES(?,?,?)", (key, body, hashlib.sha256(body.encode()).hexdigest()))
            db.execute("INSERT INTO cp_memo VALUES(?,?,?,?,?,?,?,?,?)",
                       (key, key, key, "internal", None, label, None, "unknown", "active"))
            db.execute("INSERT INTO cp_memo_revision VALUES(?,1,?,?,?)", (key, key, REVIEWED_AT, "fixture"))
            db.execute("INSERT INTO cp_source_record VALUES(?,?,?,?,?,?,?,?)",
                       (key, "library-local-file", source.as_uri(), "local_file", hashlib.sha256(label.encode()).hexdigest(),
                        str(source), "{}", "parsed"))
            db.execute("INSERT INTO cp_memo_source VALUES(?,?,'primary')", (key, key))
    return key


def verify(root):
    scripts = root / "Research-Master" / "Skills" / "library" / "scripts"
    shutil.copytree(MASTER / "Skills" / "library", scripts.parent)
    target = root / "Research-Info-Hub" / "Library"
    target.mkdir(parents=True)
    database = target / "Company-Profile-local.sqlite"
    with closing(sqlite3.connect((LIBRARY / database.name).as_uri() + "?mode=ro", uri=True)) as live:
        with closing(sqlite3.connect(database)) as copy:
            live.backup(copy)
    shutil.copytree(LIBRARY / "images", target / "images", ignore=shutil.ignore_patterns("image-input"))

    def cli(script, *args, success=True, json_output=True):
        proc = subprocess.run([sys.executable, "-X", "utf8", "-B", str(scripts / (script + ".py")), *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8", cwd=root)
        assert (proc.returncode == 0) == success, (script, args, proc.stderr, proc.stdout)
        return json.loads(proc.stdout) if success and json_output else (proc.stdout + proc.stderr)

    def compare(path, *rest):
        return cli("local_sources", "compare-word", path, *rest)

    def prepare(batch_id, path, lines, company, **options):
        body = "\n".join(lines)
        item = {
            "file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "title": "Synthetic Word update", "category": "internal", "material_date": None, "institution": None,
            "body": body, "companies": [{"company_id": company, "scope_kind": "primary",
                "reason": "Synthetic company evidence", "evidence": {"quote": lines[0], "claim_kind": "fact"}}],
            "themes": [], "primary_subject": {"kind": "company", "subject_id": company, "reason": "Synthetic fixture"},
            "review": {"reviewed_at": REVIEWED_AT, "reviewer": "test", "reason": "Synthetic Word regression",
                       "dedup_checked": True}, **options,
        }
        review = root / (batch_id + "-review.json")
        batch = root / (batch_id + ".json")
        review.write_text(json.dumps({"batch_id": batch_id, "items": [item]}, ensure_ascii=False), encoding="utf-8")
        cli("prepare_batch", review, "--out", batch, json_output=False)
        return ("--batch", batch, "--source", path)

    def bodies():
        with closing(sqlite3.connect(database)) as db:
            return dict(db.execute("SELECT m.id,b.body FROM cp_memo m JOIN cp_body b ON m.body_id=b.id"))

    with closing(sqlite3.connect(database)) as db:
        company = db.execute("SELECT id FROM cp_company WHERE name='NVIDIA' AND status='active'").fetchone()[0]
    anchors = [
        "NVIDIA 木星甲组的独立验收材料记录了织机上游温控报警与隔离复核之间的单独映射。",
        "合成项目蓝帆四号的现场取样小组在周三下午重新校准压力探头，校准前仪表误差为千分之七。",
        "项目翠鸟七号通过两套独立的合成阻尼试验后进入第二轮热循环，并保留跨批号的复检证据。",
        "蓝帆项目组对模拟器通道转换率的审核口径保持一致，书面确认本轮指引由基准模型推导为 75%。",
    ]
    old = root / "meeting" / "notes.docx"
    write_docx(old, anchors)
    original_hash = hashlib.sha256(old.read_bytes()).hexdigest()
    assert compare(old)["status"] == "new"
    first = prepare("test-word-initial", old, anchors, company)
    assert cli("local_archive", "check", *first)["status"] == "checked"
    assert cli("local_archive", "apply", *first)["created"] == 1
    baseline = bodies()
    assert "\n".join(anchors) in baseline.values()
    assert not (target / "images" / "image-input").exists()
    assert not list(target.rglob(original_hash + ".bin"))
    old.unlink()

    revised = anchors[-1].replace("75%", "72%")
    added = "新增加的乌桕研究组在复核汇总后要求下一轮独立面板披露每小时故障恢复次数和基线窗口。"
    updated = [*anchors[:-1], revised, added]
    moved = root / "another-team" / "重命名的副本.docx"
    write_docx(moved, updated)
    result = compare(moved)
    assert result["status"] == "matched", result
    assert {v["text"]: v["kind"] for v in result["changes"]} == {revised: "revised", added: "added"}, result
    repeated = prepare("test-word-whole-old-and-new", moved, updated, company)
    assert "增量" in cli("local_archive", "check", *repeated, success=False)
    revised_titles = {"intake_material_title": "合成调研纪要修订稿", "original_material_title": "合成项目复核会议"}
    second = prepare("test-word-delta", moved, [revised, added], company, **revised_titles)
    assert cli("local_archive", "check", *second)["status"] == "checked"
    other_file = root / "pending" / "another draft.docx"
    other_paragraph = "较早提交的并行审阅稿另外说明了阴极盘点期间外包工时按照申报周期分别结算。"
    write_docx(other_file, [*anchors, other_paragraph])
    stale = prepare("test-word-stale-review", other_file, [other_paragraph], company)
    assert cli("local_archive", "check", *stale)["status"] == "checked"
    assert cli("local_archive", "apply", *second)["created"] == 1
    receipt = cli("local_archive", "receipt", "--batch-id", "test-word-delta")
    assert receipt["status"] == "verified" and receipt["control"]["word_versions"], receipt
    assert set(bodies().values()) - set(baseline.values()) == {"\n".join([revised, added])}
    assert cli("local_archive", "apply", *second)["status"] == "replayed"
    assert "重新比对" in cli("local_archive", "apply", *stale, success=False)
    assert cli("local_archive", "receipt", "--batch-id", "test-word-stale-review")["status"] == "no_receipt"

    same = root / "elsewhere" / "reformatted.docx"
    write_docx(same, updated, formatted=True)
    assert same.read_bytes() != moved.read_bytes()
    assert compare(same)["status"] == "unchanged"
    covered = prepare("test-word-covered-titles", same, [revised, added], company, **revised_titles)
    assert cli("local_archive", "check", *covered)["status"] == "already_archived"
    different = {**revised_titles, "original_material_title": "另一场调查访谈"}
    conflicting = prepare("test-word-conflicting-title", same, [revised, added], company, **different)
    cli("local_archive", "check", *conflicting, success=False)
    assert cli("local_archive", "receipt", "--batch-id", "test-word-conflicting-title")["status"] == "no_receipt"
    assert not (target / "images" / "image-input").exists()

    historical = [
        "NVIDIA 合成人参甲会谈的底稿讨论了蓝绿色调色仪开机时的光学排校误差与小时工时定价。",
        "人参甲访谈第二个议题关注砖红色涂布线连续加班产生的刮板消耗，供应商以净耗加损耗结算。",
        "人参甲项目最后逐一核对了供应商换模损耗，所有留样都用手动测厚仪进行背面厚度复核。",
    ]
    legacy = seed_legacy(database, root, "legacy-only", historical)
    legacy_added = "补收的人参甲原文逐项解释了浅紫色背板的复检次数和下一阶段单位修理工时的变动。"
    later = root / "changed-folder" / "不同的旧稿文件名.docx"
    write_docx(later, [*historical, legacy_added])
    match = compare(later)
    assert match["status"] == "matched" and legacy in match["prior_memo_ids"], match
    assert {v["text"] for v in match["changes"]} == {legacy_added}, match
    assert match["changes"][0]["kind"] == "uncovered", match

    twin = seed_legacy(database, root, "legacy-second", [*historical, "另一份合成访谈的末段只谈运输与物流。"])
    unclear = compare(later)
    assert unclear["status"] == "ambiguous" and len(unclear["candidates"]) >= 2, unclear
    disputed = prepare("test-word-ambiguous", later, [legacy_added], company)
    before = bodies()
    assert "确认" in cli("local_archive", "apply", *disputed, success=False)
    assert bodies() == before
    assert cli("local_archive", "receipt", "--batch-id", "test-word-ambiguous")["status"] == "no_receipt"
    candidate = next(c for c in unclear["candidates"] if legacy in c["memo_ids"])
    document_id = candidate["document_id"]
    chosen = compare(later, "--choice", document_id)
    assert chosen["status"] == "matched" and twin not in chosen["prior_memo_ids"], chosen
    confirmed = prepare("test-word-confirmed", later, [legacy_added], company,
                        word_review={"choice": document_id, "confirmation": "Use the first historical interview."})
    assert cli("local_archive", "apply", *confirmed)["created"] == 1
    assert cli("local_archive", "receipt", "--batch-id", "test-word-confirmed")["status"] == "verified"
    print("Verified new Word, moved edits, formatting-only replay, historical DB matching, explicit selection, and no source copies.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="lw-") as temporary:
        verify(Path(temporary).resolve())

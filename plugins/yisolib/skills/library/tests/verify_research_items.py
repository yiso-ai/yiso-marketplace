"""Verify multi-memo briefings through the public CLI in a disposable local Library."""

from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile


MASTER = Path(__file__).resolve().parents[2]
LIBRARY = MASTER.parent / "Research-Info-Hub" / "Library"
CREDIT_FIELDS = ("source_entity_type", "material_type", "publication_channel", "institution", "author")
TITLE_FIELDS = ("intake_material_title", "original_material_title")


def verify(root):
    # The Windows LibreOffice launcher reports its runtime directory as sys.executable.
    python = sys.executable if Path(sys.executable).is_file() else shutil.which("python")
    if not python:
        raise RuntimeError("No Python executable for the isolated CLI test")
    scripts = root / "Research-Master" / "Skills" / "library" / "scripts"
    shutil.copytree(MASTER / "Skills" / "library", scripts.parent)
    target = root / "Research-Info-Hub" / "Library"
    target.mkdir(parents=True)
    database = target / "Company-Profile-local.sqlite"
    with closing(sqlite3.connect((LIBRARY / database.name).as_uri() + "?mode=ro", uri=True)) as live:
        with closing(sqlite3.connect(database)) as copy:
            live.backup(copy)
    shutil.copytree(LIBRARY / "images", target / "images", ignore=shutil.ignore_patterns("image-input"))

    def query(sql, parameters=()):
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            return db.execute(sql, parameters).fetchall()

    def cli(script, *args, succeed=True, expect_json=True):
        proc = subprocess.run([python, "-X", "utf8", "-B", str(scripts / (script + ".py")), *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8", cwd=root)
        assert (proc.returncode == 0) == succeed, (script, args, proc.stdout, proc.stderr)
        return json.loads(proc.stdout) if succeed and expect_json else proc.stdout + proc.stderr

    def prepare(name, items):
        review = root / (name + "-review.json")
        batch = root / (name + ".json")
        review.write_text(json.dumps({"batch_id": name, "items": items}, ensure_ascii=False), encoding="utf-8")
        cli("prepare_batch", review, "--out", batch, expect_json=False)
        return ("--batch", batch, "--source", root / "inputs")

    def counts():
        return query("SELECT (SELECT count(*) FROM cp_memo), (SELECT count(*) FROM cp_source_record), "
                     "(SELECT count(*) FROM local_archive_receipt)")[0]

    def add_counts(base, memos, sources, receipts):
        return tuple(n + change for n, change in zip(base, (memos, sources, receipts)))

    company = query("SELECT id FROM cp_company WHERE status='active' ORDER BY id LIMIT 1")[0][0]
    theme = query("SELECT id FROM cp_theme WHERE status='active' AND name='AI Capex'")[0][0]
    inputs = root / "inputs"
    inputs.mkdir()
    briefing = inputs / "Synthetic research briefing.txt"
    bodies = ["Synthetic receipt fixture: first company's brass gear orders increased to 41.",
              "Synthetic receipt fixture: industry power demand reached 57 calibration units.",
              "Synthetic receipt fixture: second research item on the same company's margin rose to 13%."]
    briefing.write_text("\n".join(bodies), encoding="utf-8")

    def item(path, body, slug, category, institution, author, kind, subject):
        reason = "Synthetic issue under a single primary subject."
        assignment = {kind + "_id": subject, "reason": reason, "evidence": {"quote": body, "claim_kind": "fact"}}
        if kind == "company":
            assignment["scope_kind"] = "primary"
        return {"file": str(path), "sha256": sha256(path.read_bytes()).hexdigest(),
                "research_item": slug, "title": "Synthetic fixture " + slug, "body": body, "category": category,
                "material_date": None, "institution": institution, "author": author,
                "primary_subject": {"kind": kind, "subject_id": subject, "reason": reason},
                "companies": [assignment] if kind == "company" else [],
                "themes": [assignment] if kind == "theme" else [],
                "review": {"reviewed_at": "2026-11-19T12:00:00+08:00", "reviewer": "test", "reason": reason,
                           "dedup_checked": True}}

    one = item(briefing, bodies[0], "company-orders", "internal", None, "Synthetic Analyst", "company", company)
    two = item(briefing, bodies[1], "power-demand", "sellside", "Synthetic Research", None, "theme", theme)
    three = item(briefing, bodies[2], "company-margin", "news", None, None, "company", company)
    digest_title = "Synthetic Research Dispatch, 19 November"
    one.update(source_entity_type="内部团队", material_type="调研访谈纪要", publication_channel="微信公众号",
               intake_material_title=digest_title, original_material_title="Orders: the plant manager interview")
    two.update(source_entity_type="卖方机构", material_type="研究分析", publication_channel="X",
               intake_material_title=digest_title, original_material_title="Industry Power: Supply / Demand")
    three.update(source_entity_type="公司", material_type="管理层交流", publication_channel=None,
                 intake_material_title=digest_title)
    first = prepare("digest-first", [one, two])
    before = counts()
    initial_preview = cli("local_archive", "check", *first)
    assert initial_preview["selected"] == 2
    for actual, expected in zip(initial_preview["research_items"], (one, two), strict=True):
        assert (actual["status"], actual["memo_ids"]) == ("selected", [])
        assert all(actual[key] == expected[key] for key in ("research_item", "title", "body"))
        assert actual["primary_subject"]["subject_id"] == expected["primary_subject"]["subject_id"]
        assert actual["primary_subject"]["registry"] == "existing" and actual["primary_subject"]["name"]
        assert all(actual["attribution"][key] == expected[key] for key in (*CREDIT_FIELDS, *TITLE_FIELDS))
    assert counts() == before and not (target / "archive-history").exists()
    written = cli("local_archive", "apply", *first)
    assert written["status"] == "committed" and written["created"] == 2
    assert Path(written["backup_bundle"]).exists()
    memo_ids = [entry["memo_id"] for entry in written["items"]]
    assert len(set(memo_ids)) == 2
    for mid, expected in zip(memo_ids, (one, two)):
        body, category, institution, kind, primary, record_key, locator, metadata = query(
            "SELECT b.body,m.category,m.institution,p.subject_kind,coalesce(p.company_id,p.theme_id),"
            "s.record_key,s.private_locator,s.metadata_json FROM cp_memo m JOIN cp_body b ON b.id=m.body_id "
            "JOIN local_memo_primary_subject p ON p.memo_id=m.id JOIN cp_memo_source ms ON ms.memo_id=m.id "
            "JOIN cp_source_record s ON s.id=ms.source_id WHERE m.id=?", (mid,))[0]
        assert (body, category, institution) == (expected["body"], expected["category"], expected["institution"])
        assert (kind, primary) == (expected["primary_subject"]["kind"], expected["primary_subject"]["subject_id"])
        assert all(briefing.name not in str(v) and briefing.as_uri() not in str(v) for v in (record_key, locator, metadata))
        source_meta = json.loads(metadata)
        attribution = source_meta["attribution"]
        assert all(attribution[key] == expected[key] for key in (*CREDIT_FIELDS, *TITLE_FIELDS))
        description = source_meta["source_description"]
        assert all(expected[key] in description for key in CREDIT_FIELDS if expected[key])
        assert description == initial_preview["research_items"][memo_ids.index(mid)]["source_description"]
    assert counts() == add_counts(before, 2, 2, 1)
    assert cli("local_archive", "apply", *first)["status"] == "replayed"

    renamed = inputs / "renamed.txt"
    briefing.rename(renamed)
    one, two, three = ({**entry, "file": str(renamed)} for entry in (one, two, three))
    assert cli("local_archive", "receipt", "--batch-id", "digest-first")["memo_ids"] == memo_ids
    scanned = cli("local_sources", "scan", renamed)
    assert {r["memo_id"] for r in scanned["files"][0]["existing_same_bytes"]} == set(memo_ids)
    repeat_args = prepare("digest-reordered", [two, one])
    covered = cli("local_archive", "check", *repeat_args)["research_items"]
    assert [(r["status"], r["memo_ids"]) for r in covered] == [
        ("already_archived", [mid]) for mid in reversed(memo_ids)]
    assert [r["attribution"]["original_material_title"] for r in covered] == [
        two["original_material_title"], one["original_material_title"]]
    repeated = cli("local_archive", "apply", *repeat_args)
    assert repeated["status"] == "already_archived"
    assert {mid for matches in repeated["already_archived"].values() for mid in matches} == set(memo_ids)
    assert counts() == add_counts(before, 2, 2, 1)

    partial = prepare("digest-supplement", [two, three, one])
    mixed = cli("local_archive", "check", *partial)
    assert mixed["selected"] == 1
    assert [(r["status"], r["memo_ids"]) for r in mixed["research_items"]] == [
        ("already_archived", [memo_ids[1]]), ("selected", []), ("already_archived", [memo_ids[0]])]
    supplement = cli("local_archive", "apply", *partial)
    assert supplement["created"] == 1
    source_id = supplement["items"][0]["source_id"]
    remaining = json.loads(query("SELECT metadata_json FROM cp_source_record WHERE id=?", (source_id,))[0][0])
    assert all(remaining["attribution"][key] == three[key] for key in CREDIT_FIELDS)
    assert remaining["attribution"]["intake_material_title"] == digest_title
    assert remaining["attribution"]["original_material_title"] is None
    assert not any(null_text in remaining["source_description"] for null_text in ("None", "null", "未知"))
    assert cli("local_archive", "apply", *partial)["status"] == "replayed"
    assert counts() == add_counts(before, 3, 3, 2)
    changed = {**two, "body": two["body"] + " Conflicting revised forecast."}
    for suffix, entry in (("body", changed), ("author", {**one, "author": "Another Analyst"}),
                          ("channel", {**two, "publication_channel": "微信公众号"}),
                          ("material", {**two, "material_type": "调研访谈纪要"}),
                          ("intake-title", {**one, "intake_material_title": "Another dispatch"}),
                          ("original-title", {**two, "original_material_title": "Different research note"})):
        args = prepare("digest-conflict-" + suffix, [entry])
        cli("local_archive", "check", *args, succeed=False)
        cli("local_archive", "apply", *args, succeed=False)
        assert cli("local_archive", "receipt", "--batch-id", "digest-conflict-" + suffix)["status"] == "no_receipt"
    other_path = inputs / "separate briefing.txt"
    other_path.write_text(one["body"] + "\nA separate synthetic publication.", encoding="utf-8")
    other_source = {**one, "file": str(other_path), "sha256": sha256(other_path.read_bytes()).hexdigest()}
    other_author = prepare("other-author", [{**other_source, "author": "Another Analyst"}])
    cli("local_archive", "check", *other_author, succeed=False)
    cli("local_archive", "apply", *other_author, succeed=False)
    other_channel = prepare("other-channel", [{**other_source, "publication_channel": "X"}])
    cli("local_archive", "check", *other_channel, succeed=False)
    two_keys = prepare("duplicate-matter", [one, other_source, {**one, "research_item": "second-key"}])
    cli("local_archive", "check", *two_keys, succeed=False)
    assert counts() == add_counts(before, 3, 3, 2)

    # Distinct issues in the same file remain atomic if the second has an invalid subject.
    rollback_path = inputs / "rollback.txt"
    fresh_body = "Synthetic rollback fixture: first independently approved research item."
    bad_body = "Synthetic rollback fixture: second item refers to a missing subject."
    rollback_path.write_text(fresh_body + "\n" + bad_body, encoding="utf-8")
    fresh = item(rollback_path, fresh_body, "new-orders", "internal", None, None, "company", company)
    bad = item(rollback_path, bad_body, "new-credit", "sellside", None, None, "theme", theme)
    bad["companies"] = [{"company_id": "missing-synthetic-company", "scope_kind": "substantive",
                         "reason": "Exercise transactional rollback",
                         "evidence": {"quote": bad_body, "claim_kind": "fact"}}]
    rollback = prepare("digest-rollback", [fresh, bad])
    cli("local_archive", "apply", *rollback, succeed=False)
    assert counts() == add_counts(before, 3, 3, 2)
    assert not query("SELECT id FROM cp_body WHERE body=?", (fresh_body,))

    # No new keys or attribution are required for existing single-source reviews.
    legacy_path = inputs / "old-style.txt"
    old_body = "Synthetic standalone item using the previous review format."
    legacy_path.write_text(old_body, encoding="utf-8")
    legacy = item(legacy_path, old_body, "old-style", "internal", None, None, "company", company)
    del legacy["research_item"], legacy["author"]
    old = prepare("digest-legacy", [legacy])
    assert cli("local_archive", "apply", *old)["created"] == 1
    assert cli("local_archive", "apply", *prepare("digest-legacy-again", [legacy]))["status"] == "already_archived"
    assert counts() == add_counts(before, 4, 4, 3)
    # The reviewed source title can be known even when the intake document has no confirmed name.
    standalone_path = inputs / "issuer-update.txt"
    standalone_body = "Synthetic issuer update: revenue reached 321 units."
    standalone_path.write_text(standalone_body, encoding="utf-8")
    standalone = item(standalone_path, standalone_body, "issuer-update", "news", "Synthetic Issuer", None,
                      "company", company)
    del standalone["research_item"], standalone["category"]
    standalone.update(source_entity_type="公司", material_type="公司公告", publication_channel="交易所",
                      original_material_title="Annual Revenue Update", intake_material_title="  ")
    standalone_args = prepare("company-publication", [standalone])
    preview = cli("local_archive", "check", *standalone_args)["research_items"]
    assert len(preview) == 1 and preview[0]["research_item"] is None
    assert all(preview[0]["attribution"][key] == standalone[key] for key in CREDIT_FIELDS)
    assert preview[0]["attribution"]["intake_material_title"] is None
    assert preview[0]["attribution"]["original_material_title"] == standalone["original_material_title"]
    standalone_written = cli("local_archive", "apply", *standalone_args)
    standalone_source = standalone_written["items"][0]["source_id"]
    stored = json.loads(query("SELECT metadata_json FROM cp_source_record WHERE id=?", (standalone_source,))[0][0])
    assert all(stored["attribution"][key] == standalone[key] for key in CREDIT_FIELDS)
    assert stored["attribution"]["intake_material_title"] is None
    assert stored["attribution"]["original_material_title"] == standalone["original_material_title"]
    assert cli("local_archive", "receipt", "--batch-id", "company-publication")["status"] == "verified"
    assert cli("local_archive", "apply", *standalone_args)["status"] == "replayed"
    assert counts() == add_counts(before, 5, 5, 4)

    prior_path = inputs / "research-source.txt"
    prior_body = "Synthetic five-field review: two machines were commissioned."
    prior_path.write_text(prior_body, encoding="utf-8")
    prior_item = item(prior_path, prior_body, "commissioned-machines", "sellside", "Synthetic Firm", None,
                      "company", company)
    prior_item.update(source_entity_type="卖方机构", material_type="研究分析", publication_channel="电子邮件")
    prior_args = prepare("existing-five-credits", [prior_item])
    prior_batch = json.loads(prior_args[1].read_text(encoding="utf-8"))["items"][0]
    assert not any(key in prior_batch for key in TITLE_FIELDS)
    assert cli("local_archive", "apply", *prior_args)["created"] == 1
    assert cli("local_archive", "receipt", "--batch-id", "existing-five-credits")["status"] == "verified"
    assert cli("local_archive", "apply", *prior_args)["status"] == "replayed"
    explicit_nulls = {**prior_item, "intake_material_title": None, "original_material_title": None}
    prior_again = prepare("existing-five-explicit-nulls", [explicit_nulls])
    assert cli("local_archive", "check", *prior_again)["selected"] == 0
    assert cli("local_archive", "apply", *prior_again)["status"] == "already_archived"

    title_path = inputs / "untagged-source.txt"
    title_body = "Synthetic review with only the published title confirmed."
    title_path.write_text(title_body, encoding="utf-8")
    untagged = item(title_path, title_body, "unclassified", "news", None, None, "company", company)
    del untagged["research_item"], untagged["category"], untagged["author"], untagged["institution"]
    untagged["original_material_title"] = "Unclassified Field Note"
    title_args = prepare("only-title-known", [untagged])
    title_credit = cli("local_archive", "check", *title_args)["research_items"][0]["attribution"]
    assert title_credit["original_material_title"] == untagged["original_material_title"]
    assert all(title_credit[key] is None for key in (*CREDIT_FIELDS, "intake_material_title"))
    title_result = cli("local_archive", "apply", *title_args)
    assert title_result["created"] == 1
    stored_credit = query("SELECT json_extract(metadata_json,'$.attribution') FROM cp_source_record WHERE id=?",
                          (title_result["items"][0]["source_id"],))[0][0]
    assert json.loads(stored_credit) == title_credit

    # Validate ordinary publications as well as split items, including prebuilt batch requests.
    for index, (field, forbidden) in enumerate((
        ("intake_material_title", r"C:\Private\Synthetic report.pdf"),
        ("original_material_title", "[Read the note](https://example.test/research)"),
        ("intake_material_title", "file:///home/analyst/source.pdf"),
        ("original_material_title", "https://example.test/report"),
    )):
        invalid_item = {**untagged, field: forbidden}
        invalid_batch = root / f"invalid-title-{index}.json"
        invalid_review = root / f"invalid-title-{index}-review.json"
        invalid_review.write_text(json.dumps({"batch_id": f"bad-title-{index}", "items": [invalid_item]}),
                                  encoding="utf-8")
        assert field in cli("prepare_batch", invalid_review, "--out", invalid_batch, succeed=False)
        template = json.loads(title_args[1].read_text(encoding="utf-8"))
        template["batch_id"] = f"bad-prepared-title-{index}"
        template["items"][0][field] = forbidden
        invalid_batch.write_text(json.dumps(template), encoding="utf-8")
        assert field in cli("local_archive", "check", "--batch", invalid_batch, "--source", inputs, succeed=False)
    assert counts() == add_counts(before, 7, 7, 6)
    print("Verified multi-memo transactions, independent source titles, queryable attribution, tentative or "
          "missing titles, link/path rejection, renamed inputs, replay, incremental append, and historical inputs.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="dr-") as temporary:
        verify(Path(temporary).resolve())

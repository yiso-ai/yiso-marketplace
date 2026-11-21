"""Exercise industry assignment on a private temporary copy of the sibling Library."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile


MASTER = Path(__file__).resolve().parents[2]
REGISTRY = MASTER.parent / "Research-Info-Hub" / "Library"
TABLES = ("cp_company", "cp_memo", "cp_company_industry", "cp_industry_review", "local_archive_receipt")
sys.dont_write_bytecode = True


def counts(database):
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES)


def run_regression(root):
    skill = root / "Research-Master" / "Skills" / "library"
    shutil.copytree(MASTER / "Skills" / "library", skill)
    target = root / "Research-Info-Hub" / "Library"
    target.mkdir(parents=True)
    database = target / "Company-Profile-local.sqlite"
    original = REGISTRY / database.name
    with closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(database)) as destination:
            source.backup(destination)
    shutil.copytree(REGISTRY / "images", target / "images", ignore=shutil.ignore_patterns("image-input"))
    sys.path.insert(0, str(skill / "scripts"))
    import prepare_batch
    import local_industry
    import local_sources

    def cli(command, *args, success=True):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(skill/"scripts"/"local_archive.py"), command, *map(str, args)],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert (result.returncode == 0) == success, (command, result.returncode, result.stderr, result.stdout)
        return json.loads(result.stdout) if success else result.stderr

    def prepare(name, items):
        batch = prepare_batch.prepare({"batch_id": name, "items": deepcopy(items)})
        out = root / (name + ".json")
        out.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        return ("--batch", out, "--source", root)

    def sample(index, document, code="110100"):
        name = f"归档行业验收虚构公司{index}"
        key = f"library-industry-test/company-{index}"
        cid = local_industry.archive.new_entities.company_id(key)
        business = ("本公司培育玉米和小麦种子。" if code == "110100" else
                    "本公司同时种植粮食和养殖水产，尚未披露分业务收入。")
        path = root / f"test-{index}-{document}.txt"
        body = f"{name}第{document}份合成研究材料。"
        path.write_text(body + "\n" + business, encoding="utf-8")
        reviewed_at = "2026-11-18T19:00:00+08:00"
        return {
            "file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "title": body, "category": "internal", "material_date": None, "institution": None, "body": body,
            "companies": [{"company_id": cid, "scope_kind": "primary", "reason": "测试公司",
                           "evidence": {"quote": name, "claim_kind": "fact"}}],
            "themes": [], "primary_subject": {"kind": "company", "subject_id": cid, "reason": "测试公司"},
            "review": {"reviewer": "test", "reviewed_at": reviewed_at, "reason": "隔离演练", "dedup_checked": True},
            "new_companies": [{"company_id": cid, "name": name, "identity_key": key, "aliases": [],
                               "evidence": {"quote": name},
                               "review": {"reviewer": "test", "reviewed_at": reviewed_at, "reason": "原件具名",
                                          "disambiguation_checked": True, "security_identity_not_asserted": True},
                               "industry": {"code": code, "basis": "source", "reason": business,
                                            "evidence": {"quote": business}}}],
        }

    with closing(sqlite3.connect(database)) as connection:
        historical = connection.execute(
            """SELECT batch_id FROM local_archive_receipt
               WHERE json_extract(receipt_json,'$.control.industry_assignments') IS NULL
               ORDER BY rowid DESC LIMIT 1""").fetchone()[0]
        connection.row_factory = sqlite3.Row
        menu = local_sources.industries(connection, "农林牧渔")["industries"]
        assert {"110000", "110100"} <= {item["code"] for item in menu}
        assert all(item["level"] in (1, 2) and len(item["path"]) == item["level"] for item in menu)
    assert cli("receipt", "--batch-id", historical)["industries"] == []

    first, second = sample(1, 1), sample(1, 2)
    before = counts(database)
    absent = deepcopy(first)
    del absent["new_companies"][0]["industry"]
    assert "requires" in cli("check", *prepare("test-industry-missing", [absent]), success=False)
    invalid = deepcopy(first)
    invalid["new_companies"][0]["industry"]["code"] = "999999"
    cli("check", *prepare("test-industry-unknown", [invalid]), success=False)
    conflict = deepcopy(second)
    conflict["new_companies"][0]["industry"]["code"] = "110200"
    assert "conflicting" in cli("apply", *prepare("test-industry-conflict", [first, conflict]), success=False)
    assert counts(database) == before

    batch_id = "sw-ok"
    # Older input may use a leaf code; a new company is now archived at its actual parent.
    first["new_companies"][0]["industry"]["code"] = "110101"
    second["new_companies"][0]["industry"]["code"] = "110000"
    broad = sample(2, 1, "110000")
    broad["new_companies"][0]["industry"] = {
        "code": "110000", "basis": "company_website", "source_title": "归档行业验收虚构公司2公司简介",
        "evidence": {"quote": "本集团经营种植及养殖业务。"}, "reason": "主营农业，未披露主导板块",
    }
    annual = sample(3, 1, "110200")
    annual["new_companies"][0]["industry"] = {
        "code": "110200", "basis": "annual_report", "source_title": "归档行业验收虚构公司3年度报告",
        "evidence": {"quote": "养殖及销售水产品占收入九成。"}, "reason": "主要收入来自渔业",
    }
    # A weaker first-level review preceding the matched second-level evidence must not lose detail.
    args = prepare(batch_id, [second, first, broad, annual])
    expected_paths = {"110100": ["农林牧渔", "种植业"], "110000": ["农林牧渔"], "110200": ["农林牧渔", "渔业"]}
    for command in ("check", "apply"):
        result = cli(command, *args)["industries"]
        assert {entry["code"]: [n["name"] for n in entry["path"]] for entry in result} == expected_paths
        assert all(entry["level"] == len(entry["path"]) for entry in result)
    intake = target / "images" / "image-input"
    assert not intake.exists() and not (target / "image-input").exists()
    after = counts(database)
    assert after == tuple(n + k for n, k in zip(before, (3, 4, 3, 3, 1))), (after, before)
    verified = cli("receipt", "--batch-id", batch_id)
    assert verified["status"] == "verified" and verified["industries"] == result
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        for item, code in ((first, "110100"), (broad, "110000"), (annual, "110200")):
            company = item["new_companies"][0]
            row = connection.execute("""
                SELECT n.code,n.level,m.mapping_kind FROM cp_company_industry m
                JOIN cp_industry_node n ON n.id=m.leaf_id
                JOIN cp_industry_review r ON r.company_id=m.company_id AND r.leaf_id=m.leaf_id
                WHERE m.company_id=? AND m.review_status='approved' AND m.valid_to IS NULL
            """, (company["company_id"],)).fetchall()
            assert row == [(code, len(expected_paths[code]), "research_mapping")], row
        for item in (broad, annual):
            company = item["new_companies"][0]
            row = connection.execute("SELECT source_json FROM cp_industry_review WHERE company_id=?",
                                     (company["company_id"],)).fetchone()
            saved = json.loads(row[0])
            assert all(saved[key] == company["industry"][key] for key in ("basis", "source_title", "evidence"))
            assert saved["evidence"]["quote"] not in item["body"]
    assert cli("apply", *args)["status"] == "replayed"
    assert cli("apply", *prepare("test-industry-new-batch", [first]))["status"] == "already_archived"
    assert not intake.exists() and not (target / "image-input").exists()
    assert counts(database) == after

    broken = sample(4, 1)
    broken["themes"] = [{"theme_id": "library-test-missing-theme", "reason": "触发事务回滚",
                         "evidence": {"quote": broken["body"], "claim_kind": "fact"}}]
    assert "unknown/inactive" in cli("apply", *prepare("sw-rollback", [broken]), success=False)
    assert counts(database) == after

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE cp_company_industry SET source=? WHERE company_id=?",
                           ("tampered temporary copy", first["new_companies"][0]["company_id"]))
        connection.commit()
    assert "industry receipt differs" in cli("receipt", "--batch-id", batch_id, success=False)
    print("Verified SW2021 level 2 and level 1, official evidence, receipts, commit/rollback and replay.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="sw-") as temp:
        run_regression(Path(temp).resolve())

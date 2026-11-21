"""Focused, in-memory checks for identifying and comparing archived Word documents."""

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Skills" / "library" / "scripts"))
import local_word


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class WordMatch(unittest.TestCase):
    first = "项目组在秋季访谈中首次分别披露东线和西线工厂的校准系数。"
    second = "采购端按实际交货批次核对次月可确认的样本，尚需独立复核。"
    prior = "季度材料通过率为75%，验收依据为现场设备填报记录。"
    revised = prior.replace("75%", "72%")
    added = "本周新增的压力测试扩大了试验覆盖的温度范围。"
    original_hash = digest("original old Word")

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE cp_source_record(id TEXT,namespace TEXT,record_key TEXT,private_locator TEXT,content_hash TEXT);
            CREATE TABLE cp_body(id TEXT,body TEXT);
            CREATE TABLE cp_memo(id TEXT,body_id TEXT,lifecycle TEXT,category TEXT,title TEXT);
            CREATE TABLE cp_memo_source(memo_id TEXT,source_id TEXT);
            CREATE TABLE local_archive_receipt(receipt_json TEXT);
        """)

    def excerpt(self, mid, body, start, source_hash=None, name="old memo.docx", namespace="historical_memo_original"):
        source_hash = source_hash or self.original_hash
        self.db.execute("INSERT INTO cp_body VALUES(?,?)", (mid, body))
        self.db.execute("INSERT INTO cp_memo VALUES(?,?,'active','internal',?)", (mid, mid, name))
        locator = json.dumps({"original_name": name, "paragraph_start": start})
        self.db.execute("INSERT INTO cp_source_record VALUES(?,?,?,?,?)",
                        (mid, namespace, f"{mid}-p{start}", locator, source_hash))
        self.db.execute("INSERT INTO cp_memo_source VALUES(?,?)", (mid, mid))

    def inspect(self, lines, marker="new word", **kwargs):
        extraction = {"body": "\n".join(lines), "source_sha256": digest(marker),
                      "supplemental_hashes": kwargs.pop("parts", {})}
        return local_word.propose(self.db, Path("renamed/new memo.docx"), extraction=extraction, **kwargs)

    def save(self, plan, delta_text, mid="revision", marked=None):
        item = {"body": delta_text, "word_review": {"paragraphs": marked or []}}
        version = local_word.reviewed_version(plan, item, 0)
        self.excerpt(mid, delta_text, 1, plan["source_sha256"], namespace="library-word-version")
        receipt = {"control": {"word_versions": [version]}, "items": [{"memo_id": mid}]}
        self.db.execute("INSERT INTO local_archive_receipt VALUES(?)", (json.dumps(receipt),))
        return version

    def test_legacy_split_archive_and_renamed_revision(self):
        self.excerpt("z", self.first + "\n" + self.second, 0)
        self.excerpt("a", self.prior, 10)
        result = self.inspect([self.first, self.second, self.revised, self.added])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["prior_memo_ids"], ["a", "z"])
        self.assertEqual([(x["kind"], x["paragraph"]) for x in result["changes"]],
                         [("revised", 3), ("uncovered", 4)])
        self.assertEqual(result["changes"][0]["old_text"], self.prior)
        self.assertFalse(result["removed"])
        version = self.save(result, self.revised + "\n" + self.added)
        self.assertEqual(version["covered"], [1, 2, 3, 4])
        self.assertEqual(version["revisions"][0]["prior_paragraph"], 3)
        again = self.inspect([self.first, self.second, self.revised, self.added], marker="only theme formatting")
        self.assertEqual(again["status"], "unchanged")
        stale = self.inspect([self.first, self.second, self.prior], marker="original old Word")
        self.assertEqual(stale["status"], "unchanged")

    def test_no_false_append_for_reorder_or_repeats(self):
        initial = self.inspect([self.first, self.second, self.second, self.prior])
        self.save(initial, "\n".join([self.first, self.second, self.second, self.prior]))
        moved = self.inspect([self.second, self.first, self.second, self.prior], marker="moved")
        self.assertEqual(moved["status"], "unchanged")
        extra = self.inspect([self.second, self.first, self.second, self.prior, self.second], marker="third")
        self.assertEqual([c["text"] for c in extra["changes"]], [self.second])
        fewer = self.inspect([self.first, self.prior], marker="removed")
        self.assertEqual(fewer["status"], "unchanged")
        self.assertEqual(len(fewer["removed"]), 2)

    def test_parts_and_summarized_editorial_coverage(self):
        p = self.inspect([self.first, self.prior], parts={"word/media/image1.png": digest("one")})
        v = self.save(p, "按编辑规则提炼的摘要。", marked=[2])
        self.assertEqual(v["covered"], [2])
        update = self.inspect([self.first, self.prior], marker="updated picture",
                              parts={"word/media/image1.png": digest("two")})
        self.assertEqual(update["status"], "matched")
        self.assertEqual(update["changes"], [])
        self.assertEqual(update["supplemental"]["modified"], ["word/media/image1.png"])

    def test_uncertain_and_independent(self):
        self.excerpt("one", self.first + "\n" + self.second, 0)
        self.excerpt("two", self.first + "\n" + self.second, 0, digest("another"))
        pending = self.inspect([self.first, self.second, self.added])
        self.assertEqual(pending["status"], "ambiguous")
        self.assertEqual(len(pending["candidates"]), 2)
        selected = self.inspect([self.first, self.second, self.added],
                                choice=pending["candidates"][0]["document_id"])
        self.assertEqual(selected["status"], "matched")
        independent = self.inspect([self.first, self.second, self.added], choice="new")
        self.assertEqual(independent["status"], "new")
        self.assertEqual(self.inspect(["关于年度铜矿新投资合同，矿场昨日披露全新报价方案。"])["status"], "new")

    def test_rephrased_single_paragraph_and_partial_legacy(self):
        self.excerpt("one", self.prior, 12)
        self.assertEqual(self.inspect([self.revised])["status"], "ambiguous")
        exact = self.inspect([self.first, self.prior], marker="original old Word")
        self.assertEqual(exact["status"], "unchanged")
        self.assertTrue(exact["legacy_partial"])


if __name__ == "__main__":
    unittest.main()

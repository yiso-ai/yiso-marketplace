"""Bind an editorial Word delta to its inspected source and immutable Library receipt."""
import json
from pathlib import Path
import re

import local_sources
import local_word


WORD_SUFFIXES = {".docx", ".docm", ".doc"}


def is_word(item):
    return "research_item" not in item and Path(item["source"]["private_locator"]).suffix.lower() in WORD_SUFFIXES


def review_fields(item):
    review = item.get("word_review", {})
    if not isinstance(review, dict) or set(review) - {"choice", "confirmation", "paragraphs", "parts", "against"}:
        raise ValueError("invalid word_review fields")
    if "word_review" in item and not is_word(item):
        raise ValueError("word_review requires a Word source")
    if "choice" in review or "confirmation" in review:
        if not all(isinstance(review.get(k), str) and review[k].strip() for k in ("choice", "confirmation")):
            raise ValueError("a Word identity override requires the user's choice and confirmation")
    if "against" in review:
        basis = review["against"]
        if (not isinstance(basis, dict) or set(basis) != {"document_id", "base_version"}
                or not isinstance(basis["document_id"], str)
                or not (basis["base_version"] is None or isinstance(basis["base_version"], str))):
            raise ValueError("invalid Word comparison baseline")
    for key, kind in (("paragraphs", int), ("parts", str)):
        values = review.get(key, [])
        if (not isinstance(values, list) or any(type(value) is not kind for value in values)
                or len(values) != len(set(values))):
            raise ValueError("invalid word_review." + key)
    return review


def bind_baseline(db, item, path, batch_id, extraction):
    review = review_fields(item)
    if not isinstance(batch_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", batch_id):
        raise ValueError("invalid batch ID")
    saved = local_sources.LIBRARY / "archive-history" / "requests" / f"request-{batch_id}.json"
    if saved.is_file():
        # Rebuilding the same reviewed request after commit must use its original
        # comparison head. The writer independently verifies the entire saved batch.
        with local_sources.safe_source(saved).open(encoding="utf-8") as stream:
            stored = json.load(stream)
        for old in stored["reviewed_batch"]["items"]:
            if old["source"] == item["source"]:
                basis = old.get("word_review", {}).get("against")
                if basis is not None:
                    item["word_review"] = {**review, "against": basis}
                return
    proposal = local_word.propose(db, path, choice=review.get("choice"), extraction=extraction)
    if proposal.get("source_sha256") != item["source"]["content_hash"]:
        raise ValueError("Word changed during preparation")
    if proposal["status"] != "ambiguous":
        basis = {key: proposal[key] for key in ("document_id", "base_version")}
        if "against" in review and proposal["status"] != "unchanged" and review["against"] != basis:
            raise ValueError("Word 审阅所用的库内版本已更新，请重新比对")
        item["word_review"] = {**review, "against": basis}


def body_lines(item, extraction):
    """Image transcription is attributed to the retained picture and reviewed region."""
    images = list(item.get("original_images", []))
    images.extend(v["original"] for v in item.get("original_vector_images", []))
    parts = set(item.get("word_review", {}).get("parts", []))
    part_hashes = extraction.get("supplemental_hashes", {})
    quoted = {"【图中摘录】" + row["source_quote"]
              for image in images
              if any(part_hashes.get(part) == image["image_sha256"] for part in parts)
              for row in image["research"]["search"] if row["source_quote"].strip()}
    return [line for line in item["body"].splitlines() if line.strip() and line not in quoted]


def supplemental_text(item, extraction):
    texts = extraction.get("supplemental_texts", {})
    return "\n".join(texts[part] for part in item.get("word_review", {}).get("parts", [])
                     if part in texts)


def plan(db, item, item_index, extraction):
    review = review_fields(item)
    source = item["source"]
    if extraction["source_sha256"] != source["content_hash"]:
        raise ValueError("Word changed since editorial review")
    proposal = local_word.propose(db, Path(source["private_locator"]), choice=review.get("choice"),
                                  extraction=extraction)
    status = proposal["status"]
    if status == "ambiguous":
        raise ValueError("Word 匹配不确定，请向用户确认候选文档或独立新文档：" +
                         json.dumps(proposal["candidates"], ensure_ascii=False))
    if status not in {"unchanged", "matched", "new"}:
        raise ValueError("unknown Word comparison status: " + status)
    basis = {key: proposal[key] for key in ("document_id", "base_version")}
    if status != "unchanged" and "against" in review and review["against"] != basis:
        raise ValueError("Word 审阅所用的库内版本已更新，请重新比对新增和修订内容")
    summary = {"status": status, "document_id": proposal["document_id"],
               "base_version": proposal.get("base_version"), "prior_memo_ids": proposal["prior_memo_ids"],
               "source_sha256": source["content_hash"], "legacy_partial": proposal["legacy_partial"],
               "removed_paragraphs": len(proposal["removed"])}
    if proposal.get("legacy_unmatched_paragraphs"):
        summary["legacy_unmatched_paragraphs"] = proposal["legacy_unmatched_paragraphs"]
    if status == "unchanged":
        return summary, None
    changes = proposal["changes"]
    by_number = {c["paragraph"]: c["text"] for c in changes}
    paragraphs = review.get("paragraphs", [])
    if any(n not in by_number for n in paragraphs):
        raise ValueError("word_review.paragraphs must refer to added, revised or previously uncovered paragraphs")
    supplemental = proposal.get("supplemental") or {}
    changed_parts = set(supplemental.get("added", [])) | set(supplemental.get("modified", []))
    parts = review.get("parts", [])
    if any(part not in changed_parts for part in parts):
        raise ValueError("word_review.parts must refer to reported added or modified supplemental parts")
    if status == "matched":
        retained = [v["image_sha256"] for v in item.get("original_images", [])]
        retained.extend(v["original"]["image_sha256"] for v in item.get("original_vector_images", []))
        if retained:
            changed_hashes = {extraction.get("supplemental_hashes", {}).get(part) for part in changed_parts}
            if not set(retained) <= changed_hashes:
                raise ValueError("增量 Word 只能申报本次新增或修改的独立图片")
        # A historical image/table may be present in a legacy Word whose unselected parts
        # have no stored fingerprint. Check previously archived assets for this document too.
        if proposal["prior_memo_ids"] and (retained or item.get("native_tables")):
            prior = json.dumps(proposal["prior_memo_ids"])
            if retained:
                existing = db.execute("""
                    SELECT image_sha256 FROM cp_memo_original_image WHERE memo_id IN (SELECT value FROM json_each(?))
                    UNION ALL
                    SELECT original_sha256 FROM cp_memo_vector_image WHERE memo_id IN (SELECT value FROM json_each(?))
                    """, (prior, prior))
                if set(retained).intersection(row[0] for row in existing):
                    raise ValueError("本 Word 的旧 Memo 已收录所申报的原图，请仅提交新增或修订图像")
            if item.get("native_tables"):
                old_tables = [json.loads(row[0]) for row in db.execute(
                    "SELECT content_json FROM cp_native_table WHERE memo_id IN (SELECT value FROM json_each(?))",
                    (prior,))]
                if any(table["content"] in old_tables for table in item["native_tables"]):
                    raise ValueError("本 Word 的旧 Memo 已收录相同原生表格，请仅提交变动内容")
        if item["category"] == "internal" and not item.get("image_record"):
            # Include only the reviewed current text. An unchanged heading or attribution
            # belongs in the memo title/metadata, not repeated inside every new delta.
            delta = "\n".join(by_number[n] for n in (sorted(paragraphs) if paragraphs else sorted(by_number)))
            delta += "\n" + supplemental_text(item, extraction)
            offset = 0
            for line in body_lines(item, extraction):
                start = delta.find(line, offset)
                if start < 0:
                    raise ValueError("Word 增量正文包含旧段落或超出本次变更；请用 compare-word 核对原文")
                offset = start + len(line)
        elif not (paragraphs or parts):
            raise ValueError("Word 增量摘要或图片记录须用 word_review.paragraphs / parts 标出本次变化的依据")
        elif item.get("image_record") and any(line not in by_number.values() for line in
                                              item["image_record"]["original_text"].splitlines() if line.strip()):
            raise ValueError("单图记录包含旧正文；请用新增图片的正式申报和带出处的图中摘录记录增量")
    version = local_word.reviewed_version(proposal, item, item_index)
    summary["change_counts"] = {kind: sum(c["kind"] == kind for c in changes)
                                for kind in ("added", "revised", "uncovered")}
    return summary, version


def version_source(item, version):
    # The historical writer uses (namespace, record_key) as an immutable identity.
    # Preserve the actual path as provenance while allowing later versions at that path.
    return {**item, "source": {**item["source"], "namespace": "library-word-version",
                               "record_key": version["document_id"] + "/" + version["source_sha256"]}}


def verify_receipt(db, receipt):
    for version in (receipt.get("control") or {}).get("word_versions", []):
        index = version["item_index"]
        if type(index) is not int or not 0 <= index < len(receipt["items"]):
            raise ValueError("Word version points outside the receipt")
        item = receipt["items"][index]
        bound = db.execute("""SELECT s.namespace,s.record_key,s.content_hash
                              FROM cp_source_record s JOIN cp_memo_source ms ON ms.source_id=s.id
                              WHERE s.id=? AND ms.memo_id=?""", (item["source_id"], item["memo_id"])).fetchone()
        expected = ("library-word-version", version["document_id"] + "/" + version["source_sha256"],
                    version["source_sha256"])
        if bound is None or tuple(bound) != expected:
            raise ValueError("Word version and committed source or memo differ")

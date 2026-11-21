"""Archive reviewed local materials to the existing, sibling Research Hub Library."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile


sys.dont_write_bytecode = True
import local_sources
from local_image_inputs import direct_inputs

SKILL = local_sources.SKILL
LIBRARY = local_sources.LIBRARY
DATABASE = local_sources.DATABASE
HISTORY = LIBRARY / "archive-history"
RUNTIME = SKILL / "production" / "runtime"
AUTHORITY = "info-hub-library-local-v1"
EXPECTED = {
    "database_uuid": "c881b3f0-a2f0-451c-88ec-c003d238d3a2",
    "schema_sha256": "54cd8ee3dda9e4435f56b2661b5ed4145a9232befb75ac2b9462429ea07fcdce",
}

sys.path.insert(0, str(RUNTIME))
import archiver as archive
import controlled_writer as original
import local_industry as industry
import local_research
import word_intake


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_target(path):
    require(SKILL.name == "library" and SKILL.parent.name == "Skills"
            and SKILL.parents[1].name in {"Research-Master", "Research-Publish"},
            "expected Skills/library in the Master or Publish package")
    db = archive.safe(path)
    require(db == DATABASE and db.stat().st_nlink == 1, "target is not the dedicated Hub Library file")
    return db


def bound_control(connection):
    """The old D: writer remains paused. This location uses its own per-batch authority."""
    identity = archive.target_identity(connection)
    require(identity["scope"] == "local-production-v1"
            and all(identity[k] == v for k, v in EXPECTED.items()),
            "Library database UUID or supported schema differs")
    row = connection.execute("SELECT mode, generation, code_json FROM local_archive_control WHERE id=1").fetchone()
    require(row and row["mode"] == "paused", "historical writer is active or its control row is missing")
    require(json.loads(row["code_json"]) == original.code_identity(), "historical runtime fingerprint differs")
    return row["generation"]


def read_connection():
    return archive.connect(checked_target(DATABASE), ro=True)


def valid_batch_id(batch_id):
    require(isinstance(batch_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", batch_id), "invalid batch ID")
    return batch_id


def request_path(batch_id):
    return HISTORY / "requests" / ("request-" + valid_batch_id(batch_id) + ".json")


def validated_source_path(value):
    path = local_sources.safe_source(value)
    require(path.is_file() and not path.is_relative_to(LIBRARY), "an input must be a source file outside Library")
    return path


def validate_excerpt(item, path, extraction=None):
    if (item["category"] != "internal" or path.suffix.lower() not in {".md", ".txt"} | word_intake.WORD_SUFFIXES
            or item.get("image_record")):
        return
    extraction = extraction if extraction is not None else local_sources.extract(path)
    require(extraction["source_sha256"] == item["source"]["content_hash"],
            f"source changed during text verification: {path}")
    extracted = extraction["body"].replace("\r\n", "\n").replace("\r", "\n")
    excerpt = item["body"].replace("\r\n", "\n").replace("\r", "\n")
    # Whole passages or ordered, individually faithful lines from one source.
    if excerpt in extracted:
        return
    extracted += "\n" + word_intake.supplemental_text(item, extraction)
    offset = 0
    for line in word_intake.body_lines(item, extraction):
        start = extracted.find(line, offset)
        require(start >= 0, f"internal text differs from source or order: {path.name}")
        offset = start + len(line)


def validate_inputs(batch, scopes, previews):
    require(batch.get("protocol") in {"cp-local-batch-v2", "cp-local-batch-v3"},
            "an explicit reviewed primary subject is required (batch v2/v3)")
    payload_hash = industry.validate(batch)
    roots = [local_sources.safe_source(s) for s in scopes]
    sources = {}
    inspected = {}
    split_sources = set()
    split_bodies = set()
    seen_bodies = {}
    word_extractions = {}
    expected_previews = set()
    for item in batch["items"]:
        source = item["source"]
        split = "research_item" in item
        locator = item["input_file"] if split else source["private_locator"]
        require(Path(locator).is_absolute(), "input must be an absolute local path")
        path = validated_source_path(locator)
        require(any(path == root or (root.is_dir() and path.is_relative_to(root)) for root in roots),
                f"material outside the requested source paths: {path}")
        if not split:
            require(source["source_type"] == "local_file" and source["namespace"] == "library-local-file"
                    and source["record_key"] == path.as_uri(),
                    f"source namespace or canonical file URI differs: {path}")
        if path not in inspected:
            extraction = local_sources.extract(path) if path.suffix.lower() in word_intake.WORD_SUFFIXES else None
            sha = extraction["source_sha256"] if extraction is not None else local_sources.read_source(path)[0]
            inspected[path] = sha, extraction
        sha, extraction = inspected[path]
        require(sha == source["content_hash"], f"source changed since review: {path}")
        require(sha not in sources or (split and sha in split_sources),
                "duplicate source bytes require distinct research matters throughout the batch")
        if split:
            split_sources.add(sha)
        body_hash = archive.sha(item["body"])
        if split:
            identity = sha, body_hash
            require(identity not in split_bodies, "the same research text appears under two different matter keys")
            split_bodies.add(identity)
        if body_hash in seen_bodies:
            previous = seen_bodies[body_hash]
            require(local_research.attribution(item) == local_research.attribution(previous),
                    "identical memo text has conflicting source credits")
        seen_bodies[body_hash] = item
        word_intake.review_fields(item)
        if extraction is not None:
            word_extractions[sha] = extraction
        validate_excerpt(item, path, extraction)
        industry.check_source_evidence(item, path, extraction)
        sources[sha] = path
        for vector in item.get("original_vector_images", []):
            expected_previews.add(vector["preview"]["preview_sha256"])
    preview_files = {}
    for name in previews:
        path = validated_source_path(name)
        sha, _ = local_sources.read_source(path)
        preview_files[sha] = path
    require(preview_files.keys() == expected_previews, "supply exactly the reviewed vector preview PNGs with --preview")
    return payload_hash, sources, preview_files, word_extractions


def matches_memo(connection, item, row):
    """The same source file may have several reviewed excerpts. Match this specific memo."""
    if item["body"] != row["body"] or archive.sha(item["body"]) != row["sha256"]:
        return False
    if any(item[name] != row[name] for name in ("title", "category", "material_date", "institution")):
        return False
    mid = row["memo_id"]
    try:
        archive.primary_subject.verify(connection, item["primary_subject"], mid)
        archive.extensions.duplicate_check(connection, item, mid)
    except ValueError:
        return False
    for group, table, field in (("companies", "cp_memo_company", "company_id"),
                                ("themes", "cp_memo_theme", "theme_id")):
        fields = [field, "reason"]
        if group == "companies":
            fields.append("scope_kind")
        columns = ", ".join("a." + key for key in fields)
        records = connection.execute(
            f"SELECT {columns}, e.quote, e.start_offset, e.end_offset, e.claim_kind FROM {table} a "
            "JOIN cp_evidence e ON e.id=a.evidence_id AND e.memo_id=a.memo_id "
            "WHERE a.memo_id=? AND a.review_status='approved' AND e.review_status='approved' AND e.body_id=?",
            (mid, row["body_id"]),
        )
        evidence = ("quote", "start_offset", "end_offset", "claim_kind")
        wanted = [tuple(a[k] for k in fields) + tuple(a["evidence"][k] for k in evidence) for a in item[group]]
        if sorted(wanted) != sorted(tuple(r) for r in records):
            return False
    if any(item.get(k) for k in ("new_companies", "original_images", "original_vector_images", "image_record")):
        # These plans include reviewed provenance beyond text and subjects. Only recognize a
        # byte-equivalent approved plan from the original source; otherwise keep it for review.
        source = {k: row[k] for k in item["source"]}
        meta = json.loads(row["metadata_json"])
        if "review" not in meta or meta.get("local_item_stable_sha256") != archive.stable_item_hash(
                {**item, "source": source, "review": meta["review"]}):
            return False
    return True


def coverage(connection, item):
    source = item["source"]
    split = "research_item" in item
    existing = connection.execute(
        "SELECT content_hash FROM cp_source_record WHERE "
        "(namespace=? AND record_key=? COLLATE NOCASE) OR (? AND private_locator=? COLLATE NOCASE)",
        (source["namespace"], source["record_key"], not split, source["private_locator"]),
    ).fetchall()
    require(all(r[0] == source["content_hash"] for r in existing),
            f"previous source at this path changed; review the revision separately: {source['private_locator']}")
    rows = connection.execute(
        "SELECT s.*, m.id AS memo_id, m.title, m.category, m.material_date, m.institution, "
        "b.id AS body_id, b.body, b.sha256 FROM cp_source_record s "
        "JOIN cp_memo_source ms ON ms.source_id=s.id JOIN cp_memo m ON m.id=ms.memo_id "
        "JOIN cp_body b ON b.id=m.body_id WHERE s.content_hash=? AND m.lifecycle='active' ORDER BY m.id",
        (source["content_hash"],),
    ).fetchall()
    core = industry.core_item(item)
    if split:
        keyed = [r for r in rows if (r["namespace"], r["record_key"]) ==
                 (source["namespace"], source["record_key"])]
        require(not existing or keyed, "existing research matter has no active memo: " + item["research_item"])
        if keyed:
            rows = keyed
        else:
            same_body = [r for r in rows if r["body"] == item["body"]]
            require(all(r["namespace"] != local_research.NAMESPACE for r in same_body),
                    "existing research matter has the same text under a different key")
            rows = same_body  # Earlier whole-file entries may already cover this exact Memo.
    matched = sorted({row["memo_id"] for row in rows
                      if local_research.credit_matches(item, row) and matches_memo(connection, core, row)})
    identity = item["research_item"] if split else source["private_locator"]
    require(not rows or len(matched) == 1,
            f"existing memo content, subject or source credit differs or is ambiguous for {identity}: "
            + ", ".join(sorted({row["memo_id"] for row in rows})))
    return matched


def select_items(connection, batch, word_extractions):
    included, skipped, versions, documents = [], {}, [], []
    for i, item in enumerate(batch["items"]):
        source = item["source"]
        if not word_intake.is_word(item):
            existing = coverage(connection, item)
            if existing:
                skipped[source["record_key"]] = existing
            else:
                local_research.check_duplicate_credit(connection, item)
                included.append(i)
            continue
        summary, version = word_intake.plan(connection, item, len(included), word_extractions[source["content_hash"]])
        documents.append(summary)
        if version is None:
            local_research.check_covered_word_titles(connection, item, summary)
            skipped[source["record_key"]] = summary["prior_memo_ids"]
            continue
        local_research.check_duplicate_credit(connection, item)
        for prior in versions:
            if (prior["document_id"] == version["document_id"]
                    or word_intake.local_word.possibly_same_batch(prior, version)):
                raise ValueError("本批含可能属于同一份 Word 的多个版本，请按版本先后分批归档并重新比对")
        versions.append(version)
        included.append(i)
    result = {"included": included, "already_archived": skipped}
    if documents:
        result.update(word_documents=documents, word_versions=versions)
    return result


def design_request(connection, batch, payload_hash, word_extractions, prior):
    generation = bound_control(connection)
    batch_id = batch["batch_id"]
    path = request_path(batch_id)
    base = {"authority": AUTHORITY, "target_path": str(DATABASE), **EXPECTED,
            "historical_paused_generation": generation, "batch_id": batch_id,
            "reviewed_batch_sha256": payload_hash}
    if path.exists():
        # A request wraps up to 16 MiB of normalized batch JSON plus the selection and coverage.
        # Do not reapply the raw batch-file size limit to the larger immutable envelope.
        with archive.safe(path).open(encoding="utf-8") as stream:
            saved = json.load(stream)
        require(all(saved.get(k) == v for k, v in base.items()) and saved.get("reviewed_batch") == batch,
                "this request ID already belongs to different content, authority, or target")
        included = saved["included"]
        skipped = saved["already_archived"]
        require(isinstance(included, list) and len(included) == len(set(included))
                and all(type(i) is int and 0 <= i < len(batch["items"]) for i in included),
                "invalid stored request selection")
        omitted = {batch["items"][i]["source"]["record_key"] for i in range(len(batch["items"])) if i not in included}
        require(omitted == set(skipped), "stored request coverage differs")
        if not prior:
            selection = select_items(connection, batch, word_extractions)
            require(all(saved.get(k) == v for k, v in selection.items()),
                    "归档前资料或匹配基准已变化，请重新比对并生成新批次")
        return saved
    old = connection.execute("SELECT 1 FROM local_archive_receipt WHERE batch_id=?", (batch_id,)).fetchone()
    require(not old, "batch ID already belongs to a different archived operation")
    return {**base, **select_items(connection, batch, word_extractions), "reviewed_batch": batch}


def ensure_directory(path):
    if not path.exists():
        archive.safe(path.parent)
        path.mkdir(exist_ok=True)
    archive.safe(path)
    require(path.is_dir(), f"directory required: {path}")


def publish_request(path, content):
    """Publish an immutable review request without overwriting another writer."""
    expected_hash = archive.sha(content)
    if not path.exists():
        with tempfile.NamedTemporaryFile(prefix=".request-", dir=archive.safe(path.parent), delete=False) as out:
            tmp = Path(out.name)
            try:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            except BaseException:
                out.close()
                tmp.unlink(missing_ok=True)
                raise
        try:
            require(digest(tmp) == expected_hash, f"request changed during capture: {path}")
            try:
                if os.name == "nt":
                    tmp.rename(path)  # On Windows rename fails when the destination exists.
                else:
                    os.link(tmp, path)
            except FileExistsError:
                pass
        finally:
            tmp.unlink(missing_ok=True)
    require(digest(archive.safe(path)) == expected_hash, f"immutable review request conflicts: {path}")


def save_request(request):
    for folder in [HISTORY, HISTORY / "requests", HISTORY / "backups", HISTORY / "receipts"]:
        ensure_directory(folder)
    publish_request(request_path(request["batch_id"]), archive.canonical(request).encode())


def selected_batch(request):
    batch = request["reviewed_batch"]
    items = [batch["items"][i] for i in request["included"]]
    for version in request.get("word_versions", []):
        idx = version["item_index"]
        items[idx] = word_intake.version_source(items[idx], version)
    return {**batch, "items": items}


def receipt(connection, batch_id):
    valid_batch_id(batch_id)
    row = connection.execute("SELECT payload_sha256,receipt_json FROM local_archive_receipt WHERE batch_id=?",
                             (batch_id,)).fetchone()
    if row is None:
        return None
    result = json.loads(row["receipt_json"])
    require(result["batch_id"] == batch_id and result["payload_sha256"] == row["payload_sha256"],
            "receipt payload or batch ID mismatch")
    result = archive.verify_receipt(connection, result)
    industry.verify_receipt(connection, result)
    word_intake.verify_receipt(connection, result)
    local_research.verify_credits(connection, result)
    return result


def process(args):
    batch = archive.read_json(args.batch)
    payload_hash, sources, previews, word_extractions = validate_inputs(batch, args.source, args.preview)
    with closing(read_connection()) as connection:
        prior = receipt(connection, batch["batch_id"])
        request = design_request(connection, batch, payload_hash, word_extractions, prior)
        reviewed = selected_batch(request)
        plans = industry.assignments(connection, reviewed) if not prior else (prior.get("control") or {}).get("industry_assignments", [])
        research_preview = local_research.preview(connection, request, prior) if args.command == "check" else []
    selected = industry.core_batch(reviewed)
    summary = {"batch_id": batch["batch_id"], "reviewed_batch_sha256": payload_hash,
               "already_archived": request["already_archived"], "selected": len(selected["items"]),
               "industries": industry.summary(plans)}
    if "word_documents" in request:
        summary["word_documents"] = request["word_documents"]
    if research_preview:
        summary["research_items"] = research_preview
    if not selected["items"]:
        return {**summary, "status": "already_archived", "created": 0}
    selected_hash = archive.validate(selected)
    if prior:
        require(prior["payload_sha256"] == selected_hash, "previous receipt has a different selected batch")
        require((prior.get("control") or {}).get("request_sha256") == archive.sha(archive.canonical(request)),
                "the saved review and industry plan differ from the original Library receipt")
    if args.command == "check":
        return {**summary, "status": "already_committed" if prior else "checked"}
    for expected in {item["source"]["content_hash"] for item in selected["items"]}:
        sha, _ = local_sources.read_source(sources[expected])
        require(sha == expected, f"source changed before submission: {sources[expected]}")
    save_request(request)
    request_hash = archive.sha(archive.canonical(request))
    adapter_hash = digest(Path(__file__))

    def guard(connection):
        require(bound_control(connection) == request["historical_paused_generation"], "historical writer state changed")
        require(digest(request_path(batch["batch_id"])) == request_hash, "local per-batch authorization changed")
        old = connection.execute("SELECT payload_sha256 FROM local_archive_receipt WHERE batch_id=?",
                                 (batch["batch_id"],)).fetchone()
        if old:
            require(old[0] == selected_hash, "batch ID collision")
        else:
            selection = select_items(connection, batch, word_extractions)
            require(all(request.get(k) == v for k, v in selection.items()),
                    "资料或 Word 匹配基准已被另一批次更新，请重新比对后提交")
        context = {"authority": AUTHORITY, "target_path": str(DATABASE), "request_sha256": request_hash,
                   "selected_batch_sha256": selected_hash, "adapter_sha256": adapter_hash,
                   "historical_mode": "paused", "historical_generation": request["historical_paused_generation"]}
        if request.get("word_versions"):
            context["word_versions"] = request["word_versions"]
        if not old:
            credits = {local_research.source_id(item): local_research.attribution(item)
                       for item in reviewed["items"]}
            context["source_credits"] = credits
            if connection.in_transaction:
                local_research.install_credits(connection, credits)
            current = industry.assignments(connection, reviewed)
            require(current == plans, "company registry changed since preflight; retry after checking coverage")
            if current:
                context["industry_assignments"] = current
                if connection.in_transaction:
                    industry.install(connection, current, selected)
        return context

    with direct_inputs(archive.image_assets, DATABASE, sources, previews):
        result = archive._apply_engine(
            DATABASE, selected, EXPECTED, checked_target, guard,
            backup_root=HISTORY / "backups", receipt_root=HISTORY / "receipts",
        )
    replayed = bool(result.get("replayed"))
    with closing(read_connection()) as connection:
        industry.verify_receipt(connection, result)
        word_intake.verify_receipt(connection, result)
        local_research.verify_credits(connection, result)
    return {**summary, "status": "replayed" if replayed else "committed",
            "created": 0 if replayed else sum(x["action"] == "created" for x in result["items"]),
            "items": [{k: x[k] for k in ("memo_id", "source_id", "action")} for x in result["items"]],
            "backup_bundle": result["image_backup_bundle"]["path"],
            "receipt": str(HISTORY / "receipts" / ("receipt-" + batch["batch_id"] + ".json"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("receipt").add_argument("--batch-id", required=True)
    for name in ("check", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--batch", required=True)
        command.add_argument("--source", action="append", required=True, help="user-selected input file or directory; repeatable")
        command.add_argument("--preview", action="append", default=[], help="reviewed local vector preview PNG; repeatable")
    args = parser.parse_args()
    if args.command in {"check", "apply"}:
        result = process(args)
    else:
        with closing(read_connection()) as connection:
            generation = bound_control(connection)
            if args.command == "receipt":
                result = receipt(connection, args.batch_id)
                if result is None:
                    pending = request_path(args.batch_id).is_file()
                    result = {"batch_id": args.batch_id, "status": "pending" if pending else "no_receipt"}
                else:
                    result = {"status": "verified", **{k: result[k] for k in ("batch_id", "payload_sha256", "control")},
                              "memo_ids": [item["memo_id"] for item in result["items"]],
                              "industries": industry.summary((result.get("control") or {}).get("industry_assignments", []))}
            else:
                result = {"library": str(LIBRARY), "authority": AUTHORITY, **EXPECTED,
                          "historical_mode": "paused", "historical_generation": generation,
                          "memos": connection.execute("SELECT count(*) FROM cp_memo WHERE lifecycle='active'").fetchone()[0],
                          "receipts": connection.execute("SELECT count(*) FROM local_archive_receipt").fetchone()[0]}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise SystemExit(str(exc))

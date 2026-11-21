"""Bind reviewed SW2021 industry choices to newly archived Library companies."""

import re

import archiver as archive
import local_research
import local_sources

OFFICIAL_BASES = {"annual_report", "company_website"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def core_item(item):
    """The original writer accepts the historical entity contract."""
    adapter_fields = {"word_review", "research_item", "input_file", "author", *local_research.SOURCE_FIELDS}
    if adapter_fields.intersection(item):
        item = {key: value for key, value in item.items() if key not in adapter_fields}
    if not item.get("new_companies"):
        return item
    companies = [{k: v for k, v in proposal.items() if k != "industry"}
                 for proposal in item["new_companies"]]
    return {**item, "new_companies": companies}


def core_batch(batch):
    return {**batch, "items": [core_item(item) for item in batch["items"]]}


def evidence_matches(evidence, text):
    start, end = evidence["start_offset"], evidence["end_offset"]
    return 0 <= start < end <= len(text) and text[start:end] == evidence["quote"]


def validate(batch):
    archive.validate(core_batch(batch))
    for item in batch["items"]:
        local_research.attribution(item)
        if ("input_file" in item) != ("research_item" in item):
            raise ValueError("a research matter requires its reviewed input_file")
        if "research_item" in item:
            if item["source"] != local_research.research_source(item, item["source"]["content_hash"]):
                raise ValueError("research source differs from the reviewed matter or attribution")
            if "word_review" in item:
                raise ValueError("choose either a research matter or a whole-document Word revision")
        for company in item.get("new_companies", []):
            if "industry" not in company:
                continue  # Whether this is a new company is a live database decision.
            plan = company["industry"]
            require(isinstance(plan, dict) and {"code", "reason", "evidence"} <= set(plan)
                    and set(plan) <= {"code", "reason", "evidence", "basis", "source_title"},
                    "industry requires code, reason, evidence and an applicable source basis")
            require(isinstance(plan["code"], str) and re.fullmatch(r"[0-9]{6}", plan["code"]),
                    "expected a six-digit SW2021 industry code")
            require(isinstance(plan["reason"], str) and plan["reason"].strip(),
                    "industry assignment requires a main-business rationale")
            basis = plan.get("basis", "memo")
            ev = plan["evidence"]
            if basis in OFFICIAL_BASES:
                title = plan.get("source_title")
                require(isinstance(title, str) and title.strip() and not local_research.TITLE_LOCATOR.search(title),
                        "official industry evidence requires the plain-text annual report or company page title")
                require(isinstance(ev, dict) and set(ev) == {"quote"} and isinstance(ev["quote"], str)
                        and ev["quote"].strip(), "official industry evidence requires a reviewed quotation")
                continue
            require(basis in ("memo", "source") and "source_title" not in plan,
                    "industry evidence basis must be memo, source, annual_report or company_website")
            require(isinstance(ev, dict) and set(ev) == {"quote", "start_offset", "end_offset"}
                    and isinstance(ev["quote"], str) and ev["quote"].strip()
                    and type(ev["start_offset"]) is int and type(ev["end_offset"]) is int
                    and 0 <= ev["start_offset"] < ev["end_offset"],
                    "industry evidence needs an exact quotation and Unicode offsets")
            if basis == "memo":
                require(evidence_matches(ev, item["body"]), "industry evidence differs from the reviewed memo")
    return archive.sha(archive.canonical(batch))


def source_text(item, path, extraction=None):
    if any(c.get("industry", {}).get("basis") == "source" for c in item.get("new_companies", [])):
        result = extraction if extraction is not None else local_sources.extract(path)
        require(result["source_sha256"] == item["source"]["content_hash"],
                "source changed during industry evidence verification")
        return result["body"]
    return None


def check_source_evidence(item, path, extraction=None):
    text = source_text(item, path, extraction)
    if text is not None:
        for company in item["new_companies"]:
            plan = company.get("industry", {})
            if plan.get("basis") == "source":
                require(evidence_matches(plan["evidence"], text), "industry evidence differs from the source text")


def assignments(db, batch):
    """Return exactly one choice per entity that is absent in this transaction."""
    if not any(item.get("new_companies") for item in batch["items"]):
        return []
    catalog = local_sources.industry_catalog(db)
    proposals, chosen = {}, {}
    for item in batch["items"]:
        for company in item.get("new_companies", []):
            cid = company["company_id"]
            proposals.setdefault(cid, company)
            plan = company.get("industry")
            if plan is None:
                continue
            node = catalog.get(plan["code"])
            require(node is not None, f"unknown SW2021 industry code {plan['code']}")
            if node["level"] == 3:
                node = catalog[node["path"][1]["code"]]
            path = node["path"]
            prior = chosen.get(cid)
            if prior:
                if prior["path"][:len(path)] == path:
                    continue
                require(path[:len(prior["path"])] == prior["path"],
                        f"conflicting industry choices for {company['name']}")
            chosen[cid] = {"company_id": cid, "company_name": company["name"], "leaf_id": node["id"],
                           "level": node["level"], "code": node["code"], "path": path, "reason": plan["reason"],
                           "basis": plan.get("basis", "memo"), "evidence": plan["evidence"],
                           "source": item["source"], "body_sha256": archive.sha(item["body"]),
                           "reviewer": company["review"]["reviewer"], "reviewed_at": company["review"]["reviewed_at"]}
            if "source_title" in plan:
                chosen[cid]["source_title"] = plan["source_title"]
    new = []
    for cid, proposal in proposals.items():
        spec = chosen.get(cid)
        if db.execute("SELECT 1 FROM cp_company WHERE id=?", (cid,)).fetchone():
            if spec:
                current = db.execute(
                    """SELECT n.id,n.code FROM cp_company_industry i JOIN cp_industry_node n ON n.id=i.leaf_id
                       WHERE i.company_id=? AND i.review_status='approved' AND i.valid_to IS NULL""", (cid,)).fetchone()
                old = catalog.get(current[1]) if current else None
                require(old and old["id"] == current[0] and old["path"][:spec["level"]] == spec["path"],
                        f"existing company industry differs or is unassigned; review separately: {proposal['name']}")
            continue
        require(spec, f"new company requires a reviewed SW2021 level-2 or level-1 industry: {proposal['name']}")
        new.append(spec)
    return new


def row_values(spec):
    source = archive.canonical(spec)
    key = archive.sha(spec["company_id"] + "\x00" + spec["leaf_id"] + "\x00" + spec["source"]["content_hash"])
    mapping_id, review_id = "local-industry-" + key, "local-industry-review-" + key
    mapping = (mapping_id, spec["company_id"], spec["leaf_id"], "research_mapping", source,
               spec["reviewed_at"][:10], "approved", spec["reviewer"], spec["reviewed_at"])
    review = (review_id, spec["company_id"], spec["company_id"], spec["leaf_id"], "research_mapping",
              "approved", spec["reason"], source, spec["reviewer"], spec["reviewed_at"])
    return mapping, review


MAP_COLUMNS = "id, company_id, leaf_id, mapping_kind, source, valid_from, review_status, reviewed_by, reviewed_at"
REVIEW_COLUMNS = "id, legacy_company_id, company_id, leaf_id, mapping_kind, review_status, reason, source_json, reviewed_by, reviewed_at"


def install(db, specs, batch):
    """Called from the pinned engine's state guard after BEGIN IMMEDIATE."""
    require(db.in_transaction, "industry and company must use the memo write transaction")
    plans = {plan["company_id"]: plan for item in batch["items"] for plan in item.get("new_companies", [])}
    for spec in specs:
        # The pinned engine uses this same idempotent entity writer later in this transaction.
        archive.new_entities.upsert(db, {"new_companies": [plans[spec["company_id"]]]})
        mapping, review = row_values(spec)
        db.execute(f"INSERT INTO cp_industry_review ({REVIEW_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)", review)
        db.execute(f"INSERT INTO cp_company_industry ({MAP_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)", mapping)
    verify_rows(db, specs)


def verify_rows(db, specs):
    for spec in specs:
        mapping, review = row_values(spec)
        for table, columns, expected in (("cp_company_industry", MAP_COLUMNS, mapping),
                                         ("cp_industry_review", REVIEW_COLUMNS, review)):
            row = db.execute(f"SELECT {columns} FROM {table} WHERE id=?", (expected[0],)).fetchone()
            require(row is not None and tuple(row) == expected, f"industry receipt differs from {table}: {expected[0]}")


def verify_receipt(db, receipt):
    specs = (receipt.get("control") or {}).get("industry_assignments", [])
    verify_rows(db, specs)
    receipt_links = {(item["memo_id"], item["source_id"]) for item in receipt["items"]}
    for spec in specs:
        s = spec["source"]
        rows = db.execute(
            """SELECT m.id,s.id,b.body FROM cp_source_record s
               JOIN cp_memo_source ms ON ms.source_id=s.id JOIN cp_memo m ON m.id=ms.memo_id
               JOIN cp_body b ON b.id=m.body_id
               JOIN cp_memo_company a ON a.memo_id=m.id AND a.company_id=?
               WHERE s.namespace=? AND s.record_key=? AND s.content_hash=? AND b.sha256=?
               AND a.review_status='approved' AND a.scope_kind IN ('primary','substantive')""",
            (spec["company_id"],s["namespace"],s["record_key"],s["content_hash"],spec["body_sha256"]))
        require(any((mid, sid) in receipt_links and
                    (spec["basis"] in {"source", *OFFICIAL_BASES} or evidence_matches(spec["evidence"], body))
                    for mid, sid, body in rows), "industry receipt source or memo evidence mismatch")
    return specs


def summary(specs):
    rows = []
    for spec in specs:
        row = {key: spec[key] for key in ("company_id", "company_name", "code", "path", "basis", "reason")}
        row["level"] = len(spec["path"])
        if "source_title" in spec:
            row["source_title"] = spec["source_title"]
        rows.append(row)
    return rows

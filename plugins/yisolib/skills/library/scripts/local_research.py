"""Source credits and independent research matters in the Hub's local input adapter."""
import json
import re

import archiver as archive


NAMESPACE = "library-research-item"
CATEGORIES = {"internal": "内部研究", "sellside": "卖方研究", "news": "新闻及报道"}
SOURCE_DIMENSIONS = ("source_entity_type", "material_type", "publication_channel")
MATERIAL_TITLE_FIELDS = ("intake_material_title", "original_material_title")
SOURCE_FIELDS = (*SOURCE_DIMENSIONS, *MATERIAL_TITLE_FIELDS)
# Slashes in dates and research subjects (such as AI/ML) are ordinary title text.
TITLE_LOCATOR = re.compile(r"""
    [\x00-\x1f\x7f\u2028\u2029\\]
    | [a-z][a-z0-9+.-]*:// | \b(?:file|mailto|outlook|onedrive|smb): | \bwww\.
    | \b[a-z]:/ | (?:^|[\s(《"'=])(?:/|\.{1,2}/|~/)(?=\S)
    | [\w.-]+/[^\s/]+\.(?:pdf|docx?|docm|xlsx?|pptx?|png|jpe?g|webp|eml|msg|html?|txt|md)\b
    | \b[\w.-]+\.(?:com|org|net|cn|io|co|gov|edu)/(?=\S)
    | \[[^\]]*\](?:\(|\[[^\]]*\]) | </?[a-z][\w:-]*(?:\s[^<>]*)?/?>
    """, re.IGNORECASE | re.VERBOSE)
ENTITY_TYPES = {
    "内部团队", "卖方机构", "公司", "媒体", "独立研究者", "研究者", "第三方研究机构",
    "行业专家", "行业组织", "监管机构", "政府机构", "交易所", "学术机构", "投资机构",
}
MATERIAL_TYPES = {
    "研究分析", "调研访谈纪要", "会议纪要", "公司公告", "财务报告", "经营数据披露",
    "业绩会实录", "管理层交流", "产品发布", "新闻报道", "资讯综述", "个人评论",
    "监管文件", "统计数据",
}


def prepare_source_fields(item):
    """Keep old requests reproducible and normalize new reviews before hashing."""
    if not any(field in item for field in SOURCE_FIELDS):
        return
    fields = (*SOURCE_DIMENSIONS, "institution", "author")
    # Do not add keys to a reviewed batch prepared with the previous contract.
    if any(field in item for field in MATERIAL_TITLE_FIELDS):
        fields += MATERIAL_TITLE_FIELDS
    for field in fields:
        value = item.get(field)
        item[field] = (value.strip() or None) if isinstance(value, str) else value
    channel = item["publication_channel"]
    if isinstance(channel, str):
        aliases = {"x": "X", "twitter": "X", "公众号": "微信公众号", "wechat official account": "微信公众号"}
        item["publication_channel"] = aliases.get(channel.casefold(), channel)
    if "category" not in item:
        item["category"] = {"内部团队": "internal", "卖方机构": "sellside"}.get(
            item["source_entity_type"], "news")


def attribution(item):
    values = {key: item.get(key) for key in ("category", "institution", "author", *SOURCE_FIELDS)}
    if values["category"] not in CATEGORIES:
        raise ValueError("invalid research source category")
    for field in ("institution", "author", *SOURCE_FIELDS):
        value = values[field]
        if value is not None and (not isinstance(value, str) or not value.strip() or "\x00" in value):
            raise ValueError(f"{field} must be a known source credit or null")
    for field, choices in (("source_entity_type", ENTITY_TYPES), ("material_type", MATERIAL_TYPES)):
        if values[field] is not None and values[field] not in choices:
            raise ValueError(f"invalid {field}: {values[field]}")
    for field in MATERIAL_TITLE_FIELDS:
        if values[field] is not None and TITLE_LOCATOR.search(values[field]):
            raise ValueError(f"{field} requires a plain-text title without links or paths")
    return values


def description(credit):
    if any(field in credit for field in SOURCE_DIMENSIONS):
        parts = [credit[field] for field in (*SOURCE_DIMENSIONS, "institution", "author") if credit.get(field)]
        return " · ".join(parts) or "出处未注明"
    return " · ".join(x for x in (CATEGORIES[credit["category"]], credit["institution"], credit["author"]) if x)


def research_source(item, digest):
    key = item["research_item"]
    if not isinstance(key, str) or len(key) > 100 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", key):
        raise ValueError("research_item requires a stable lowercase research matter identifier")
    credit = attribution(item)
    if not any(field in item for field in SOURCE_FIELDS):
        # Old prepared batches used the three-part description as their private locator.
        credit = {field: credit[field] for field in ("category", "institution", "author")}
    return {"namespace": NAMESPACE, "record_key": digest + "/" + key, "content_hash": digest,
            "source_type": "research_item", "private_locator": description(credit)}


def source_id(item):
    source = item["source"]
    return "local-src-" + archive.sha(source["namespace"] + "\x00" + source["record_key"])


def credit_matches(item, row):
    saved = json.loads(row["metadata_json"]).get("attribution")
    if saved is None:
        return all(item.get(field) is None for field in ("author", *SOURCE_FIELDS))
    return attribution(saved) == attribution(item)


def check_duplicate_credit(db, item):
    """The frozen writer shares exact bodies across different source documents."""
    rows = db.execute("""SELECT s.metadata_json FROM cp_body b
        JOIN cp_memo m ON m.body_id=b.id JOIN cp_memo_source ms ON ms.memo_id=m.id
        JOIN cp_source_record s ON s.id=ms.source_id
        WHERE b.sha256=? AND m.lifecycle='active'""", (archive.sha(item["body"]),))
    known = [attribution(credit) for row in rows if (credit := json.loads(row[0]).get("attribution")) is not None]
    if known and attribution(item) not in known:
        raise ValueError("identical archived memo text has different source credits; review the attribution")


def check_covered_word_titles(db, item, summary):
    """An unchanged Word skips insertion, so its proposed titles must already be on record."""
    if not any(field in item for field in MATERIAL_TITLE_FIELDS):
        return
    rows = db.execute("""SELECT s.metadata_json,
        CASE WHEN s.content_hash=? THEN 1 WHEN b.sha256=? THEN 2 WHEN s.content_hash=? THEN 3 ELSE 4 END AS priority
        FROM cp_memo m JOIN cp_body b ON b.id=m.body_id
        JOIN cp_memo_source ms ON ms.memo_id=m.id JOIN cp_source_record s ON s.id=ms.source_id
        WHERE m.id IN (SELECT value FROM json_each(?)) ORDER BY priority""",
        (item["source"]["content_hash"], archive.sha(item["body"]), summary["base_version"],
         json.dumps(summary["prior_memo_ids"]))).fetchall()
    for metadata, priority in rows:
        if priority != rows[0][1]:
            break
        saved = json.loads(metadata).get("attribution") or {}
        if all(item.get(field) == saved.get(field) for field in MATERIAL_TITLE_FIELDS):
            return
    raise ValueError("unchanged Word has different archived material titles; review the attribution")


def preview(db, request, prior):
    items = request["reviewed_batch"]["items"]
    proposed = {company["company_id"]: company["name"] for item in items
                for company in item.get("new_companies", [])}
    covered = dict(request["already_archived"])
    if prior:
        for index, stored in zip(request["included"], prior["items"], strict=True):
            covered[items[index]["source"]["record_key"]] = [stored["memo_id"]]
    rows = []
    for item in items:
        if "research_item" not in item and not any(field in item for field in SOURCE_FIELDS):
            continue
        subject = item["primary_subject"]
        kind, sid = subject["kind"], subject["subject_id"]
        table = {"company": "cp_company", "theme": "cp_theme"}[kind]
        row = db.execute(f"SELECT name,status FROM {table} WHERE id=?", (sid,)).fetchone()
        if row and row["status"] == "active":
            name, registry = row["name"], "existing"
        elif row is None and kind == "company" and sid in proposed:
            name, registry = proposed[sid], "proposed"
        else:
            raise ValueError("unknown or inactive primary subject: " + sid)
        credit = attribution(item)
        memo_ids = covered.get(item["source"]["record_key"], [])
        source_description = description(credit)
        if memo_ids:
            saved = db.execute(
                "SELECT s.metadata_json FROM cp_source_record s JOIN cp_memo_source ms ON ms.source_id=s.id "
                "WHERE ms.memo_id=? AND s.content_hash=? ORDER BY (s.id=?) DESC, s.id",
                (memo_ids[0], item["source"]["content_hash"], source_id(item)))
            for row in saved:
                metadata = json.loads(row["metadata_json"])
                if credit_matches(item, row) and "source_description" in metadata:
                    source_description = metadata["source_description"]
                    break
        rows.append({"research_item": item.get("research_item"), "title": item["title"], "body": item["body"],
                     "primary_subject": {**subject, "name": name, "registry": registry},
                     "attribution": credit, "source_description": source_description,
                     "status": "already_archived" if memo_ids else "selected", "memo_ids": memo_ids})
    return rows


def install_credits(db, credits):
    """Enrich the new append-only source INSERT, on this writer connection only.

    The frozen writer owns the INSERT statement and the database forbids even
    immediate post-insert updates. The inner INSERT passes the original schema
    constraints and carries all the writer metadata. Its populated attribution
    makes the trigger a no-op even with recursive triggers enabled.
    """
    if not db.in_transaction:
        raise ValueError("source credits must share the memo transaction")
    db.execute("CREATE TEMP TABLE pending_source_credit(id TEXT PRIMARY KEY, credit TEXT, description TEXT)")
    db.executemany("INSERT INTO pending_source_credit VALUES(?,?,?)", [
        (sid, archive.canonical(credit), description(credit)) for sid, credit in credits.items()])
    db.execute("""CREATE TEMP TRIGGER attach_source_credit BEFORE INSERT ON main.cp_source_record
        WHEN json_type(NEW.metadata_json,'$.attribution') IS NULL
             AND EXISTS(SELECT 1 FROM pending_source_credit p WHERE p.id=NEW.id)
        BEGIN
            INSERT INTO cp_source_record
                (id,namespace,record_key,source_type,content_hash,private_locator,metadata_json,processing_status)
            SELECT NEW.id,NEW.namespace,NEW.record_key,NEW.source_type,NEW.content_hash,NEW.private_locator,
                   json_set(NEW.metadata_json,'$.attribution',json(p.credit),'$.source_description',p.description),
                   NEW.processing_status
            FROM pending_source_credit p WHERE p.id=NEW.id;
            SELECT RAISE(IGNORE);
        END""")
    db.execute("""CREATE TEMP TRIGGER verify_source_credit BEFORE INSERT ON main.local_archive_receipt
        WHEN EXISTS(SELECT 1 FROM pending_source_credit p LEFT JOIN cp_source_record s ON s.id=p.id
            WHERE json_extract(s.metadata_json,'$.attribution') IS NOT p.credit
               OR json_extract(s.metadata_json,'$.source_description') IS NOT p.description)
        BEGIN SELECT RAISE(ABORT,'source credit failed transactional readback'); END""")


def verify_credits(db, receipt):
    credits = (receipt.get("control") or {}).get("source_credits")
    if credits is None:  # Old receipts carry no per-Memo credit plan.
        return
    if set(credits) != {item["source_id"] for item in receipt["items"]}:
        raise ValueError("source credit receipt coverage differs")
    for sid, expected in credits.items():
        row = db.execute("SELECT metadata_json FROM cp_source_record WHERE id=?", (sid,)).fetchone()
        metadata = json.loads(row[0]) if row else {}
        if metadata.get("attribution") != expected or metadata.get("source_description") != description(expected):
            raise ValueError("stored source credit differs from the reviewed receipt: " + sid)

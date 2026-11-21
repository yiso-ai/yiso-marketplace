"""Identify a Word document and its research changes from committed Library records."""

from collections import Counter, defaultdict, deque
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import local_sources


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalized(text):
    # Preserve dates, signs, case, units and punctuation. Ignore layout whitespace.
    return " ".join(unicodedata.normalize("NFC", text).split())


def _lines(body):
    return [line for line in body.splitlines() if line.strip()]


def _hashes(lines):
    return [_sha(_normalized(line)) for line in lines]


def _path_key(value):
    return str(value).replace("\\", "/").casefold()


def _name_hint(value):
    stem = re.sub(r"\.doc(?:x|m)?$", "", _path_key(value).rsplit("/", 1)[-1])
    stem = re.sub(r"[\s_()[\]（）.-]*(?:rev(?:ision)?|ver(?:sion)?|v)[\s._-]*\d+(?:\.\d+)*[)）\]]*$",
                  "", stem)
    return "".join(c for c in unicodedata.normalize("NFKC", stem) if c.isalnum())


def _parts(extraction):
    result = dict(extraction.get("supplemental_hashes", {}))
    for i, rows in enumerate(extraction.get("native_tables", []), 1):
        result[f"native-table:{i}"] = _sha(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    return result


def _candidate(doc_id):
    return {"id": doc_id, "paragraphs": [], "texts": {}, "segments": {}, "memos": set(),
            "titles": [], "paths": set(), "source_hashes": set(), "covered": set(),
            "typed": False, "tracked": False, "full_text": False, "base": "", "parts": {}}


def _legacy_candidates(db):
    docs = {}
    rows = db.execute("""
        SELECT s.id,s.namespace,s.record_key,s.private_locator,s.content_hash,m.id,m.title,b.body,m.category
        FROM cp_memo m JOIN cp_body b ON b.id=m.body_id
        JOIN cp_memo_source ms ON ms.memo_id=m.id JOIN cp_source_record s ON s.id=ms.source_id
        WHERE m.lifecycle='active' AND (m.category='internal' OR lower(s.private_locator) LIKE '%.doc%')
        ORDER BY s.id,m.id
    """)
    for sid, namespace, key, locator, source_hash, mid, title, body, category in rows:
        if namespace in {"library-word-version", "library-research-item"}:
            continue
        locator = locator or ""
        details = {}
        if locator.startswith("{"):
            try:
                details = json.loads(locator)
            except ValueError:
                pass
        name = details.get("original_name") or details.get("original_source")
        local = details.get("local_source")
        if not name and isinstance(local, dict):
            name = local.get("member")
        if not name and not details:
            name = locator
        typed = bool(re.search(r"\.doc(?:x|m)?(?:$|[^\w])", str(name or ""), re.I)
                     or namespace in ("historical_memo_original", "historical-independent-image-docx")
                     or "docx" in str(key).lower() or "word/document.xml" in str(key).lower())
        # A named non-Word source cannot be a Word predecessor. Some migrated internal
        # memos have only an opaque ledger locator and can still be proposed for review.
        if not typed and (category != "internal" or (name and Path(str(name)).suffix)):
            continue
        declared = details.get("source_docx") or {}
        original = declared.get("sha256") if isinstance(declared, dict) else None
        original = original or details.get("original_sha256") or source_hash
        original = original if isinstance(original, str) and re.fullmatch("[a-f0-9]{64}", original) else None
        doc_id = "word-" + (original if typed and original else _sha("legacy-memo:" + mid))
        doc = docs.setdefault(doc_id, _candidate(doc_id))
        doc["typed"] |= typed
        if (category == "internal" and details.get("whole_source_covered") is True
                and details.get("paragraph_start") == 0 and isinstance(declared, dict)
                and isinstance(declared.get("total_direct_paragraphs"), int)
                and details.get("paragraph_end_inclusive") == declared["total_direct_paragraphs"] - 1):
            doc["full_text"] = True
        if original and typed:
            doc["source_hashes"].add(original)
        if name:
            doc["paths"].add(_path_key(name))
        if title not in doc["titles"]:
            doc["titles"].append(title)
        doc["memos"].add(mid)
        if mid in doc["segments"]:
            continue
        start = details.get("paragraph_start", details.get("first"))
        if not isinstance(start, int):
            matched = re.search(r"::p(\d+)(?:-|$)", str(key))
            start = int(matched.group(1)) if matched else 2**32
        doc["segments"][mid] = (start, body)
    for doc in docs.values():
        for _, body in sorted(doc.pop("segments").values(), key=lambda s: s[0]):
            lines = _lines(body)
            fingerprints = _hashes(lines)
            doc["texts"].update(zip(fingerprints, lines))
            doc["paragraphs"].extend(fingerprints)
        doc["covered"] = set(range(1, len(doc["paragraphs"]) + 1))
        doc["base"] = "legacy-" + _sha("|".join(doc["paragraphs"]))
    return docs


def _documents(db):
    docs = _legacy_candidates(db)
    # Receipt rows and memo bodies are committed together. Earlier version hashes prevent
    # resubmission of a stale Word from rolling a document's head back.
    tracked = db.execute("""
        SELECT v.value,json_extract(r.receipt_json,
               '$.items[' || json_extract(v.value,'$.item_index') || '].memo_id')
        FROM local_archive_receipt r JOIN json_each(r.receipt_json,'$.control.word_versions') v
        ORDER BY r.rowid, v.key
    """)
    adopted = set()
    for raw, mid in tracked:
        version = json.loads(raw)
        doc_id = version["document_id"]
        doc = docs.setdefault(doc_id, _candidate(doc_id))
        doc["tracked"] = doc["typed"] = doc["full_text"] = True
        doc["paragraphs"] = version["paragraphs"]
        doc["covered"] = set(version["covered"])
        doc["parts"] = version["parts"]
        doc["base"] = version["source_sha256"]
        doc["source_hashes"].add(doc["base"])
        doc["paths"].add(_path_key(version["path"]))
        doc["memos"].update(version.get("prior_memo_ids", []))
        if mid:
            doc["memos"].add(mid)
            row = db.execute("SELECT m.title,b.body FROM cp_memo m JOIN cp_body b ON b.id=m.body_id WHERE m.id=?",
                             (mid,)).fetchone()
            if row:
                doc["titles"].append(row[0])
                lines = _lines(row[1])
                doc["texts"].update(zip(_hashes(lines), lines))
        adopted.update(doc["memos"])
    return {key: doc for key, doc in docs.items()
            if doc["tracked"] or not doc["memos"].issubset(adopted)}


def _probes(value):
    # Sampling several separated substrings finds a paragraph with changed figures or a
    # rewritten opening, without a quadratic character comparison against every old memo.
    width = 4 if len(value) < 30 else 8
    return {value[offset:offset + width] for offset in
            (0, max(0, (len(value) - width) // 2), max(0, len(value) - width))}


def _fuzzy(old, new):
    a, b = _normalized(old).casefold(), _normalized(new).casefold()
    shorter = min(len(a), len(b))
    if shorter < 3 or shorter < .62 * max(len(a), len(b)):
        return 0.0
    if not any(probe in b for probe in _probes(a)) and shorter >= 13:
        return 0.0
    # Long paragraphs often contain copied tables. Bound character-level comparisons.
    if len(a) > 1400 or len(b) > 1400:
        a, b = a[:700] + a[-700:], b[:700] + b[-700:]
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _rank(docs, source_hash, path, keys, lines):
    incoming = set(keys)
    freq = Counter(k for d in docs.values() for k in set(d["paragraphs"]) & incoming)
    typed_freq = Counter(k for d in docs.values() if d["typed"] for k in set(d["paragraphs"]) & incoming)
    lengths = {k: len(_normalized(line)) for k, line in zip(keys, lines)}
    # Limit the index to the small collection of probe strings found in existing text.
    # Exact known paragraphs are already handled by the digest lookup.
    probes = {probe for d in docs.values() for text in d["texts"].values() if len(text) >= 8
              for probe in _probes(_normalized(text).casefold())}
    lookup = defaultdict(set)
    for i, line in enumerate(lines):
        value = _normalized(line).casefold()
        for width in (4, 8):
            for n in range(len(value) - width + 1):
                key = value[n:n + width]
                if key in probes and len(lookup[key]) < 7:
                    lookup[key].add(i)
    ranked = []
    incoming_name = _name_hint(path)
    for d in docs.values():
        oldkeys = set(d["paragraphs"])
        same_file = source_hash in d["source_hashes"]
        same_path = _path_key(path) in d["paths"]
        common = oldkeys & incoming
        whole_text = bool(keys and keys == d["paragraphs"])
        shared = {k for k in common if lengths[k] >= 8}
        useful = {k for k in shared if freq[k] <= 3}
        # A migrated ledger projection may duplicate the same physical Word excerpt.
        frequencies = typed_freq if d["typed"] else freq
        unique = {k for k in useful if frequencies[k] == 1}
        short_anchors = {k for k in common if lengths[k] >= 4 and freq[k] == 1}
        fuzzy = 0.0
        if not useful and not same_file:
            for k in sorted(oldkeys - incoming):
                text = d["texts"].get(k)
                if not text or len(text) < 8:
                    continue
                targets = set()
                for probe in _probes(_normalized(text).casefold()):
                    offsets = lookup.get(probe, ())
                    if len(offsets) < 7:
                        targets.update(offsets)
                for i in targets:
                    fuzzy = max(fuzzy, _fuzzy(text, lines[i]))
                if fuzzy >= .93:
                    break
        denom = max(1, len(oldkeys))
        overlap = len(shared) / denom
        mass = sum(min(lengths[k], 180) for k in useful)
        longest = max((lengths[k] for k in unique), default=0)
        strong = (d["typed"] and ((whole_text and (same_path or
                                      (len(short_anchors) >= 2 and sum(lengths[k] for k in short_anchors) >= 18)))
                                  or (len(short_anchors) >= 3 and sum(lengths[k] for k in short_anchors) >= 27
                                      and len(short_anchors) >= .5 * denom)
                                  or (len(unique) >= 2 and mass >= 36 and overlap >= .32)
                                  or (longest >= 44 and overlap >= .62 and len(shared) / len(incoming) >= .35)
                                  or (same_path and unique and overlap >= .35)))
        if not d["typed"] and len(unique) >= 3 and mass >= 120 and overlap >= .70:
            strong = True
        same_name = (len(incoming_name) >= 5 and
                     any(_name_hint(p) == incoming_name for p in (*d["paths"], *d["titles"])))
        plausible = (same_file or same_path or whole_text or strong or (len(useful) >= 2 and mass >= 28)
                     or (len(short_anchors) >= 2 and sum(lengths[k] for k in short_anchors) >= 12)
                     or (useful and max(lengths[k] for k in useful) >= 18)
                     or (len(shared) >= 2 and sum(lengths[k] for k in shared) >= 40)
                     or (same_name and (common or fuzzy >= .65))
                     or fuzzy >= .84)
        if plausible:
            score = (int(same_file), int(bool(strong)), len(unique), mass, round(overlap, 3),
                     int(same_path), round(fuzzy, 3))
            ranked.append((score, d, shared))
    return sorted(ranked, key=lambda v: (v[0], v[1]["id"]), reverse=True)


def _choose(ranked, choice):
    if choice:
        if choice == "new":
            if ranked and ranked[0][0][0]:
                raise ValueError("an identical registered Word version cannot be classified as a new document")
            return None, False
        for _, doc, _ in ranked:
            if doc["id"] == choice:
                return doc, False
        raise ValueError("selected Word document is not a current match candidate")
    if not ranked:
        return None, False
    score, doc, hits = ranked[0]
    if score[0]:
        return (doc, False) if sum(bool(row[0][0]) for row in ranked) == 1 else (None, True)
    if not score[1]:
        return None, True
    for other, other_doc, other_hits in ranked[1:]:
        # Opaque historical index projections may repeat part of a known Word. Only
        # disregard that projection when the physical document has independent support.
        if not other_doc["typed"] and other_hits and other_hits <= hits and score[2] >= 2:
            continue
        if other[1] or other[5] or (other_hits and len(other_hits) >= max(1, len(hits) * .6)):
            return None, True
    return doc, False


def _occurrences(keys):
    seen = Counter()
    result = []
    for key in keys:
        seen[key] += 1
        result.append((key, seen[key]))
    return result


def _revisions(hunks, prev, lines, old, pairs, used, tracked):
    revisions = {}
    for a, b, x, y in hunks:
        new_indices = [j for j in range(x, y) if j not in pairs]
        old_indices = [i for i in range(a, b) if i not in used]
        # Restrict very large replacement blocks to nearby positions; enormous rewrites
        # do not justify a speculative pairing merely because the ordinal is similar.
        choices = defaultdict(list)
        reverse = defaultdict(list)
        for pos, j in enumerate(new_indices):
            near = old_indices
            if len(new_indices) * len(old_indices) > 4096:
                center = round(pos * len(old_indices) / len(new_indices))
                near = old_indices[max(0, center - 4):center + 5]
            for i in near:
                prior = old.get(prev[i])
                if prior is None:
                    continue
                similarity = _fuzzy(prior, lines[j])
                floor = .70 if max(len(prior), len(lines[j])) < 9 else .62
                if similarity >= floor:
                    choices[j].append((similarity, i))
                    reverse[i].append((similarity, j))
        for j, options in choices.items():
            options.sort(reverse=True)
            score, i = options[0]
            matches = sorted(reverse[i], reverse=True)
            if matches[0][1] != j:
                continue
            if ((len(options) > 1 and score - options[1][0] < .06)
                    or (len(matches) > 1 and score - matches[1][0] < .06)):
                continue
            revisions[j] = i
            used.add(i)
        if tracked and len(new_indices) == len(old_indices) == 1:
            j, i = new_indices[0], old_indices[0]
            if i not in used and prev[i] not in old and (j - 1 in pairs or j + 1 in pairs):
                revisions[j] = i
                used.add(i)
    return revisions


def _delta(doc, keys, lines):
    prev = doc["paragraphs"] if doc else []
    old = doc["texts"] if doc else {}
    matcher = SequenceMatcher(None, _occurrences(prev), _occurrences(keys), autojunk=False)
    pairs = {}
    hunks = []
    for tag, a, b, x, y in matcher.get_opcodes():
        if tag == "equal":
            pairs.update((j, a + j - x) for j in range(x, y))
        if tag == "replace":
            hunks.append((a, b, x, y))
    used = set(pairs.values())
    free = defaultdict(deque)
    for i, k in enumerate(prev):
        if i not in used:
            free[k].append(i)
    # Sequence alignment alone treats moved paragraphs as deletions/insertions.
    for j, k in enumerate(keys):
        if j not in pairs and free[k]:
            i = free[k].popleft()
            pairs[j] = i
            used.add(i)
    covered = {j + 1 for j, i in pairs.items() if i + 1 in doc["covered"]} if doc else set()
    revisions = _revisions(hunks, prev, lines, old, pairs, used, doc and doc["tracked"])
    changes = []
    for j, text in enumerate(lines):
        if j in pairs:
            continue
        kind = "uncovered" if doc and not doc["full_text"] else "added"
        item = {"kind": kind, "paragraph": j + 1, "text": text}
        i = revisions.get(j)
        if i is not None:
            prior_text = old.get(prev[i])
            item.update(kind="revised", prior_paragraph=i + 1, prior_sha256=prev[i])
            if prior_text:
                item["old_text"] = prior_text
        changes.append(item)
    removed = [{"prior_paragraph": i + 1, "prior_sha256": k, **({"old_text": old[k]} if k in old else {})}
               for i, k in enumerate(prev) if i not in used]
    return changes, removed, covered


def propose(db, path, *, choice=None, extraction=None):
    """Read a file and library state. A choice identifies an explicit ambiguity resolution."""
    path = Path(path)
    if path.suffix.lower() not in {".doc", ".docm", ".docx"}:
        raise ValueError("Word comparison requires a Word document")
    result = extraction if extraction is not None else local_sources.extract(path)
    source_hash = result["source_sha256"]
    lines = _lines(result["body"])
    keys = _hashes(lines)
    parts = _parts(result)
    docs = _documents(db)
    ranked = _rank(docs, source_hash, path, keys, lines)
    doc, ambiguous = _choose(ranked, choice)
    candidates = [{"document_id": d["id"], "titles": d["titles"][:4], "memo_ids": sorted(d["memos"]),
                   "matched_paragraphs": len(hits), "same_path": bool(score[5]), "exact_file": bool(score[0]),
                   "similarity": score[6]} for score, d, hits in ranked]
    if ambiguous:
        for candidate, (_, d, hits) in zip(candidates, ranked):
            candidate["source_names"] = sorted({p.rsplit("/", 1)[-1] for p in d["paths"]})[:4]
            ordered = dict.fromkeys(d["paragraphs"])
            candidate["matching_examples"] = [d["texts"][k][:140] for k in ordered
                                              if k in hits and k in d["texts"]][:2]
            candidate["other_prior_example"] = next((d["texts"][k][:140] for k in ordered
                                                       if k not in hits and k in d["texts"]
                                                       and len(d["texts"][k]) >= 12), None)
        return {"status": "ambiguous", "candidates": candidates, "source_sha256": source_hash}
    changes, removed, covered = _delta(doc, keys, lines)
    prior_parts = doc["parts"] if doc else {}
    supplemental = {"added": sorted(parts.keys() - prior_parts.keys()),
                    "modified": sorted(k for k in parts.keys() & prior_parts.keys() if parts[k] != prior_parts[k]),
                    "removed": sorted(prior_parts.keys() - parts.keys()),
                    "legacy_unverified": bool(doc and not doc["tracked"] and parts)}
    supplemental["changed"] = any(supplemental.values())
    exact = bool(doc and source_hash in doc["source_hashes"])
    legacy_unmatched = []
    if exact:
        if not doc["full_text"]:
            legacy_unmatched = [c["paragraph"] for c in changes]
        changes, removed = [], []
        supplemental = dict(changed=False, added=[], modified=[], removed=[], legacy_unverified=False)
    doc_id = doc["id"] if doc else "word-" + source_hash
    status = "new" if not doc else ("matched" if changes or supplemental["changed"] else "unchanged")
    prior_memos = sorted(doc["memos"]) if doc else []
    base = doc["base"] if doc else None
    version = {"document_id": doc_id, "source_sha256": source_hash, "base_version": base,
               "path": str(path), "paragraphs": keys, "covered": sorted(covered), "parts": parts,
               "prior_memo_ids": prior_memos}
    return {"status": status, "document_id": doc_id, "source_sha256": source_hash, "base_version": base,
            "prior_memo_ids": prior_memos, "legacy_partial": bool(doc and not doc["full_text"]),
            "exact_file": exact, "legacy_unmatched_paragraphs": legacy_unmatched,
            "changes": changes, "removed": removed, "supplemental": supplemental,
            "candidates": candidates, "version": version}


def reviewed_version(proposal, item, item_index):
    """Record review coverage with the version, without persisting unselected source text."""
    version = dict(proposal["version"])
    covered = set(version["covered"])
    reviewed = item.get("word_review", {})
    explicit = set(reviewed.get("paragraphs", []))
    covered.update(explicit)
    inferred = set()
    if not explicit:
        # One copy of a quote in a memo supports one occurrence, even when the current
        # Word repeats an identical paragraph elsewhere.
        offset = 0
        for change in proposal["changes"]:
            match = item["body"].find(change["text"], offset)
            if match >= 0:
                offset = match + len(change["text"])
                inferred.add(change["paragraph"])
        covered.update(inferred)
    revisions = []
    for change in proposal["changes"]:
        if change["kind"] == "revised" and change["paragraph"] in covered:
            revisions.append({k: change[k] for k in ("paragraph", "prior_paragraph", "prior_sha256")})
    version.update(covered=sorted(covered), item_index=item_index, revisions=revisions,
                   selected_paragraphs=sorted(explicit or inferred),
                   selection_basis="editorial" if explicit else "verbatim",
                   reviewed_parts=sorted(reviewed.get("parts", [])))
    return version


def possibly_same_batch(a, b):
    if a["document_id"] == b["document_id"] or a["source_sha256"] == b["source_sha256"]:
        return True
    left, right = set(a["paragraphs"]), set(b["paragraphs"])
    shared = len(left & right)
    return bool(shared and (min(len(left), len(right)) == 1 or
                            (shared >= 2 and shared >= .45 * min(len(left), len(right)))))

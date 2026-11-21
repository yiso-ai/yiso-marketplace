"""Convert a completed editorial review into a checked local Library batch."""

import argparse
from contextlib import closing
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
RUNTIME = Path(__file__).resolve().parents[1] / "production" / "runtime"
sys.path.insert(0, str(RUNTIME))
import local_industry
import local_research
import local_sources
import word_intake


def bind_quotes(value, body):
    """Calculate Unicode offsets only for unambiguous, exact quotations."""
    if isinstance(value, list):
        for entry in value:
            bind_quotes(entry, body)
    elif isinstance(value, dict):
        if "quote" in value and set(value) <= {
            "quote", "claim_kind", "start_offset", "end_offset"
        }:
            quote = value["quote"]
            if not isinstance(quote, str) or not quote.strip():
                raise ValueError("an evidence quotation is empty")
            if "start_offset" in value or "end_offset" in value:
                start, end = value.get("start_offset"), value.get("end_offset")
                if (type(start) is not int or type(end) is not int
                        or start < 0 or end > len(body) or body[start:end] != quote):
                    raise ValueError("evidence offsets do not match the reviewed text")
            else:
                start = body.find(quote)
                if start < 0 or body.find(quote, start + 1) >= 0:
                    raise ValueError("quotation missing or repeated; specify exact offsets")
                value.update(start_offset=start, end_offset=start + len(quote))
        else:
            for child in value.values():
                bind_quotes(child, body)


def prepare(review):
    if set(review) != {"batch_id", "items"} or not isinstance(review["items"], list):
        raise ValueError("expected a batch_id and a list of reviewed items")
    items = []
    inspected = {}
    for entry in review["items"]:
        item = dict(entry)
        path = Path(item.pop("file"))
        if not path.is_absolute():
            raise ValueError("provide the absolute path of the reviewed source file")
        path = local_sources.safe_source(path)
        word = path.suffix.lower() in word_intake.WORD_SUFFIXES
        if path not in inspected:
            extraction = local_sources.extract(path) if word else None
            digest = extraction["source_sha256"] if word else local_sources.read_source(path)[0]
            inspected[path] = digest, extraction
        digest, extraction = inspected[path]
        if digest != item.pop("sha256"):
            raise ValueError(f"source changed since review: {path}")
        if "source" in item or "input_file" in item:
            raise ValueError("source identity is derived from the reviewed file")
        local_research.prepare_source_fields(item)
        item["source"] = {
            "namespace": "library-local-file",
            "record_key": path.as_uri(),
            "source_type": "local_file",
            "content_hash": digest,
            "private_locator": str(path),
        }
        if "research_item" in item:
            item["input_file"] = str(path)
            item["source"] = local_research.research_source(item, digest)
        elif word:
            with closing(local_sources.read_db()) as db:
                word_intake.bind_baseline(db, item, path, review["batch_id"], extraction)
        for field in ("companies", "themes", "relations", "relevance"):
            bind_quotes(item.get(field, []), item["body"])
        source_text = local_industry.source_text(item, path, extraction)
        for company in item.get("new_companies", []):
            bind_quotes(company["evidence"], item["body"])
            if "industry" in company:
                plan = company["industry"]
                basis = plan.get("basis", "memo")
                if basis not in local_industry.OFFICIAL_BASES:
                    bind_quotes(plan["evidence"], source_text if basis == "source" else item["body"])
        items.append(item)
    vector = any(item.get("original_vector_images") for item in items)
    batch = {"protocol": "cp-local-batch-v3" if vector else "cp-local-batch-v2",
             "batch_id": review["batch_id"], "items": items}
    local_industry.validate(batch)
    return batch


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = prepare(json.loads(args.review.read_text(encoding="utf-8-sig")))
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        with args.out.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
        print(f"{payload['batch_id']}: {len(payload['items'])} reviewed item(s)")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"{error}\n")

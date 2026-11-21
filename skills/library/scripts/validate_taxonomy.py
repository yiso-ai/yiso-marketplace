#!/usr/bin/env python3
"""Offline structural check of normalized review snapshots, not production authority."""
import json
import sys
import unicodedata


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def validate(current, previous=None):
    errors = []
    nodes = current.get('nodes', [])
    ids = [n['id'] for n in nodes]
    if len(ids) != len(set(ids)):
        errors.append('duplicate stable ID')
    by_id = {n['id']: n for n in nodes}
    names, aliases = {}, {}
    for node in nodes:
        key = normalized(node['canonical_name'])
        names.setdefault(key, []).append(node['id'])
        for alias in node.get('aliases', []):
            aliases.setdefault(normalized(alias['name']), []).append((node['id'], alias.get('context_only') is True))
        for parent in node.get('parents', []):
            if parent not in by_id:
                errors.append(f"missing parent: {node['id']} -> {parent}")
    for key, owners in names.items():
        if len(set(owners)) > 1:
            errors.append(f'canonical collision: {key}')
    for key, entries in aliases.items():
        owners = set(names.get(key, []) + [owner for owner, _ in entries])
        if len(owners) > 1 and any(not context for _, context in entries):
            errors.append(f'alias collision requires context_only: {key}')
    visiting, done = set(), set()
    def visit(key):
        if key in visiting:
            errors.append(f'cycle: {key}')
            return
        if key in done or key not in by_id:
            return
        visiting.add(key)
        for parent in by_id[key].get('parents', []):
            visit(parent)
        visiting.remove(key)
        done.add(key)
    for key in by_id:
        visit(key)
    for edge in current.get('crosslinks', []):
        if edge['source'] not in by_id or edge['target'] not in by_id:
            errors.append('missing crosslink endpoint')
    companies = set(current.get('company_ids', []))
    for edge in current.get('company_topic_edges', []):
        if edge['topic'] not in by_id or edge['company'] not in companies:
            errors.append('missing company/topic endpoint')
    for old in (previous or {}).get('nodes', []):
        new = by_id.get(old['id'])
        if new is None:
            errors.append(f"removed stable ID: {old['id']}")
        elif any(old.get(field) and new.get(field) != old[field] for field in ('page_id', 'page_url')):
            errors.append(f"changed existing Page reference: {old['id']}")
    return errors


if __name__ == '__main__':
    try:
        with open(sys.argv[1]) as f:
            current = json.load(f)
        previous = None
        if len(sys.argv) > 2:
            with open(sys.argv[2]) as f:
                previous = json.load(f)
        errors = validate(current, previous)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        errors = [f'invalid review snapshot: {exc}']
    print('\n'.join(errors) if errors else 'Structural checks passed; semantics, permissions and remote state not verified.')
    sys.exit(bool(errors))

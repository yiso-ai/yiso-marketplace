# Authenticated Worker/D1 new-instance starter

这是可运行的Worker/D1适配与本地验证说明；实际Sites部署、迁移、身份和访问权限必须在新用户自己的实例另行验收。

This is a locally tested starter, not a production migration or deployment. It has not been run against live Sites or D1. Do not point it at the existing user database. No production data or credentials are included.

## Local verification and build

From the skill root, first run `cd assets/starter/worker`, then:

    node scripts/generate_migration.mjs
    node scripts/test_worker.mjs
    node build.mjs

Requires Node 20+ (Node 22 recommended) and Python 3 for the test-only SQLite bridge. Runtime uses standard Worker APIs; there are no npm dependencies. Build reads the three UI files in the parent directory and emits `dist/server/index.js`, a standalone ES-module Worker embedding all static assets. The source adapter can alternatively receive a compatible `ASSETS.fetch` binding; the generated module does not require an assets binding.

The SQL file in `migrations/0001_initial.sql` is a schema-only blueprint for a FRESH database; it contains no INSERT seeds. After applying that schema via the reviewed target migration flow, explicitly execute `bootstrap.sql` once to initialize the singleton version state before importing. It does not seed fixture records. It is not an upgrade for earlier schemas. Sites may require native Drizzle migration files and metadata/journal; do not assume this SQL filename is automatically applied. Convert and review the blueprint using the actual target project's supported migration flow before deployment. Fixture injection is separate and explicit (the local test applies schema, then separately runs bootstrap.sql, then imports the parent synthetic fixture into a temporary SQLite database).

## Hosted boundary and configuration

Use only with trusted Sites dispatch in front of every request. Configure its native D1 binding as `DB`, and use the supported private Site audience/access policy without expanding it. The relevant hosting binding fragment is `{"d1":"DB","r2":null}`; this is not a complete project manifest and contains no invented project ID. Confirm all other manifest fields with the current Sites starter.

Configure server-side `OWNER_USER_ID` to the owner's verified Site-scoped stable user ID, `SITES_DISPATCH_TRUSTED=true` only after verifying the platform boundary, and `IMPORT_ENABLED=true` only for an authorized import window. Default/missing configuration fails closed. These are configuration values, not a replacement authentication system.

Sites dispatch authenticates and supplies `oai-authenticated-user-id`. A request must have that ID and match the configured owner to read even static assets or `/api/data`. Import additionally requires the import flag, exact same-origin `Origin`, and same-origin Fetch Metadata when present. This is suitable for an authenticated same-origin browser caller; it does not create an unattended ingestion credential. Service-only access, including a Sites bypass token without visitor identity, is rejected. There is no homemade shared secret or fallback token.

IMPORTANT: The trust flag itself does not authenticate headers. Do not expose a direct workers.dev, preview, or alternate Worker origin accepting attacker-supplied identity headers. Arbitrary self-hosting needs a verified authenticating/stripping reverse proxy integration before enabling the flag. Never turn on the flag merely to make local requests work. Native Sites owns sign-in/out routes, which this Worker does not implement.

The sole bootstrap exception is `GET /api/identity`: after trusted dispatch and caller authentication, it returns only `{id: callerSiteScopedId}` without an owner match or database access. It grants no reader/import permission. Use the signed-in owner's value to configure `OWNER_USER_ID`; an account ID from a Site audience allowlist is not necessarily this Site-scoped ID.

## API and data rules

- `GET /api/data?offset=0&limit=50`: maximum 50 memos, ordered by date descending then ID with undated last; returns `total`, `nextOffset`, `version`, full directory metadata, evidence snippets, and native tables. When paging, a client must restart if the version changes. No raw `source_key`, `author_private`, or evidence identifiers are exposed. Internal display author and institution are intentional reader metadata.
- Directory arrays and company/theme summaries have a verified ceiling of 10,000 rows. Reader compares explicit SQL counts against returned directory rows and fails closed if incomplete or too large. It also checks expected memo page length and exact assignment/evidence and native-table row counts for the page; truncated results are rejected. This is not unlimited large-database support.
- `POST /api/import`: JSON, at most 1 MB, strict field sets and duplicate-key rejection, maximum 100 memos and 500 subjects. Supply all six taxonomy arrays, even when empty. Imports add records, not update them. Existing source keys or memo IDs conflict; identical body hashes reuse immutable body rows. Existing subjects must match exactly. Existing taxonomy rows cannot be reinserted; send only new rows and order parent industries before children.
- Batch SHA-256 replay fingerprint is computed from canonical JSON, and exact replay returns the original version. Use identifiers and integer numeric fields as in the fixture. The body SHA-256 is UTF-8; evidence offsets count Unicode codepoints, matching Python rather than JavaScript UTF-16 code units.
- D1 `batch` is the transaction boundary. First statement performs optimistic version CAS. Immediately following `changes()` guard insertion fails a CHECK when CAS changed no row, rolling back the entire batch. Schema guard must exist. The same transaction checks taxonomy relationships/cycles and writes content, evidence, assignments, tables, and batch receipt. D1's transactional batch and foreign-key behavior must be verified on the actual target before production use.
- Exactly one primary assignment must match the memo's primary subject. Every assignment has a quote matching the exact body span. Native tables retain header/row cells. Frontend uses plain text rendering, not imported HTML.

## Verification scope and limits

The test harness executes real SQLite transactions through a small D1-compatible bridge and verifies auth fail-closed behavior, separate import gate, origin checks, CAS rollback, replay races, source/body deduplication, evidence and primary validation, taxonomy cycle rejection, private-field omission, Unicode offsets, pagination and JSON validation. This is not a network concurrency/load test or proof of live D1 compatibility. Actual D1 statement limits, query duration, transaction semantics, Sites identity/header stripping and private access policy, migration application, paging consistency, and deployed security headers require target-environment checks. The entire reader is owner-only, not a public summary endpoint. No existing database migration, remote reads, publication or permission changes have been performed.

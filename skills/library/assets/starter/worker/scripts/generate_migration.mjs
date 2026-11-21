import {readFile,writeFile} from 'node:fs/promises';
const source=(await readFile(new URL('../../schema.sql',import.meta.url),'utf8')).replace(/^INSERT OR IGNORE INTO state VALUES\(1,0\);\r?\n/m,'');
// Schema only: control-row bootstrap and fixture import are separate explicit steps.
await writeFile(new URL('../migrations/0001_initial.sql',import.meta.url),source+'\nCREATE TABLE IF NOT EXISTS import_guard(id INTEGER PRIMARY KEY CHECK(id=1),ok INTEGER NOT NULL CHECK(ok=1));\n');

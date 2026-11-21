-- Explicit fresh-database control-state initialization, separate from schema.
INSERT OR IGNORE INTO state(id,version) VALUES(1,0);

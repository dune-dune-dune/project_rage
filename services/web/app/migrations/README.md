# SQL migrations

Every `*.sql` file here is applied once, in lexicographic filename order, at
startup (`app/db.py:run_migrations`, called from `create_app`). Applied versions
are recorded in the `schema_migrations` table and skipped on every later boot.

Rules:

- **Append only.** Never edit a migration that has already shipped — the engine
  tracks files by *name*, not by checksum, so an edit to an applied file is
  silently ignored on machines that already ran it (and silently applied on
  fresh ones). Fix mistakes with a new file.
- **Zero-padded numeric prefix** (`0003_…`). Ordering is a plain string sort.
- Each file runs inside one transaction together with its `schema_migrations`
  row: a failing statement rolls the whole file back and aborts startup.
- Statements must be idempotent-friendly (`IF NOT EXISTS` / `INSERT OR IGNORE`)
  so a half-migrated database from an older build still converges.

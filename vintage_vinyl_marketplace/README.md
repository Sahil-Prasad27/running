# Vintage Vinyl Record Store & Trade-In Marketplace

Full-stack Python + Flask + SQLite + HTML/CSS/JavaScript reference implementation based on the supplied DB, UI and Programming requirement sheets and the 14-page project brief.

## Stack
- Backend: Python 3.11+, Flask, Flask-SocketIO
- Database: SQLite with foreign keys, WAL, partial/expression indexes and FTS5 search
- Frontend: server-rendered HTML + vanilla CSS/JS
- Receipts/statements: ReportLab PDFs plus CSV companion output

## Start
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Windows alternative without activating the venv: .venv\\Scripts\\python run.py
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```
Open http://127.0.0.1:5000

The app seeds demo records, customers, blacklist data, a wantlist and a service ticket on the first database initialization.

## Important implementation notes
SQLite does not provide PostgreSQL `ENUM`, `JSONB`, `BYTEA`, GIN or generated SQL functions with the same names. The project maps enums to `CHECK` constraints, JSONB to TEXT containing JSON, blobs to SQLite BLOB, uses FTS5 for fast text search, and uses expression/partial indexes for the matrix and first-pressing uniqueness requirements.

The payment layer stores token-like card references only and never accepts/stores a PAN. A real PCI provider should be injected for production use.

The supplied specification leaves the exact compliance threshold, store tax configuration, reward expiry length, and some operational integrations configurable; this project seeds conservative demo defaults in `services/db.py` and `data/settings.json` rather than pretending they were fixed in the brief.

## Coverage map
- DB-001..DB-010: catalogue, pressings, inventory, trade-ins, customers/wantlists, orders/tenders, consignments, service tickets, loyalty ledger, audit/blacklist.
- UI-001..UI-010: listing/grading, trade-ins, wantlists/reservation, POS, reorders, service queue, consignment, search, loyalty, dashboard.
- PROG-001..PROG-010: class hierarchy, grading service, valuation, observer-style wantlist matching, POS flow, consignment payout, FIFO service queue, chunked CSV importer, receipt renderers/dispatcher, custom exception hierarchy.

## Test
```bash
pytest -q
# Windows alternative without activating the venv: .venv\\Scripts\\python -m pytest -q
```

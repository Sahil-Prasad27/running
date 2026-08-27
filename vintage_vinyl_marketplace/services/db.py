from pathlib import Path
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from decimal import Decimal
import json

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / 'vinyl_marketplace.sqlite3'

SCHEMA = r'''
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS stores(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'USD' CHECK(length(currency)=3), vat_rate REAL NOT NULL DEFAULT 0,
 vat_mode TEXT NOT NULL DEFAULT 'included' CHECK(vat_mode IN ('included','excluded')), low_stock_default INTEGER NOT NULL DEFAULT 2,
 compliance_threshold NUMERIC NOT NULL DEFAULT 500.00, timezone TEXT NOT NULL DEFAULT 'UTC', role_discount_cap REAL NOT NULL DEFAULT 10,
 international_shipping_fee NUMERIC NOT NULL DEFAULT 20
);
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, role TEXT NOT NULL DEFAULT 'staff' CHECK(role IN ('staff','manager','admin')), store_id INTEGER NOT NULL REFERENCES stores(id)
);
CREATE TABLE IF NOT EXISTS artists(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS labels(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS consignors(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, phone TEXT);
CREATE TABLE IF NOT EXISTS customers(
 id INTEGER PRIMARY KEY, email TEXT UNIQUE, phone TEXT, display_name TEXT NOT NULL, country_code TEXT, marketing_opt_in INTEGER NOT NULL DEFAULT 0,
 tier TEXT NOT NULL DEFAULT 'basic' CHECK(tier IN ('basic','silver','gold','platinum')), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 gdpr_erased_at TEXT, wantlist_justification TEXT, cancellation_count_12m INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS records(
 id INTEGER PRIMARY KEY, artist_id INTEGER NOT NULL REFERENCES artists(id), title TEXT NOT NULL CHECK(length(title)<=200), label_id INTEGER NOT NULL REFERENCES labels(id),
 catalogue_number TEXT NOT NULL CHECK(length(catalogue_number)<=30), country_code TEXT NOT NULL CHECK(length(country_code)=2), year INTEGER NOT NULL CHECK(year BETWEEN 1948 AND 2100),
 format TEXT NOT NULL CHECK(format IN ('7in','10in','12in_lp','12in_maxi','box_set','picture_disc','coloured')), rpm INTEGER NOT NULL CHECK(rpm IN (33,45,78)),
 mono_stereo TEXT NOT NULL DEFAULT 'unknown' CHECK(mono_stereo IN ('mono','stereo','quadraphonic','unknown')), weight_grams INTEGER,
 is_reissue INTEGER NOT NULL DEFAULT 0, original_record_id INTEGER REFERENCES records(id) ON DELETE RESTRICT, pre_order INTEGER NOT NULL DEFAULT 0,
 release_date TEXT, deposit_policy TEXT NOT NULL DEFAULT 'optional' CHECK(deposit_policy IN ('none','optional','required')), per_customer_cap INTEGER NOT NULL DEFAULT 5,
 allocation_rule TEXT NOT NULL DEFAULT 'fifo' CHECK(allocation_rule IN ('fifo','raffle')), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, deleted_at TEXT,
 UNIQUE(label_id,catalogue_number,country_code),
 CHECK((is_reissue=0 AND original_record_id IS NULL) OR (is_reissue=1 AND original_record_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_records_label_cat ON records(label_id,catalogue_number);
CREATE INDEX IF NOT EXISTS idx_records_year ON records(year);
CREATE INDEX IF NOT EXISTS idx_records_not_deleted ON records(deleted_at);
CREATE TABLE IF NOT EXISTS record_genres(record_id INTEGER REFERENCES records(id) ON DELETE CASCADE, genre TEXT NOT NULL, UNIQUE(record_id,genre));
CREATE TABLE IF NOT EXISTS pressings(
 id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE RESTRICT, pressing_plant TEXT, matrix_runout_a TEXT, matrix_runout_b TEXT,
 press_year INTEGER, is_first_pressing INTEGER NOT NULL DEFAULT 0, is_promo INTEGER NOT NULL DEFAULT 0, catalogue_variant TEXT, notes TEXT, deleted_at TEXT,
 CHECK(is_first_pressing=0 OR press_year IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pressing_matrix ON pressings(record_id,COALESCE(matrix_runout_a,''),COALESCE(matrix_runout_b,'')) WHERE matrix_runout_a IS NOT NULL OR matrix_runout_b IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pressing_first ON pressings(record_id) WHERE is_first_pressing=1;
CREATE INDEX IF NOT EXISTS idx_pressings_record ON pressings(record_id);
CREATE INDEX IF NOT EXISTS idx_pressings_matrix ON pressings(matrix_runout_a,matrix_runout_b);
CREATE INDEX IF NOT EXISTS idx_pressings_promo ON pressings(is_promo);

CREATE TABLE IF NOT EXISTS counterfeit_blacklist(
 id INTEGER PRIMARY KEY, matrix_runout_a TEXT NOT NULL, matrix_runout_b TEXT, artist_name TEXT, title TEXT, reason TEXT NOT NULL, source_authority TEXT NOT NULL,
 added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, added_by INTEGER NOT NULL REFERENCES users(id), retired_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_blacklist_active ON counterfeit_blacklist(matrix_runout_a,COALESCE(matrix_runout_b,'')) WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_blacklist_matrix ON counterfeit_blacklist(matrix_runout_a,matrix_runout_b);

CREATE TABLE IF NOT EXISTS consignments(
 id INTEGER PRIMARY KEY, agreement_number TEXT NOT NULL UNIQUE, consignor_id INTEGER NOT NULL REFERENCES consignors(id), store_id INTEGER NOT NULL REFERENCES stores(id),
 effective_date TEXT NOT NULL, default_payout_pct REAL NOT NULL CHECK(default_payout_pct BETWEEN 0 AND 100), sale_floor INTEGER NOT NULL DEFAULT 1,
 statement_frequency TEXT NOT NULL DEFAULT 'monthly' CHECK(statement_frequency IN ('monthly','quarterly')), auto_return_days INTEGER NOT NULL DEFAULT 180 CHECK(auto_return_days BETWEEN 30 AND 730),
 predecessor_id INTEGER REFERENCES consignments(id), signature_blob BLOB, finalised_at TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS payout_tiers(
 id INTEGER PRIMARY KEY, consignment_id INTEGER NOT NULL REFERENCES consignments(id) ON DELETE CASCADE, sold_within_days_from INTEGER NOT NULL, sold_within_days_to INTEGER, payout_pct REAL NOT NULL CHECK(payout_pct BETWEEN 0 AND 100),
 CHECK(sold_within_days_to IS NULL OR sold_within_days_to>=sold_within_days_from)
);
CREATE TABLE IF NOT EXISTS inventory(
 id INTEGER PRIMARY KEY, pressing_id INTEGER NOT NULL REFERENCES pressings(id) ON DELETE RESTRICT, store_id INTEGER NOT NULL REFERENCES stores(id), bin_code TEXT NOT NULL CHECK(length(bin_code)<=12),
 media_grade TEXT NOT NULL CHECK(media_grade IN ('M','NM','VG+','VG','G+','G','F','P')), sleeve_grade TEXT NOT NULL CHECK(sleeve_grade IN ('M','NM','VG+','VG','G+','G','F','P')),
 has_original_inner_sleeve INTEGER NOT NULL DEFAULT 0, inserts_included TEXT, asking_price NUMERIC NOT NULL CHECK(asking_price>=0), currency TEXT NOT NULL CHECK(length(currency)=3),
 negotiable INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'in_stock' CHECK(status IN ('in_stock','reserved','sold','consignment','damaged','returned')),
 consignment_id INTEGER REFERENCES consignments(id), acquired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, listed_at TEXT, sold_at TEXT, notes TEXT, deleted_at TEXT, low_stock_threshold INTEGER NOT NULL DEFAULT 2,
 CHECK((status='consignment' AND consignment_id IS NOT NULL) OR status!='consignment'), CHECK((status='sold' AND sold_at IS NOT NULL) OR status!='sold')
);
CREATE INDEX IF NOT EXISTS idx_inventory_pressing_status ON inventory(pressing_id,status);
CREATE INDEX IF NOT EXISTS idx_inventory_store_status ON inventory(store_id,status);
CREATE INDEX IF NOT EXISTS idx_inventory_listed_at ON inventory(listed_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_active_bin ON inventory(store_id,bin_code) WHERE deleted_at IS NULL;
CREATE TABLE IF NOT EXISTS inventory_photos(id INTEGER PRIMARY KEY, inventory_id INTEGER REFERENCES inventory(id) ON DELETE CASCADE, path TEXT NOT NULL, mime TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sold_history(id INTEGER PRIMARY KEY, pressing_id INTEGER NOT NULL REFERENCES pressings(id), sold_price NUMERIC NOT NULL, sold_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS trade_ins(
 id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), store_id INTEGER NOT NULL REFERENCES stores(id), staff_id INTEGER NOT NULL REFERENCES users(id), trade_date TEXT NOT NULL,
 id_type TEXT CHECK(id_type IN ('passport','driving_licence','national_id')), id_number TEXT, offer_mode TEXT NOT NULL CHECK(offer_mode IN ('cash','store_credit')), offer_total NUMERIC NOT NULL CHECK(offer_total>=0),
 currency TEXT NOT NULL CHECK(length(currency)=3), signature_blob BLOB, manager_approval_id INTEGER REFERENCES users(id), notes TEXT, accepted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trade_in_items(
 id INTEGER PRIMARY KEY, trade_in_id INTEGER NOT NULL REFERENCES trade_ins(id) ON DELETE CASCADE, line_no INTEGER NOT NULL, pressing_id INTEGER NOT NULL REFERENCES pressings(id), media_grade TEXT NOT NULL,
 sleeve_grade TEXT NOT NULL, photo_urls TEXT NOT NULL, valuation NUMERIC NOT NULL, override_reason TEXT, matrix_a TEXT, matrix_b TEXT, UNIQUE(trade_in_id,line_no)
);
CREATE INDEX IF NOT EXISTS idx_trade_ins_customer_date ON trade_ins(customer_id,trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_trade_ins_store_date ON trade_ins(store_id,trade_date DESC);

CREATE TABLE IF NOT EXISTS wantlist_entries(
 id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE, artist_query TEXT, title_query TEXT, label_query TEXT, catalogue_query TEXT, year_from INTEGER, year_to INTEGER,
 formats TEXT, countries TEXT, max_price NUMERIC CHECK(max_price IS NULL OR max_price>0), min_media_grade TEXT NOT NULL DEFAULT 'VG' CHECK(min_media_grade IN ('M','NM','VG+','VG','G+','G','F','P')),
 notify_email INTEGER NOT NULL DEFAULT 1, notify_sms INTEGER NOT NULL DEFAULT 0, notify_push INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 100, is_active INTEGER NOT NULL DEFAULT 1,
 notes TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, CHECK(year_to IS NULL OR year_from IS NULL OR year_to>=year_from), CHECK((artist_query IS NOT NULL AND trim(artist_query)<>'') OR (title_query IS NOT NULL AND trim(title_query)<>'')),
 CHECK((notify_email OR notify_sms OR notify_push)=1)
);
CREATE INDEX IF NOT EXISTS idx_wantlist_active_artist ON wantlist_entries(artist_query,is_active);
CREATE INDEX IF NOT EXISTS idx_wantlist_customer ON wantlist_entries(customer_id);
CREATE TABLE IF NOT EXISTS reservations(id INTEGER PRIMARY KEY, inventory_id INTEGER NOT NULL REFERENCES inventory(id) ON DELETE CASCADE, customer_id INTEGER, source TEXT NOT NULL DEFAULT 'wantlist', cart_token TEXT, reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, expires_at TEXT NOT NULL, released_at TEXT);
CREATE INDEX IF NOT EXISTS idx_reservations_inventory_active ON reservations(inventory_id) WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS orders(
 id INTEGER PRIMARY KEY, order_number TEXT NOT NULL UNIQUE, customer_id INTEGER REFERENCES customers(id), store_id INTEGER NOT NULL REFERENCES stores(id), channel TEXT NOT NULL CHECK(channel IN ('pos','web','phone','pre_order')),
 subtotal NUMERIC NOT NULL CHECK(subtotal>=0), discount_total NUMERIC NOT NULL DEFAULT 0 CHECK(discount_total>=0), tax_total NUMERIC NOT NULL DEFAULT 0, shipping_total NUMERIC NOT NULL DEFAULT 0,
 grand_total NUMERIC GENERATED ALWAYS AS (subtotal-discount_total+tax_total+shipping_total) STORED CHECK(grand_total>=0), currency TEXT NOT NULL CHECK(length(currency)=3),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','paid','shipped','collected','refunded','partial_refund','cancelled')), placed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, closed_at TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, type TEXT NOT NULL, actor_id INTEGER REFERENCES users(id), reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, context TEXT);
CREATE TABLE IF NOT EXISTS order_lines(
 id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE, inventory_id INTEGER REFERENCES inventory(id), unit_price NUMERIC NOT NULL, qty INTEGER NOT NULL CHECK(qty>0), line_discount REAL NOT NULL DEFAULT 0 CHECK(line_discount BETWEEN 0 AND 100),
 line_total NUMERIC GENERATED ALWAYS AS (unit_price*qty*(1-line_discount/100.0)) STORED
);
CREATE TABLE IF NOT EXISTS tenders(
 id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE, type TEXT NOT NULL CHECK(type IN ('cash','card','voucher','store_credit','refund')),
 amount NUMERIC NOT NULL CHECK(amount>0 OR type='refund'), card_token TEXT, pan_last4 TEXT, voucher_code TEXT, store_credit_txn TEXT, currency TEXT NOT NULL CHECK(length(currency)=3)
);
CREATE INDEX IF NOT EXISTS idx_orders_customer_date ON orders(customer_id,placed_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_store_date ON orders(store_id,placed_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status_open ON orders(status) WHERE status='open';

CREATE TABLE IF NOT EXISTS preorders(
 id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES records(id), customer_id INTEGER NOT NULL REFERENCES customers(id), quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 5), deposit_amount NUMERIC NOT NULL DEFAULT 0,
 deposit_tender TEXT CHECK(deposit_tender IN ('card','cash','voucher')), ship_address TEXT NOT NULL, notes TEXT, status TEXT NOT NULL DEFAULT 'pending', placed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, release_date TEXT
);
CREATE TABLE IF NOT EXISTS preorder_events(id INTEGER PRIMARY KEY, preorder_id INTEGER NOT NULL REFERENCES preorders(id) ON DELETE CASCADE, action TEXT NOT NULL, payload TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS service_tickets(
 id INTEGER PRIMARY KEY, ticket_number TEXT NOT NULL UNIQUE, customer_id INTEGER NOT NULL REFERENCES customers(id), store_id INTEGER NOT NULL REFERENCES stores(id), equipment_type TEXT NOT NULL CHECK(equipment_type IN ('turntable','cartridge','amp','speaker','other')),
 brand TEXT NOT NULL, model TEXT NOT NULL, serial_number TEXT, symptoms TEXT NOT NULL, intake_checklist TEXT NOT NULL, authorised_limit NUMERIC NOT NULL CHECK(authorised_limit>=0), current_quote NUMERIC NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'received' CHECK(status IN ('received','diagnosing','awaiting_parts','repair','test','ready','collected','awaiting_approval','abandoned')), received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, closed_at TEXT, contact_attempts INTEGER NOT NULL DEFAULT 0 CHECK(contact_attempts BETWEEN 0 AND 10), notes TEXT
);
CREATE TABLE IF NOT EXISTS ticket_events(id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES service_tickets(id) ON DELETE CASCADE, event_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, actor_id INTEGER REFERENCES users(id), action TEXT NOT NULL, payload TEXT);
CREATE TABLE IF NOT EXISTS ticket_parts(id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES service_tickets(id) ON DELETE CASCADE, name TEXT NOT NULL, supplier TEXT, cost NUMERIC NOT NULL CHECK(cost>=0), eta_days INTEGER, status TEXT NOT NULL DEFAULT 'estimated');
CREATE TABLE IF NOT EXISTS ticket_labour(id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES service_tickets(id) ON DELETE CASCADE, hours NUMERIC NOT NULL CHECK(hours>0), rate NUMERIC NOT NULL CHECK(rate>=0), performed_by INTEGER REFERENCES users(id), performed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON service_tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_customer_date ON service_tickets(customer_id,received_at DESC);

CREATE TABLE IF NOT EXISTS consignor_ledger(id INTEGER PRIMARY KEY, consignment_id INTEGER NOT NULL REFERENCES consignments(id), inventory_id INTEGER, sale_date TEXT, sale_price NUMERIC, payout NUMERIC NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS statement_periods(id INTEGER PRIMARY KEY, consignment_id INTEGER NOT NULL REFERENCES consignments(id), period_start TEXT NOT NULL, period_end TEXT NOT NULL, generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(consignment_id,period_start,period_end));
CREATE TABLE IF NOT EXISTS consignment_items(id INTEGER PRIMARY KEY, consignment_id INTEGER NOT NULL REFERENCES consignments(id) ON DELETE CASCADE, inventory_id INTEGER NOT NULL REFERENCES inventory(id), agreed_min_price NUMERIC NOT NULL, per_item_payout_pct REAL, returned_at TEXT);
CREATE INDEX IF NOT EXISTS idx_consignments_consignor ON consignments(consignor_id);
CREATE INDEX IF NOT EXISTS idx_consignment_items_inventory ON consignment_items(inventory_id);

CREATE TABLE IF NOT EXISTS loyalty_ledger(
 id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT, delta_points INTEGER NOT NULL CHECK(delta_points<>0), source TEXT NOT NULL CHECK(source IN ('earn_sale','redeem','expire','manual_adjust','return_reverse')),
 related_order_id INTEGER REFERENCES orders(id), related_redemption_id INTEGER, expires_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, created_by INTEGER NOT NULL REFERENCES users(id), note TEXT CHECK(length(note)<=200)
);
CREATE INDEX IF NOT EXISTS idx_loyalty_customer ON loyalty_ledger(customer_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_loyalty_expires ON loyalty_ledger(expires_at) WHERE expires_at IS NOT NULL AND delta_points>0;
CREATE TABLE IF NOT EXISTS redemptions(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), points INTEGER NOT NULL, reward TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id INTEGER, action TEXT NOT NULL, actor_id INTEGER, context TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type,entity_id,created_at DESC);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, customer_id INTEGER, channel TEXT, subject TEXT, body TEXT, status TEXT DEFAULT 'queued', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS saved_searches(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE, name TEXT NOT NULL, query TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);

CREATE VIRTUAL TABLE IF NOT EXISTS record_search USING fts5(record_id UNINDEXED, title, artist, label, catalogue, runout, content='');
CREATE TABLE IF NOT EXISTS record_search_meta(record_id INTEGER PRIMARY KEY, title TEXT, artist TEXT, label TEXT, catalogue TEXT, runout TEXT);

CREATE TRIGGER IF NOT EXISTS trg_records_validate_reissue BEFORE INSERT ON records
WHEN NEW.is_reissue=0 AND NEW.original_record_id IS NOT NULL
BEGIN SELECT RAISE(ABORT,'reissue lineage invalid'); END;
CREATE TRIGGER IF NOT EXISTS trg_records_validate_year BEFORE INSERT ON records
WHEN NEW.year < 1948 OR NEW.year > CAST(strftime('%Y','now') AS INTEGER)
BEGIN SELECT RAISE(ABORT,'year outside allowed range'); END;
CREATE TRIGGER IF NOT EXISTS trg_inventory_state BEFORE UPDATE OF status ON inventory
WHEN OLD.status='sold' AND NEW.status <> 'sold'
BEGIN SELECT RAISE(ABORT,'sold is terminal'); END;
CREATE TRIGGER IF NOT EXISTS trg_inventory_reserved_before_sale BEFORE UPDATE OF status ON inventory
WHEN NEW.status='sold' AND OLD.status NOT IN ('reserved','in_stock','consignment')
BEGIN SELECT RAISE(ABORT,'invalid inventory state transition'); END;
CREATE TRIGGER IF NOT EXISTS trg_ticket_abandon BEFORE UPDATE OF status ON service_tickets
WHEN NEW.status='abandoned' AND (julianday('now')-julianday(OLD.received_at) < 90 OR OLD.contact_attempts < 2)
BEGIN SELECT RAISE(ABORT,'abandonment policy not met'); END;
'''

class Database:
    def __init__(self,path=DB_PATH):
        self.path=str(path)
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
    def connect(self):
        conn=sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=10000')
        return conn
    def init(self):
        with self.connect() as c:
            c.executescript(SCHEMA)
            self._seed(c)
    def _seed(self,c):
        if c.execute('SELECT 1 FROM stores LIMIT 1').fetchone(): return
        c.execute("INSERT INTO stores(id,name,currency,vat_rate,vat_mode,low_stock_default,compliance_threshold,timezone,role_discount_cap,international_shipping_fee) VALUES(1,'Vintage Vinyl HQ','USD',0.10,'included',2,500,'UTC',10,20)")
        c.executemany("INSERT INTO users(id,name,email,role,store_id) VALUES(?,?,?,?,1)",[(1,'Admin','admin@example.com','admin'),(2,'Maya Staff','staff@example.com','staff'),(3,'Jordan Manager','manager@example.com','manager')])
        artists=['The Velvet Echoes','Mina Rhodes','Blue Harbour Trio','The Paper Planes']
        labels=['Moonlight Records','North Star Audio','Sunset Pressings']
        c.executemany('INSERT INTO artists(name) VALUES(?)',[(x,) for x in artists]); c.executemany('INSERT INTO labels(name) VALUES(?)',[(x,) for x in labels])
        c.executemany('INSERT INTO customers(id,email,phone,display_name,country_code,marketing_opt_in,tier) VALUES(?,?,?,?,?,?,?)',[(1,'haru@example.com','555-0101','Haru Customer','US',1,'gold'),(2,'collector@example.com','555-0102','Alex Collector','GB',1,'silver')])
        c.executemany('INSERT INTO consignors(id,name,email,phone) VALUES(?,?,?,?)',[(1,'North Side Estate','estate@example.com','555-0210'),(2,'Blue Room Records','blueroom@example.com','555-0220')])
        for name,year,title,artist,label,cat,price,genre,grade,country in [
            ('lp',1977,'Midnight Signal','The Velvet Echoes','Moonlight Records','ML-101',39.99,'Rock','NM','US'),
            ('single',1982,'Neon Run','Mina Rhodes','North Star Audio','NSA-22',12.50,'Pop','VG+','GB'),
            ('lp',1969,'Blue Harbour Live','Blue Harbour Trio','Sunset Pressings','SP-69',55.00,'Jazz','VG','US'),
            ('coloured',2021,'Paper Planes Deluxe','The Paper Planes','Moonlight Records','ML-2021',28.00,'Indie','M','DE')]:
            aid=c.execute('SELECT id FROM artists WHERE name=?',(artist,)).fetchone()['id']; lid=c.execute('SELECT id FROM labels WHERE name=?',(label,)).fetchone()['id']
            fmt={'lp':'12in_lp','single':'7in','coloured':'coloured'}[name]; rpm=45 if fmt=='7in' else 33
            c.execute('INSERT INTO records(artist_id,title,label_id,catalogue_number,country_code,year,format,rpm,mono_stereo,weight_grams,pre_order,release_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(aid,title,lid,cat,country,year,fmt,rpm,'stereo',180,1 if year==2021 else 0,(date.today()+timedelta(days=15)).isoformat() if year==2021 else None))
            rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO record_genres(record_id,genre) VALUES(?,?)',(rid,genre))
            c.execute('INSERT INTO pressings(record_id,pressing_plant,matrix_runout_a,matrix_runout_b,press_year,is_first_pressing,is_promo) VALUES(?,?,?,?,?,?,?)',(rid,'Demo Plant',f'{cat}-A',f'{cat}-B',year,1,0))
            pid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO inventory(pressing_id,store_id,bin_code,media_grade,sleeve_grade,has_original_inner_sleeve,asking_price,currency,status,listed_at,low_stock_threshold) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(pid,1,f'BIN-{rid:03d}',grade,grade,1,price,'USD','in_stock',datetime.now().isoformat(),2))
            iid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO sold_history(pressing_id,sold_price) VALUES(?,?)',(pid,price*.9))
            c.execute('INSERT INTO sold_history(pressing_id,sold_price) VALUES(?,?)',(pid,price*1.1))
        c.execute("INSERT INTO counterfeit_blacklist(matrix_runout_a,matrix_runout_b,artist_name,title,reason,source_authority,added_by) VALUES('FAKE-001','FAKE-002','Fake Artist','Counterfeit Demo','Known mismatch','Demo Authority',1)")
        # first wantlist and service ticket demo
        c.execute("INSERT INTO wantlist_entries(customer_id,artist_query,title_query,max_price,min_media_grade,notify_email,priority,notes) VALUES(1,'The Velvet Echoes','Midnight Signal',60,'VG',1,10,'Demo wantlist')")
        c.execute("INSERT INTO service_tickets(ticket_number,customer_id,store_id,equipment_type,brand,model,symptoms,intake_checklist,authorised_limit) VALUES('STK-%s-0001',1,1,'turntable','AudioCraft','AC-1200','Hums at 33 RPM','{\"powers_on\":true,\"platter_spins\":true,\"arm_balanced\":false,\"stylus_inspected\":false,\"cosmetic_damage_noted\":false}',300)" % date.today().year)
        c.execute("INSERT INTO ticket_events(ticket_id,actor_id,action,payload) VALUES(last_insert_rowid(),2,'received','{}')")
        self.rebuild_fts(c)
    def rebuild_fts(self,c):
        c.execute('DELETE FROM record_search'); c.execute('DELETE FROM record_search_meta')
        rows=c.execute('''SELECT r.id, r.title, a.name artist, l.name label, r.catalogue_number, GROUP_CONCAT(COALESCE(p.matrix_runout_a,'')||' '||COALESCE(p.matrix_runout_b,''),' ') runout FROM records r JOIN artists a ON a.id=r.artist_id JOIN labels l ON l.id=r.label_id LEFT JOIN pressings p ON p.record_id=r.id WHERE r.deleted_at IS NULL GROUP BY r.id''').fetchall()
        for x in rows:
            c.execute('INSERT INTO record_search(rowid,record_id,title,artist,label,catalogue,runout) VALUES(?,?,?,?,?,?,?)',(x['id'],x['id'],x['title'],x['artist'],x['label'],x['catalogue_number'],x['runout'] or ''))
            c.execute('INSERT INTO record_search_meta(record_id,title,artist,label,catalogue,runout) VALUES(?,?,?,?,?,?)',(x['id'],x['title'],x['artist'],x['label'],x['catalogue_number'],x['runout'] or ''))

DB=Database()

def qdict(rows): return [dict(r) for r in rows]

class Tx:
    def __init__(self,db=DB): self.db=db; self.conn=None
    def __enter__(self):
        self.conn=self.db.connect(); self.conn.execute('BEGIN IMMEDIATE'); return self.conn
    def __exit__(self,exc_type,exc,val):
        if exc_type: self.conn.rollback()
        else: self.conn.commit()
        self.conn.close()

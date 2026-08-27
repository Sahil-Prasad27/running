from __future__ import annotations
from flask import Flask, jsonify, request, render_template, send_file, Response
from flask_socketio import SocketIO
from pathlib import Path
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
import sqlite3, json, csv, io, os, logging, uuid
from werkzeug.utils import secure_filename

from services.db import DB
from services.exceptions import *
from services.domain import *
from services.importer import CatalogueImporter
from services.receipt import ReceiptDispatcher

BASE_DIR=Path(__file__).resolve().parent
UPLOAD_DIR=BASE_DIR/'uploads'; UPLOAD_DIR.mkdir(parents=True,exist_ok=True)
STORAGE_DIR=BASE_DIR/'storage'; STORAGE_DIR.mkdir(parents=True,exist_ok=True)
LOG_DIR=STORAGE_DIR/'logs'; LOG_DIR.mkdir(parents=True,exist_ok=True)
logging.basicConfig(filename=LOG_DIR/'app.log',level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')

app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH']=6*8*1024*1024
app.config['SECRET_KEY']=os.environ.get('SECRET_KEY','dev-secret-change-me')
socketio=SocketIO(app,cors_allowed_origins='*',async_mode='threading')
DB.init()

def conn(): return DB.connect()
def now_iso(): return datetime.now().isoformat(timespec='seconds')
def jresp(data,status=200): return jsonify(data),status
def current_user_id(): return int(request.headers.get('X-User-Id','2'))
def current_role():
    with conn() as c:
        x=c.execute('SELECT role FROM users WHERE id=?',(current_user_id(),)).fetchone(); return x['role'] if x else 'staff'
def store_config():
    with conn() as c: return dict(c.execute('SELECT * FROM stores WHERE id=1').fetchone())
def audit(entity,entity_id,action,context=None,actor=None):
    try:
        with conn() as c: c.execute('INSERT INTO audit_log(entity_type,entity_id,action,actor_id,context) VALUES(?,?,?,?,?)',(entity,entity_id,action,actor or current_user_id(),json.dumps(context or {})))
    except Exception: logging.exception('audit failed')
def error_response(e):
    code=getattr(e,'errorCode','INTERNAL_ERROR')
    status=400 if isinstance(e,VinylException) else 500
    if not isinstance(e,VinylException): logging.exception('unhandled error')
    return jsonify({'ok':False,'errorCode':code,'message':str(e),'context':getattr(e,'context',{})}),status
def send_pdf_download(payload,filename):
    stream=payload if hasattr(payload,'read') else io.BytesIO(payload)
    stream.seek(0)
    return send_file(stream,mimetype='application/pdf',download_name=filename,as_attachment=True)
def render_dashboard_pdf(rows):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf=io.BytesIO(); pdf=canvas.Canvas(buf,pagesize=A4); width,height=A4; y=height-48
    def header():
        nonlocal y
        pdf.setFont('Helvetica-Bold',16); pdf.drawString(40,y,'Vintage Vinyl - Daily Sales Report'); y-=18
        pdf.setFont('Helvetica',9); pdf.drawString(40,y,f'Generated {now_iso()}'); y-=24
        pdf.setFont('Helvetica-Bold',10); pdf.drawString(40,y,'Order'); pdf.drawString(180,y,'Placed At'); pdf.drawRightString(width-130,y,'Status'); pdf.drawRightString(width-40,y,'Total'); y-=10
        pdf.line(40,y,width-40,y); y-=18; pdf.setFont('Helvetica',9)
    header()
    if not rows: pdf.drawString(40,y,'No paid orders were recorded for this day.')
    for row in rows:
        if y<56: pdf.showPage(); y=height-48; header()
        pdf.drawString(40,y,str(row['order_number'])[:22]); pdf.drawString(180,y,str(row['placed_at'])[:19]); pdf.drawRightString(width-130,y,str(row['status']).upper()[:18]); pdf.drawRightString(width-40,y,f"${float(row['grand_total']):.2f}"); y-=14
    pdf.save(); buf.seek(0); return buf

def normalize_money(v):
    try: return Decimal(str(v).replace(',','.'))
    except Exception: raise ValueError('Invalid monetary amount')
def decade_for(year): return f"{(int(year)//10)*10}s"
def grade_at_least(actual,minimum): return GRADE_RANK.get(actual,99) <= GRADE_RANK.get(minimum,7)
def active_blacklist_match(c,matrix_a,matrix_b):
    if not matrix_a: return False
    return c.execute("SELECT 1 FROM counterfeit_blacklist WHERE retired_at IS NULL AND UPPER(trim(matrix_runout_a))=UPPER(trim(?)) AND COALESCE(UPPER(trim(matrix_runout_b)),'')=COALESCE(UPPER(trim(?)),'')",(matrix_a,matrix_b)).fetchone() is not None

def record_search_rows(c,query='',limit=50):
    if query.strip():
        tokens=' OR '.join(query.split()) or query
        ids=[r['rowid'] for r in c.execute("SELECT rowid FROM record_search WHERE record_search MATCH ? ORDER BY rank LIMIT ?",(tokens,limit)).fetchall()]
        if not ids:return []
        placeholders=','.join('?'*len(ids)); args=ids
        return c.execute(f'''SELECT r.id,r.title,a.name artist,l.name label,r.catalogue_number,r.country_code,r.year,r.format,r.rpm,r.mono_stereo,r.pre_order,r.release_date,
            MIN(i.asking_price) price, MIN(i.media_grade) media_grade, GROUP_CONCAT(DISTINCT rg.genre) genres, COUNT(CASE WHEN i.status IN ('in_stock','reserved','consignment') THEN 1 END) stock
            FROM records r JOIN artists a ON a.id=r.artist_id JOIN labels l ON l.id=r.label_id LEFT JOIN record_genres rg ON rg.record_id=r.id LEFT JOIN pressings p ON p.record_id=r.id LEFT JOIN inventory i ON i.pressing_id=p.id
            WHERE r.id IN ({placeholders}) AND r.deleted_at IS NULL GROUP BY r.id ORDER BY r.year DESC''',args).fetchall()
    return c.execute('''SELECT r.id,r.title,a.name artist,l.name label,r.catalogue_number,r.country_code,r.year,r.format,r.rpm,r.mono_stereo,r.pre_order,r.release_date,
        MIN(i.asking_price) price, MIN(i.media_grade) media_grade, GROUP_CONCAT(DISTINCT rg.genre) genres, COUNT(CASE WHEN i.status IN ('in_stock','reserved','consignment') THEN 1 END) stock
        FROM records r JOIN artists a ON a.id=r.artist_id JOIN labels l ON l.id=r.label_id LEFT JOIN record_genres rg ON rg.record_id=r.id LEFT JOIN pressings p ON p.record_id=r.id LEFT JOIN inventory i ON i.pressing_id=p.id
        WHERE r.deleted_at IS NULL GROUP BY r.id ORDER BY r.year DESC LIMIT ?''',(limit,)).fetchall()

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/meta')
def meta():
    cfg=store_config()
    with conn() as c:
        return jsonify({'ok':True,'grades':GRADES,'grade_defs':GRADE_DEFS,'formats':FORMAT_VALUES,'format_labels':FORMAT_LABELS,'rpm':[33,45,78],
                        'artists':[dict(x) for x in c.execute('SELECT id,name FROM artists ORDER BY name')], 'labels':[dict(x) for x in c.execute('SELECT id,name FROM labels ORDER BY name')],
                        'customers':[dict(x) for x in c.execute('SELECT id,display_name,email,country_code,tier FROM customers ORDER BY display_name')], 'consignors':[dict(x) for x in c.execute('SELECT * FROM consignors ORDER BY name')], 'store':cfg})

@app.route('/api/dashboard')
def dashboard():
    with conn() as c:
        today=date.today().isoformat(); start=today+'T00:00:00'; end=(date.today()+timedelta(days=1)).isoformat()+'T00:00:00'
        row=c.execute('''SELECT COUNT(*) transactions, COALESCE(SUM(subtotal),0) gross, COALESCE(SUM(subtotal-discount_total),0) net, COALESCE(AVG(grand_total),0) avg_basket FROM orders WHERE placed_at>=? AND placed_at<? AND status IN ('paid','shipped','collected','partial_refund','refunded')''',(start,end)).fetchone()
        new_customers=c.execute('SELECT COUNT(*) FROM customers WHERE created_at>=? AND created_at<?',(start,end)).fetchone()[0]
        want_matches=c.execute("SELECT COUNT(*) FROM notifications WHERE created_at>=? AND created_at<?",(start,end)).fetchone()[0]
        preorders=c.execute('SELECT COUNT(*) FROM preorders WHERE placed_at>=? AND placed_at<?',(start,end)).fetchone()[0]
        hourly=c.execute("SELECT substr(placed_at,12,2) h, COALESCE(SUM(grand_total),0) total FROM orders WHERE placed_at>=? AND placed_at<? AND status NOT IN ('cancelled') GROUP BY h ORDER BY h",(start,end)).fetchall()
        genre=c.execute('''SELECT COALESCE(rg.genre,'Other') genre, SUM(ol.line_total) total FROM order_lines ol JOIN orders o ON o.id=ol.order_id LEFT JOIN inventory i ON i.id=ol.inventory_id LEFT JOIN pressings p ON p.id=i.pressing_id LEFT JOIN record_genres rg ON rg.record_id=p.record_id WHERE o.placed_at>=? AND o.placed_at<? AND o.status NOT IN ('cancelled') GROUP BY genre ORDER BY total DESC''',(start,end)).fetchall()
        top=c.execute('''SELECT ol.inventory_id,i.asking_price, SUM(ol.qty) qty, r.title,a.name artist, SUM(ol.line_total) total FROM order_lines ol JOIN orders o ON o.id=ol.order_id JOIN inventory i ON i.id=ol.inventory_id JOIN pressings p ON p.id=i.pressing_id JOIN records r ON r.id=p.record_id JOIN artists a ON a.id=r.artist_id WHERE o.placed_at>=? AND o.placed_at<? GROUP BY ol.inventory_id ORDER BY qty DESC,total DESC LIMIT 10''',(start,end)).fetchall()
        low=c.execute('''SELECT i.id,i.bin_code,i.asking_price,i.low_stock_threshold,r.title,a.name artist FROM inventory i JOIN pressings p ON p.id=i.pressing_id JOIN records r ON r.id=p.record_id JOIN artists a ON a.id=r.artist_id WHERE i.status IN ('in_stock','reserved','consignment') GROUP BY p.record_id HAVING COUNT(CASE WHEN i.status='in_stock' THEN 1 END) <= MAX(i.low_stock_threshold) ORDER BY COUNT(CASE WHEN i.status='in_stock' THEN 1 END)''').fetchall()
        due=c.execute('''SELECT cl.id,cl.payout,co.agreement_number,c.name consignor FROM consignor_ledger cl JOIN consignments co ON co.id=cl.consignment_id JOIN consignors c ON c.id=co.consignor_id WHERE cl.sale_date>=? ORDER BY cl.sale_date DESC LIMIT 20''',(today,)).fetchall()
        return jsonify({'ok':True,'kpi':dict(row)|{'new_customers':new_customers,'wantlist_matches':want_matches,'preorders_received':preorders},'hourly':[dict(x) for x in hourly],'genre':[dict(x) for x in genre],'top_items':[dict(x) for x in top],'low_stock':[dict(x) for x in low],'payouts_due':[dict(x) for x in due]})


@app.route('/api/dashboard/export')
def dashboard_export():
    # Export the same current-day order dataset used by the dashboard.
    with conn() as c:
        today=date.today().isoformat(); start=today+'T00:00:00'; end=(date.today()+timedelta(days=1)).isoformat()+'T00:00:00'
        rows=c.execute('SELECT order_number,placed_at,grand_total,status FROM orders WHERE placed_at>=? AND placed_at<? ORDER BY placed_at',(start,end)).fetchall()
    fmt=request.args.get('format','csv')
    if fmt=='csv':
        out=io.StringIO(); w=csv.writer(out); w.writerow(['order_number','placed_at','grand_total','status']); [w.writerow([r['order_number'],r['placed_at'],r['grand_total'],r['status']]) for r in rows]
        return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=daily_sales.csv'})
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        buf=io.BytesIO(); cpdf=canvas.Canvas(buf,pagesize=A4); y=800; cpdf.setFont('Helvetica-Bold',16); cpdf.drawString(40,y,'Vintage Vinyl · Daily Sales'); y-=30; cpdf.setFont('Helvetica',9)
        for r in rows[:35]: cpdf.drawString(40,y,f"{r['order_number']}  {r['placed_at']}  {float(r['grand_total']):.2f}  {r['status']}"); y-=14
        cpdf.save(); buf.seek(0); return send_file(buf,mimetype='application/pdf',download_name='daily_sales.pdf')
    except Exception as e: return error_response(e)

@app.route('/api/records',methods=['GET'])
def records():
    with conn() as c:
        rows=record_search_rows(c,request.args.get('q',''),100)
        grade=request.args.get('grade'); country=request.args.get('country'); fmt=request.args.get('format'); decade=request.args.get('decade'); genre=request.args.get('genre'); min_price=request.args.get('min_price'); max_price=request.args.get('max_price'); in_stock=request.args.get('in_stock')
        data=[dict(r) for r in rows]
        if grade:data=[r for r in data if r['media_grade'] and grade_at_least(r['media_grade'],grade)]
        if country:data=[r for r in data if r['country_code']==country.upper()]
        if fmt:data=[r for r in data if r['format']==fmt]
        if decade:data=[r for r in data if str(r['year'])[:3]==str(decade)[:3]]
        if genre:data=[r for r in data if genre in (r['genres'] or '')]
        if min_price:data=[r for r in data if r['price'] is not None and float(r['price'])>=float(min_price)]
        if max_price:data=[r for r in data if r['price'] is not None and float(r['price'])<=float(max_price)]
        if in_stock=='1':data=[r for r in data if r['stock']>0]
        sort=request.args.get('sort','year_desc'); data=sorted(data,key=lambda r: r.get('year') or 0,reverse=True) if sort=='year_desc' else sorted(data,key=lambda r: r.get('price') or 0,reverse=sort=='price_desc')
        return jsonify({'ok':True,'items':data,'count':len(data),'facets':{'grades':GRADES,'genres':sorted({g for r in data for g in (r['genres'] or '').split(',') if g})}})

@app.route('/api/records/<int:rid>')
def record_detail(rid):
    with conn() as c:
        r=c.execute('''SELECT r.*,a.name artist,l.name label,GROUP_CONCAT(DISTINCT rg.genre) genres FROM records r JOIN artists a ON a.id=r.artist_id JOIN labels l ON l.id=r.label_id LEFT JOIN record_genres rg ON rg.record_id=r.id WHERE r.id=? GROUP BY r.id''',(rid,)).fetchone()
        if not r:return jresp({'error':'not found'},404)
        press=c.execute('SELECT * FROM pressings WHERE record_id=? AND deleted_at IS NULL ORDER BY press_year',(rid,)).fetchall()
        inv=c.execute('''SELECT i.*,p.matrix_runout_a,p.matrix_runout_b FROM inventory i JOIN pressings p ON p.id=i.pressing_id WHERE p.record_id=? AND i.deleted_at IS NULL ORDER BY i.acquired_at DESC''',(rid,)).fetchall()
        return jsonify({'ok':True,'record':dict(r),'pressings':[dict(x) for x in press],'inventory':[dict(x) for x in inv]})

@app.route('/api/records',methods=['POST'])
def create_record():
    data=request.get_json(silent=True) or {}
    try:
        year=int(data.get('year')); rpm=int(data.get('rpm')); fmt=data.get('format'); title=str(data.get('title','')).strip(); cat=str(data.get('catalogue_number','')).strip(); country=str(data.get('country_code','')).strip().upper()
        if not title or len(title)>200: raise ValueError('title is required and max 200 chars')
        if not 1948<=year<=date.today().year: raise ValueError('year outside allowed range')
        if rpm not in RPM_VALUES: raise ValueError('rpm invalid')
        if fmt not in FORMAT_VALUES: raise ValueError('format invalid')
        aid=data.get('artist_id'); lid=data.get('label_id')
        with DB.connect() as c:
            if data.get('artist_name') and not aid:
                ex=c.execute('SELECT id FROM artists WHERE lower(name)=lower(?)',(data['artist_name'].strip(),)).fetchone();
                if ex: aid=ex[0]
                elif data.get('create_artist'): c.execute('INSERT INTO artists(name) VALUES(?)',(data['artist_name'].strip(),)); aid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                else: raise UnknownArtistException('Artist could not be resolved',{'artist':data['artist_name']})
            if data.get('label_name') and not lid:
                ex=c.execute('SELECT id FROM labels WHERE lower(name)=lower(?)',(data['label_name'].strip(),)).fetchone();
                if ex: lid=ex[0]
                elif data.get('create_label'): c.execute('INSERT INTO labels(name) VALUES(?)',(data['label_name'].strip(),)); lid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            if not aid or not lid: raise ValueError('artist and label required')
            dup=c.execute('SELECT id FROM records WHERE label_id=? AND catalogue_number=? AND country_code=? AND deleted_at IS NULL',(aid if False else lid,cat,country)).fetchone()
            if dup: raise DuplicateCatalogueException('Duplicate label + catalogue + country',{'existing_record_id':dup[0]})
            is_reissue=1 if data.get('is_reissue') else 0; original=data.get('original_record_id')
            if is_reissue and not original: raise ValueError('reissue requires original_record_id')
            if not is_reissue and original: raise ValueError('non-reissue cannot set original_record_id')
            if fmt=='box_set' and len(data.get('included_records',[]))<2: raise ValueError('BoxSet requires at least 2 included records')
            c.execute('''INSERT INTO records(artist_id,title,label_id,catalogue_number,country_code,year,format,rpm,mono_stereo,weight_grams,is_reissue,original_record_id,pre_order,release_date,deposit_policy,per_customer_cap) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(aid,title,lid,cat,country,year,fmt,rpm,data.get('mono_stereo','unknown'),data.get('weight_grams'),is_reissue,original,1 if data.get('pre_order') else 0,data.get('release_date'),data.get('deposit_policy','optional'),min(int(data.get('per_customer_cap',5)),5)))
            rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            for g in data.get('genres',[]): c.execute('INSERT OR IGNORE INTO record_genres(record_id,genre) VALUES(?,?)',(rid,g.strip()))
            matrix_a=data.get('matrix_runout_a'); matrix_b=data.get('matrix_runout_b')
            c.execute('INSERT INTO pressings(record_id,matrix_runout_a,matrix_runout_b,press_year,is_first_pressing,is_promo,catalogue_variant,notes) VALUES(?,?,?,?,?,?,?,?)',(rid,matrix_a,matrix_b,year,1 if data.get('is_first_pressing') else 0,1 if data.get('is_promo') else 0,data.get('catalogue_variant'),data.get('pressing_notes')))
            pid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            grade_service=GradingService(GoldmineRules(True)); grading=Grading(data.get('media_grade','VG'),data.get('sleeve_grade','VG'),bool(data.get('has_original_inner_sleeve')),data.get('defects',[])); grading=grade_service.validate(grading)
            cfg=store_config(); price=normalize_money(data.get('asking_price',0));
            c.execute('INSERT INTO inventory(pressing_id,store_id,bin_code,media_grade,sleeve_grade,has_original_inner_sleeve,inserts_included,asking_price,currency,negotiable,status,listed_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(pid,1,str(data.get('bin_code','')).strip(),grading.media,grading.sleeve,1 if data.get('has_original_inner_sleeve') else 0,json.dumps(data.get('inserts',[])),float(price),cfg['currency'],1 if data.get('negotiable') else 0,'in_stock',now_iso(),str(data.get('notes',''))[:1000]))
            iid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            for ph in data.get('photo_paths',[])[:6]: c.execute('INSERT INTO inventory_photos(inventory_id,path,mime) VALUES(?,?,?)',(iid,ph,'image/*'))
            # FTS rebuild
            DB.rebuild_fts(c); audit('inventory',iid,'listed',{'record_id':rid,'grade_note':grading.explanation})
        notify_matches(rid,pid,iid)
        socketio.emit('inventory_listed',{'record_id':rid,'inventory_id':iid},namespace='/')
        return jsonify({'ok':True,'record_id':rid,'inventory_id':iid,'decade':decade_for(year),'grading_explanation':grading.explanation,'warning':duplicate_matrix_warning(matrix_a,matrix_b)})
    except Exception as e: return error_response(e)

def duplicate_matrix_warning(a,b):
    if not a and not b:return None
    with conn() as c:
        x=c.execute("SELECT p.id,r.id record_id,r.title,a.name artist FROM pressings p JOIN records r ON r.id=p.record_id JOIN artists a ON a.id=r.artist_id WHERE UPPER(COALESCE(p.matrix_runout_a,''))=UPPER(COALESCE(?,'')) AND UPPER(COALESCE(p.matrix_runout_b,''))=UPPER(COALESCE(?,'')) LIMIT 1",(a,b)).fetchone()
        return dict(x) if x else None

def notify_matches(rid,pid,iid):
    with conn() as c:
        x=c.execute('''SELECT i.id,r.id record_id,r.title,a.name artist,l.name label,r.year,r.format,r.country_code,i.media_grade,i.asking_price,w.id wish_id,w.customer_id,w.notify_email,w.notify_sms,w.notify_push
        FROM inventory i JOIN pressings p ON p.id=i.pressing_id JOIN records r ON r.id=p.record_id JOIN artists a ON a.id=r.artist_id JOIN labels l ON l.id=r.label_id JOIN wantlist_entries w ON w.is_active=1
        WHERE i.id=? AND ((lower(w.artist_query)=lower(a.name)) OR (w.artist_query IS NOT NULL AND lower(a.name) LIKE lower('%'||w.artist_query||'%'))) AND (w.title_query IS NULL OR lower(r.title) LIKE lower('%'||w.title_query||'%'))
        AND (w.max_price IS NULL OR i.asking_price<=w.max_price) AND grade_rank(i.media_grade)<=grade_rank(w.min_media_grade)''',(iid,)).fetchall() if False else []
        # SQLite scalar grade_rank isn't installed; use Python matching instead.
        inv=c.execute('''SELECT i.id,r.title,a.name artist,r.year,r.format,r.country_code,i.media_grade,i.asking_price FROM inventory i JOIN pressings p ON p.id=i.pressing_id JOIN records r ON r.id=p.record_id JOIN artists a ON a.id=r.artist_id WHERE i.id=?''',(iid,)).fetchone()
        if not inv:return
        wishes=c.execute('SELECT * FROM wantlist_entries WHERE is_active=1').fetchall()
        for w in wishes:
            artist_ok=w['artist_query'] and w['artist_query'].lower() in inv['artist'].lower(); title_ok=not w['title_query'] or w['title_query'].lower() in inv['title'].lower()
            year_ok=(w['year_from'] is None or inv['year']>=w['year_from']) and (w['year_to'] is None or inv['year']<=w['year_to'])
            price_ok=w['max_price'] is None or float(inv['asking_price'])<=float(w['max_price'])
            grade_ok=grade_at_least(inv['media_grade'],w['min_media_grade'])
            fmt_ok=not w['formats'] or inv['format'] in json.loads(w['formats']) if str(w['formats']).startswith('[') else True
            country_ok=not w['countries'] or inv['country_code'] in json.loads(w['countries']) if str(w['countries']).startswith('[') else True
            if artist_ok and title_ok and year_ok and price_ok and grade_ok and fmt_ok and country_ok:
                for ch,flag in [('email','notify_email'),('sms','notify_sms'),('push','notify_push')]:
                    if w[flag]: c.execute('INSERT INTO notifications(customer_id,channel,subject,body,status) VALUES(?,?,?,?,?)',(w['customer_id'],ch,'Wantlist match',f"{inv['artist']} - {inv['title']} is now listed.",'queued'))

@app.route('/api/records/<int:rid>/pricing')
def pricing(rid):
    with conn() as c:
        rows=c.execute('''SELECT sh.sold_price FROM sold_history sh JOIN pressings p ON p.id=sh.pressing_id WHERE p.record_id=? ORDER BY sh.sold_at DESC LIMIT 30''',(rid,)).fetchall(); vals=[Decimal(str(x[0])) for x in rows]
        if not vals:return jsonify({'ok':True,'low':0,'mid':0,'high':0,'confidence':'low confidence'})
        mid=sum(vals)/Decimal(len(vals)); return jsonify({'ok':True,'low':float(mid*.8),'mid':float(mid),'high':float(mid*1.2),'confidence':'high' if len(vals)>=5 else 'low confidence'})

@app.route('/api/grading/validate',methods=['POST'])
def grading_validate():
    d=request.get_json(silent=True) or {}; g=Grading(d.get('media'),d.get('sleeve'),bool(d.get('has_original_inner_sleeve')),d.get('defects',[]));
    try:
        out=GradingService(GoldmineRules(bool(d.get('allow_downgrade',True)))).validate(g); return jsonify({'ok':True,'grading':out.__dict__,'explain':GradingService(GoldmineRules()).explain(out),'grade_defs':GRADE_DEFS})
    except Exception as e:return error_response(e)

@app.route('/api/trade-ins',methods=['POST'])
def trade_in_create():
    d=request.get_json(silent=True) or {}; rows=d.get('rows',[])
    try:
        if not rows: raise ValueError('At least one record row is required')
        customer_id=d.get('customer_id'); offer_mode=d.get('offer_mode','cash'); signature=d.get('signature','').strip(); accepted=bool(d.get('customer_accepts'))
        if accepted and not signature: raise ValueError('Signature is mandatory before finalise')
        cfg=store_config(); valuator=TradeInValuator(lambda pid: [r[0] for r in []])
        valuations=[]
        with conn() as c:
            for row in rows:
                if len(row.get('photos',[]))<1: raise ValueError('Each trade-in row requires at least one photo')
                matrix_a=row.get('matrix_a','').strip(); matrix_b=row.get('matrix_b','').strip()
                if active_blacklist_match(c,matrix_a,matrix_b): raise CounterfeitPressingException('Counterfeit pressing - do not accept')
                pressing_id=int(row['pressing_id']); hist=[x[0] for x in c.execute('SELECT sold_price FROM sold_history WHERE pressing_id=? ORDER BY sold_at DESC LIMIT 30',(pressing_id,)).fetchall()]
                base=Decimal(str(sum(map(float,hist))/len(hist)*.65)) if len(hist)>=5 else Decimal(str(row.get('fallback_value',20)))
                if len(hist)<5: confidence='low confidence'
                else: confidence='high'
                base*=Decimal(str(TradeInValuator.BASE[row['media_grade']]))/Decimal('40')
                valuations.append((pressing_id,base.quantize(Decimal('.01')),confidence,row))
            offer=sum((x[1] for x in valuations),Decimal('0'))
            overrides=[x for x in valuations if x[3].get('override_value') is not None]
            for _,_,_,r in overrides:
                reason=str(r.get('override_reason','')).strip()
                if len(reason)<20: raise ValuationOverrideException('Override reason must be at least 20 characters')
                offer=offer-base+normalize_money(r['override_value'])
            if offer_mode=='store_credit': offer=(offer*Decimal('1.20')).quantize(Decimal('.01'))
            if offer>Decimal(str(cfg['compliance_threshold'])) and not d.get('id_type'): raise ValueError('Compliance error: ID verification required above threshold')
            if customer_id:
                cutoff=(date.today()-timedelta(days=30)).isoformat(); count=c.execute('SELECT COUNT(*) FROM trade_ins WHERE customer_id=? AND trade_date>=?',(customer_id,cutoff)).fetchone()[0]
                if count>50 and not d.get('manager_approval_id'): raise ValueError('Possible commercial seller: manager approval required')
            if offer_mode=='store_credit' and not customer_id: raise ValueError('Store credit requires registered customer')
            c.execute('INSERT INTO trade_ins(customer_id,store_id,staff_id,trade_date,id_type,id_number,offer_mode,offer_total,currency,signature_blob,manager_approval_id,notes,accepted) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(customer_id,1,current_user_id(),date.today().isoformat(),d.get('id_type'),d.get('id_number'),offer_mode,float(offer),cfg['currency'],signature.encode() if signature else None,d.get('manager_approval_id'),str(d.get('notes',''))[:500],1 if accepted else 0))
            tid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            for idx,(pid,val,conf,r) in enumerate(valuations,1): c.execute('INSERT INTO trade_in_items(trade_in_id,line_no,pressing_id,media_grade,sleeve_grade,photo_urls,valuation,override_reason,matrix_a,matrix_b) VALUES(?,?,?,?,?,?,?,?,?,?)',(tid,idx,pid,r['media_grade'],r['sleeve_grade'],json.dumps(r.get('photos',[])),float(r.get('override_value') or val),r.get('override_reason'),r.get('matrix_a'),r.get('matrix_b')))
            audit('trade_in',tid,'created',{'offer_total':float(offer)})
        return jsonify({'ok':True,'trade_in_id':tid,'offer_total':float(offer),'valuations':[{'pressing_id':x[0],'mid':float(x[1]),'low':float(x[1]*Decimal('.8')),'high':float(x[1]*Decimal('1.2')),'confidence':x[2]} for x in valuations]})
    except Exception as e:return error_response(e)

@app.route('/api/wantlists',methods=['GET','POST'])
def wantlists():
    if request.method=='GET':
        with conn() as c:return jsonify({'ok':True,'items':[dict(x) for x in c.execute('''SELECT w.*,c.display_name FROM wantlist_entries w JOIN customers c ON c.id=w.customer_id ORDER BY w.priority ASC,w.created_at DESC''').fetchall()]})
    d=request.get_json(silent=True) or {}; cid=d.get('customer_id') or 1
    try:
        with conn() as c:
            if not d.get('artist') and not d.get('title'): raise ValueError('Artist or title is required')
            if d.get('year_from') and d.get('year_to') and int(d['year_from'])>int(d['year_to']): raise ValueError('Year range is invalid')
            if not any(d.get(x) for x in ('notify_email','notify_sms','notify_push')): raise ValueError('At least one notification channel must be selected')
            active=c.execute('SELECT COUNT(*) FROM wantlist_entries WHERE customer_id=? AND is_active=1',(cid,)).fetchone()[0]
            cust=c.execute('SELECT wantlist_justification FROM customers WHERE id=?',(cid,)).fetchone()
            if active>=200 and not (d.get('justification') or (cust and cust['wantlist_justification'])): raise ValueError('More than 200 active entries require justification')
            c.execute('INSERT INTO wantlist_entries(customer_id,artist_query,title_query,label_query,catalogue_query,year_from,year_to,formats,countries,max_price,min_media_grade,notify_email,notify_sms,notify_push,priority,is_active,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(cid,d.get('artist'),d.get('title'),d.get('label'),d.get('catalogue'),d.get('year_from'),d.get('year_to'),json.dumps(d.get('formats',[])),json.dumps(d.get('countries',[])),d.get('max_price'),d.get('min_media_grade','VG'),int(bool(d.get('notify_email'))),int(bool(d.get('notify_sms'))),int(bool(d.get('notify_push'))),int(d.get('priority',100)),int(bool(d.get('active',True))),str(d.get('notes',''))[:200]))
            wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        return jsonify({'ok':True,'id':wid})
    except Exception as e:return error_response(e)

@app.route('/api/wantlists/<int:wid>',methods=['PATCH'])
def wantlist_patch(wid):
    d=request.get_json(silent=True) or {}
    with conn() as c:
        row=c.execute('SELECT * FROM wantlist_entries WHERE id=?',(wid,)).fetchone()
        if not row:return jresp({'error':'not found'},404)
        if 'priority' in d: c.execute('UPDATE wantlist_entries SET priority=? WHERE id=?',(int(d['priority']),wid))
        if 'is_active' in d: c.execute('UPDATE wantlist_entries SET is_active=? WHERE id=?',(int(bool(d['is_active'])),wid))
        return jsonify({'ok':True})

@app.route('/api/reservations',methods=['POST'])
def reserve():
    d=request.get_json(silent=True) or {}; inventory_id=int(d['inventory_id']); customer_id=int(d.get('customer_id',1)); hours=int(d.get('hours',48))
    try:
        with DB.connect() as c:
            active=c.execute('SELECT * FROM reservations WHERE inventory_id=? AND released_at IS NULL AND expires_at>datetime(\'now\')',(inventory_id,)).fetchone()
            if active: raise ReservationConflictException('Item is already reserved',{'inventory_id':inventory_id})
            inv=c.execute('SELECT status FROM inventory WHERE id=?',(inventory_id,)).fetchone()
            if not inv or inv['status'] not in ('in_stock','consignment'): raise OutOfStockException('Item is not available')
            exp=(datetime.now()+timedelta(hours=hours)).isoformat(timespec='seconds')
            c.execute('INSERT INTO reservations(inventory_id,customer_id,source,expires_at) VALUES(?,?,?,?,?)',(inventory_id,customer_id,'wantlist',exp))
            c.execute("UPDATE inventory SET status='reserved' WHERE id=?",(inventory_id,)); rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; audit('inventory',inventory_id,'reserved',{'reservation_id':rid})
        return jsonify({'ok':True,'reservation_id':rid,'expires_at':exp})
    except Exception as e:return error_response(e)

@app.route('/api/reservations/release',methods=['POST'])
def release_reservations():
    with DB.connect() as c:
        ids=c.execute("SELECT id,inventory_id FROM reservations WHERE released_at IS NULL AND expires_at<=datetime('now')").fetchall()
        for x in ids:
            c.execute("UPDATE reservations SET released_at=datetime('now') WHERE id=?",(x['id'],)); c.execute("UPDATE inventory SET status='in_stock' WHERE id=? AND status='reserved'",(x['inventory_id'],))
        return jsonify({'ok':True,'released':len(ids)})

@app.route('/api/pos/checkout',methods=['POST'])
def pos_checkout():
    d=request.get_json(silent=True) or {}; items=d.get('items',[]); tenders=d.get('tenders',[]); customer_id=d.get('customer_id'); role=current_role(); cfg=store_config()
    try:
        if not items: raise ValueError('Cart is empty')
        order_discount=normalize_money(d.get('order_discount_pct',0));
        if order_discount<0 or order_discount>100: raise ValueError('Invalid order discount')
        if order_discount>Decimal(str(cfg['role_discount_cap'])) and not d.get('manager_approved') and role not in ('manager','admin'): raise DiscountAboveRoleCapException('Discount exceeds role cap',{'role':role,'cap':cfg['role_discount_cap']})
        with DB.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            subtotal=Decimal('0'); line_payload=[]; reserved_warnings=[]; inventory_ids=[]
            for it in items:
                iid=int(it['inventory_id']); row=c.execute('''SELECT i.*,r.title,a.name artist,p.id pressing_id FROM inventory i JOIN pressings p ON p.id=i.pressing_id JOIN records r ON r.id=p.record_id JOIN artists a ON a.id=r.artist_id WHERE i.id=?''',(iid,)).fetchone()
                if not row or row['status'] not in ('in_stock','reserved','consignment'): raise OutOfStockException('Item not available',{'inventory_id':iid})
                active_res=c.execute("SELECT * FROM reservations WHERE inventory_id=? AND released_at IS NULL AND expires_at>datetime('now') AND (customer_id IS NULL OR customer_id!=?)",(iid,customer_id or -1)).fetchone()
                if active_res: reserved_warnings.append({'inventory_id':iid,'until':active_res['expires_at']})
                qty=int(it.get('qty',1));
                if qty!=1: raise OutOfStockException('Each physical inventory item can be sold once',{'inventory_id':iid})
                line_discount=normalize_money(it.get('line_discount_pct',0)); price=normalize_money(row['asking_price']); line_total=(price*(Decimal('1')-line_discount/Decimal('100'))).quantize(Decimal('.01')); subtotal+=line_total
                line_payload.append((iid,price,qty,line_discount,line_total,row)); inventory_ids.append(iid)
            discount_total=(subtotal*(order_discount/Decimal('100'))).quantize(Decimal('.01')); subtotal_after=subtotal-discount_total
            tax=Decimal('0') if cfg['vat_mode']=='included' else (subtotal_after*Decimal(str(cfg['vat_rate']))).quantize(Decimal('.01'))
            shipping=normalize_money(d.get('shipping_total',0)); grand=(subtotal_after+tax+shipping).quantize(Decimal('.01'))
            tender_total=sum((normalize_money(x['amount']) for x in tenders),Decimal('0')).quantize(Decimal('.01'))
            if tender_total!=grand: raise TenderMismatchException('Tender total must equal grand total',{'expected':str(grand),'received':str(tender_total)})
            if any(x['type']=='store_credit' for x in tenders) and not customer_id: raise ValueError('Store credit tender requires customer selection')
            order_no=f"ORD-{date.today().year}-{c.execute('SELECT COUNT(*) FROM orders').fetchone()[0]+1:06d}"
            c.execute('INSERT INTO orders(order_number,customer_id,store_id,channel,subtotal,discount_total,tax_total,shipping_total,currency,status,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(order_no,customer_id,1,'pos',float(subtotal),float(discount_total),float(tax),float(shipping),cfg['currency'],'open',str(d.get('notes',''))[:200]))
            oid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            for iid,price,qty,line_discount,line_total,row in line_payload:
                # consignment floor
                if row['consignment_id']:
                    floor=c.execute('SELECT agreed_min_price FROM consignment_items WHERE inventory_id=? AND consignment_id=?',(iid,row['consignment_id'])).fetchone()
                    if floor and (price*(Decimal('1')-line_discount/Decimal('100')))<Decimal(str(floor[0])): raise BelowFloorSaleException('Sale would breach consignment floor',{'inventory_id':iid})
                c.execute('INSERT INTO order_lines(order_id,inventory_id,unit_price,qty,line_discount) VALUES(?,?,?,?,?)',(oid,iid,float(price),qty,float(line_discount)))
                cur=c.execute("UPDATE inventory SET status='sold',sold_at=datetime('now') WHERE id=? AND status IN ('in_stock','reserved','consignment')",(iid,))
                if cur.rowcount != 1: raise OutOfStockException('Inventory changed during checkout',{'inventory_id':iid})
                # consignment payout
                if row['consignment_id']:
                    con=c.execute('SELECT * FROM consignments WHERE id=?',(row['consignment_id'],)).fetchone(); listing=c.execute('SELECT listed_at FROM inventory WHERE id=?',(iid,)).fetchone(); days=0
                    if listing and listing[0]: days=max(0,(date.today()-date.fromisoformat(str(listing[0])[:10])).days)
                    tiers=[PayoutTier(int(t['sold_within_days_from']),t['sold_within_days_to'],Decimal(str(t['payout_pct']))) for t in c.execute('SELECT * FROM payout_tiers WHERE consignment_id=? ORDER BY sold_within_days_from',(row['consignment_id'],)).fetchall()]
                    pct=ConsignmentPayoutCalculator(lambda *args: None,None).applyTiers(days,tiers,Decimal(str(con['default_payout_pct'])))
                    payout=(line_total*pct/Decimal('100')).quantize(Decimal('.01')); c.execute('INSERT INTO consignor_ledger(consignment_id,inventory_id,sale_date,sale_price,payout) VALUES(?,?,?,?,?)',(row['consignment_id'],iid,date.today().isoformat(),float(line_total),float(payout)))
            for t in tenders:
                # No PAN stored; accept tokenized card only.
                c.execute('INSERT INTO tenders(order_id,type,amount,card_token,pan_last4,voucher_code,store_credit_txn,currency) VALUES(?,?,?,?,?,?,?,?)',(oid,t['type'],float(normalize_money(t['amount'])),t.get('card_token'),t.get('pan_last4'),t.get('voucher_code'),t.get('store_credit_txn'),cfg['currency']))
            c.execute("UPDATE orders SET status='paid',closed_at=datetime('now') WHERE id=?",(oid,))
            if customer_id:
                # 1 point per whole USD on net merchandise; consignment excluded
                earn=0
                for iid,price,qty,line_discount,line_total,row in line_payload:
                    if not row['consignment_id']: earn += int(line_total)
                if earn>0:c.execute('INSERT INTO loyalty_ledger(customer_id,delta_points,source,related_order_id,expires_at,created_by,note) VALUES(?,?,?,?,?,?,?)',(customer_id,earn,'earn_sale',oid,(date.today()+timedelta(days=365)).isoformat(),current_user_id(),'POS sale'))
            audit('order',oid,'paid',{'grand_total':float(grand),'warnings':reserved_warnings})
            order=build_order(c,oid)
            c.commit()
        socketio.emit('dashboard_update',{'order_id':oid},namespace='/')
        receipt=ReceiptDispatcher(STORAGE_DIR/'receipts').print(order)
        if d.get('email_receipt') and customer_id:
            with conn() as c: email=c.execute('SELECT email FROM customers WHERE id=?',(customer_id,)).fetchone();
            if email and email['email']:
                receipt['email']=ReceiptDispatcher(STORAGE_DIR/'receipts').email(order,email['email'])
        return jsonify({'ok':True,'order':order,'receipt':receipt,'reserved_warnings':reserved_warnings})
    except Exception as e:
        try:
            with conn() as c: c.execute('ROLLBACK')
        except: pass
        return error_response(e)

def build_order(c,oid):
    o=dict(c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()); lines=[]
    for x in c.execute('''SELECT ol.*,i.id inventory_id,r.title,a.name artist FROM order_lines ol LEFT JOIN inventory i ON i.id=ol.inventory_id LEFT JOIN pressings p ON p.id=i.pressing_id LEFT JOIN records r ON r.id=p.record_id LEFT JOIN artists a ON a.id=r.artist_id WHERE ol.order_id=?''',(oid,)).fetchall(): lines.append(dict(x))
    o['lines']=lines; o['tenders']=[dict(x) for x in c.execute('SELECT type,amount,currency,pan_last4 FROM tenders WHERE order_id=?',(oid,)).fetchall()]; return o

@app.route('/api/orders/<int:oid>/receipt')
def order_receipt(oid):
    with conn() as c:
        order=build_order(c,oid)
    try:
        if request.args.get('format','pdf')=='thermal':
            data=ReceiptDispatcher(STORAGE_DIR/'receipts').render(order,'thermal'); return Response(data,mimetype='application/octet-stream',headers={'Content-Disposition':f"attachment; filename={order['order_number']}.escpos"})
        data,path=ReceiptDispatcher(STORAGE_DIR/'receipts').pdf.render(order); audit('order',oid,'receipt_reprint'); return send_file(io.BytesIO(data),mimetype='application/pdf',download_name=f"{order['order_number']}.pdf")
    except Exception as e:return error_response(e)

@app.route('/api/preorders',methods=['POST'])
def preorders_create():
    d=request.get_json(silent=True) or {}
    try:
        with conn() as c:
            rec=c.execute('SELECT * FROM records WHERE id=? AND pre_order=1',(d.get('record_id'),)).fetchone()
            if not rec: raise ValueError('Selected record is not a pre-order listing')
            customer=c.execute('SELECT * FROM customers WHERE id=?',(d.get('customer_id'),)).fetchone()
            qty=int(d.get('quantity',1)); cap=min(int(rec['per_customer_cap']),5)
            used=c.execute('SELECT COALESCE(SUM(quantity),0) FROM preorders WHERE record_id=? AND customer_id=? AND status NOT IN (\'cancelled\',\'fulfilled\')',(rec['id'],customer['id'])).fetchone()[0]
            if used+qty>cap: raise ValueError('Per-customer quantity cap exceeded')
            deposit=normalize_money(d.get('deposit_amount',0)); policy=rec['deposit_policy']; forced=(customer['cancellation_count_12m'] or 0)>3
            if (policy=='required' or forced) and deposit<=0: raise ValueError('Deposit is required for this pre-order')
            address=str(d.get('ship_address') or ('International delivery' if customer['country_code'] not in ('US',None) else 'Store pickup'))
            ship=0 if customer['country_code'] in ('US',None) else float(store_config()['international_shipping_fee'])
            if ship: address += f" | international shipping +{ship:.2f}"
            c.execute('INSERT INTO preorders(record_id,customer_id,quantity,deposit_amount,deposit_tender,ship_address,notes,release_date) VALUES(?,?,?,?,?,?,?,?)',(rec['id'],customer['id'],qty,float(deposit),d.get('deposit_tender'),address,str(d.get('notes',''))[:200],rec['release_date']))
            pid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO preorder_events(preorder_id,action,payload) VALUES(?,?,?)',(pid,'created',json.dumps({'allocation_rule':rec['allocation_rule']})))
            audit('preorder',pid,'created',{'forced_deposit':forced})
            return jsonify({'ok':True,'preorder_id':pid,'release_date':rec['release_date'],'countdown':rec['release_date'],'shipping_fee':ship})
    except Exception as e:return error_response(e)


@app.route('/api/preorders/fulfill/<int:record_id>',methods=['POST'])
def fulfill_preorders(record_id):
    with DB.connect() as c:
        rec=c.execute('SELECT allocation_rule FROM records WHERE id=?',(record_id,)).fetchone()
        if not rec: return jresp({'error':'record not found'},404)
        orders=c.execute("SELECT * FROM preorders WHERE record_id=? AND status='pending' ORDER BY placed_at",(record_id,)).fetchall()
        if rec['allocation_rule']=='raffle': orders=list(orders); import random; random.shuffle(orders)
        picks=[]; cap=0
        for o in orders:
            picks.append(dict(o)); c.execute("UPDATE preorders SET status='allocated' WHERE id=?",(o['id'],)); c.execute('INSERT INTO preorder_events(preorder_id,action,payload) VALUES(?,?,?)',(o['id'],'allocated',json.dumps({'rule':rec['allocation_rule']}))); cap+=1
        return jsonify({'ok':True,'allocation_rule':rec['allocation_rule'],'pick_list':picks,'count':cap})

@app.route('/api/service-tickets',methods=['GET','POST'])
def service_tickets():
    if request.method=='GET':
        with conn() as c:return jsonify({'ok':True,'items':[dict(x) for x in c.execute('SELECT * FROM service_tickets ORDER BY received_at ASC').fetchall()]})
    d=request.get_json(silent=True) or {}
    try:
        with conn() as c:
            year=date.today().year; n=c.execute('SELECT COUNT(*) FROM service_tickets WHERE ticket_number LIKE ?', (f'STK-{year}-%',)).fetchone()[0]+1; num=f'STK-{year}-{n:04d}'
            checks=d.get('checklist',{'powers_on':False,'platter_spins':False,'arm_balanced':False,'stylus_inspected':False,'cosmetic_damage_noted':False})
            if checks.get('cosmetic_damage_noted') and len(d.get('photos',[]))<1: raise ValueError('Cosmetic damage requires at least one photo')
            c.execute('INSERT INTO service_tickets(ticket_number,customer_id,store_id,equipment_type,brand,model,serial_number,symptoms,intake_checklist,authorised_limit,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(num,d['customer_id'],1,d['equipment_type'],d['brand'],d['model'],d.get('serial_number'),d.get('symptoms',''),json.dumps(checks),float(normalize_money(d.get('authorised_limit',0))),str(d.get('notes',''))[:1000]))
            tid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO ticket_events(ticket_id,actor_id,action,payload) VALUES(?,?,?,?)',(tid,current_user_id(),'received',json.dumps(checks))); audit('service_ticket',tid,'received')
            return jsonify({'ok':True,'ticket_id':tid,'ticket_number':num})
    except Exception as e:return error_response(e)

@app.route('/api/service-tickets/<int:tid>/parts',methods=['POST'])
def add_part(tid):
    d=request.get_json(silent=True) or {}
    try:
        with conn() as c:
            c.execute('INSERT INTO ticket_parts(ticket_id,name,supplier,cost,eta_days) VALUES(?,?,?,?,?)',(tid,d['name'],d.get('supplier'),float(normalize_money(d.get('cost',0))),d.get('eta_days'))); recalc_ticket(c,tid); return jsonify({'ok':True})
    except Exception as e:return error_response(e)
@app.route('/api/service-tickets/<int:tid>/labour',methods=['POST'])
def add_labour(tid):
    d=request.get_json(silent=True) or {}
    try:
        if Decimal(str(d.get('hours',0)))<=0: raise ValueError('hours must be > 0')
        with conn() as c:
            c.execute('INSERT INTO ticket_labour(ticket_id,hours,rate,performed_by,performed_at) VALUES(?,?,?,?,?)',(tid,float(d['hours']),float(normalize_money(d.get('rate',0))),current_user_id(),now_iso())); recalc_ticket(c,tid); return jsonify({'ok':True})
    except Exception as e:return error_response(e)
def recalc_ticket(c,tid):
    try:
        parts=sum((Decimal(str(x[0])) for x in c.execute('SELECT cost FROM ticket_parts WHERE ticket_id=?',(tid,)).fetchall()),Decimal('0')); labour=sum((Decimal(str(x[0]))*Decimal(str(x[1])) for x in c.execute('SELECT hours,rate FROM ticket_labour WHERE ticket_id=?',(tid,)).fetchall()),Decimal('0')); quote=(parts+labour).quantize(Decimal('.01')); t=c.execute('SELECT authorised_limit,status FROM service_tickets WHERE id=?',(tid,)).fetchone(); new_status='awaiting_approval' if quote>Decimal(str(t['authorised_limit'])) and t['status'] not in ('collected','abandoned') else t['status']; c.execute('UPDATE service_tickets SET current_quote=?,status=? WHERE id=?',(float(quote),new_status,tid)); c.execute('INSERT INTO ticket_events(ticket_id,actor_id,action,payload) VALUES(?,?,?,?)',(tid,current_user_id(),'quote_recalculated',json.dumps({'quote':float(quote)}))); return quote
    except Exception as e: raise QuoteRecalculationException('Could not recalculate ticket quote',{'error':str(e)})
@app.route('/api/service-tickets/<int:tid>/status',methods=['POST'])
def ticket_status(tid):
    d=request.get_json(silent=True) or {}; new=d.get('status')
    try:
        with conn() as c:
            t=c.execute('SELECT * FROM service_tickets WHERE id=?',(tid,)).fetchone();
            if not t: raise ValueError('ticket not found')
            transitions=ServiceQueue.TRANSITIONS
            if new not in transitions.get(t['status'],set()): raise StatusTransitionInvalidException('Invalid status transition',{'from':t['status'],'to':new})
            c.execute('UPDATE service_tickets SET status=?,closed_at=? WHERE id=?',(new,now_iso() if new in ('collected','abandoned') else None,tid)); c.execute('INSERT INTO ticket_events(ticket_id,actor_id,action,payload) VALUES(?,?,?,?)',(tid,current_user_id(),'status_changed',json.dumps({'from':t['status'],'to':new,'note':d.get('note','')}))); audit('service_ticket',tid,'status_changed',{'from':t['status'],'to':new})
        return jsonify({'ok':True})
    except Exception as e:return error_response(e)
@app.route('/api/service-tickets/<int:tid>/contact',methods=['POST'])
def ticket_contact(tid):
    with conn() as c:
        c.execute('UPDATE service_tickets SET contact_attempts=MIN(contact_attempts+1,10) WHERE id=?',(tid,)); c.execute('INSERT INTO ticket_events(ticket_id,actor_id,action) VALUES(?,?,?)',(tid,current_user_id(),'contact_attempt')); return jsonify({'ok':True})
@app.route('/api/service-tickets/abandon-sweep',methods=['POST'])
def abandon_sweep():
    cutoff=(date.today()-timedelta(days=90)).isoformat()
    try:
        with conn() as c:
            rows=c.execute("SELECT id FROM service_tickets WHERE received_at<? AND contact_attempts>=2 AND status NOT IN ('collected','abandoned')",(cutoff,)).fetchall()
            for x in rows:
                c.execute("UPDATE service_tickets SET status='abandoned',closed_at=? WHERE id=?",(now_iso(),x[0])); c.execute("INSERT INTO ticket_events(ticket_id,actor_id,action) VALUES(?,?,?)",(x[0],current_user_id(),'abandoned'))
            return jsonify({'ok':True,'count':len(rows)})
    except Exception as e:return error_response(e)

@app.route('/api/consignments',methods=['GET','POST'])
def consignments():
    if request.method=='GET':
        with conn() as c:return jsonify({'ok':True,'items':[dict(x) for x in c.execute('SELECT co.*,c.name consignor FROM consignments co JOIN consignors c ON c.id=co.consignor_id ORDER BY co.effective_date DESC').fetchall()]})
    d=request.get_json(silent=True) or {}
    try:
        tiers=d.get('tiers',[]); last=-1
        for t in sorted(tiers,key=lambda z:int(z['from_days'])):
            if int(t['from_days'])<=last: raise TierOverlapException('Payout tiers overlap')
            last=int(t.get('to_days') or 10000000); p=Decimal(str(t['pct']));
            if p<0 or p>100: raise ValueError('payout pct out of range')
        if not d.get('signature'): raise ValueError('Signature required to finalise')
        with conn() as c:
            year=date.today().year; n=c.execute('SELECT COUNT(*) FROM consignments WHERE agreement_number LIKE ?', (f'CSG-{year}-%',)).fetchone()[0]+1; agr=f'CSG-{year}-{n:04d}'
            c.execute('INSERT INTO consignments(agreement_number,consignor_id,store_id,effective_date,default_payout_pct,sale_floor,statement_frequency,auto_return_days,signature_blob,finalised_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(agr,d['consignor_id'],1,d.get('effective_date',date.today().isoformat()),float(d['default_payout_pct']),int(bool(d.get('sale_floor',True))),d.get('statement_frequency','monthly'),int(d.get('auto_return_days',180)),d['signature'].encode(),now_iso(),str(d.get('notes',''))[:500]))
            cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            for t in tiers:c.execute('INSERT INTO payout_tiers(consignment_id,sold_within_days_from,sold_within_days_to,payout_pct) VALUES(?,?,?,?)',(cid,int(t['from_days']),t.get('to_days'),float(t['pct'])))
            return jsonify({'ok':True,'consignment_id':cid,'agreement_number':agr})
    except Exception as e:return error_response(e)
@app.route('/api/consignments/<int:cid>/statement')
def consignment_statement(cid):
    start=request.args.get('start',(date.today().replace(day=1)).isoformat()); end=request.args.get('end',date.today().isoformat()); out=STORAGE_DIR/'statements'; out.mkdir(exist_ok=True)
    try:
        with conn() as c:
            if c.execute('SELECT 1 FROM statement_periods WHERE consignment_id=? AND period_start=? AND period_end=?',(cid,start,end)).fetchone(): raise StatementPeriodOverlapException('Statement period already generated')
            rows=c.execute('''SELECT cl.*,r.title,a.name artist FROM consignor_ledger cl JOIN inventory i ON i.id=cl.inventory_id JOIN pressings p ON p.id=i.pressing_id JOIN records r ON r.id=p.record_id JOIN artists a ON a.id=r.artist_id WHERE cl.consignment_id=? AND cl.sale_date BETWEEN ? AND ? ORDER BY cl.sale_date''',(cid,start,end)).fetchall()
            total=sum(Decimal(str(x['payout'])) for x in rows); c.execute('INSERT INTO statement_periods(consignment_id,period_start,period_end) VALUES(?,?,?)',(cid,start,end)); sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        path=out/f'CSG-{cid}-{start}-{end}.csv'; tmp=path.with_suffix('.tmp');
        with tmp.open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.writer(f); w.writerow(['Date','Artist','Title','Sale Price','Payout']); [w.writerow([x['sale_date'],x['artist'],x['title'],x['sale_price'],x['payout']]) for x in rows]; w.writerow([]); w.writerow(['TOTAL','','','',total]);
        tmp.replace(path); return jsonify({'ok':True,'statement_id':sid,'total':float(total),'csv':str(path.relative_to(BASE_DIR))})
    except Exception as e:return error_response(e)


@app.route('/api/saved-searches',methods=['GET','POST'])
def saved_searches():
    cid=int(request.args.get('customer_id') or (request.get_json(silent=True) or {}).get('customer_id') or 1)
    if request.method=='GET':
        with conn() as c: return jsonify({'ok':True,'items':[dict(x) for x in c.execute('SELECT * FROM saved_searches WHERE customer_id=? ORDER BY created_at DESC',(cid,)).fetchall()]})
    d=request.get_json(silent=True) or {}; name=str(d.get('name','Saved search')).strip(); query=str(d.get('query','')).strip()
    if not query: return jresp({'error':'query required'},400)
    with conn() as c:
        c.execute('INSERT INTO saved_searches(customer_id,name,query) VALUES(?,?,?)',(cid,name,query)); sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return jsonify({'ok':True,'id':sid})

@app.route('/api/loyalty/<int:customer_id>')
def loyalty(customer_id):
    with conn() as c:
        rows=c.execute('SELECT * FROM loyalty_ledger WHERE customer_id=? ORDER BY created_at DESC',(customer_id,)).fetchall(); balance=sum(int(x['delta_points']) for x in rows); customer=c.execute('SELECT display_name,tier FROM customers WHERE id=?',(customer_id,)).fetchone();
        exp=c.execute("SELECT COALESCE(SUM(delta_points),0) FROM loyalty_ledger WHERE customer_id=? AND delta_points>0 AND expires_at BETWEEN date('now') AND date('now','+90 day')",(customer_id,)).fetchone()[0]
        return jsonify({'ok':True,'customer':dict(customer) if customer else None,'balance':balance,'monetary_equivalent':round(balance*0.01,2),'expiring_90_days':exp,'transactions':[dict(x) for x in rows]})
@app.route('/api/loyalty/<int:customer_id>/redeem',methods=['POST'])
def loyalty_redeem(customer_id):
    d=request.get_json(silent=True) or {}; points=int(d.get('points',0)); reward=d.get('reward','discount');
    try:
        with conn() as c:
            bal=sum(int(x[0]) for x in c.execute('SELECT delta_points FROM loyalty_ledger WHERE customer_id=?',(customer_id,)).fetchall())
            points=((points+9)//10)*10
            if points<=0 or points>bal: raise ValueError('Insufficient loyalty balance')
            c.execute('INSERT INTO redemptions(customer_id,points,reward) VALUES(?,?,?)',(customer_id,points,reward)); rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO loyalty_ledger(customer_id,delta_points,source,related_redemption_id,created_by,note) VALUES(?,?,?,?,?,?)',(customer_id,-points,'redeem',rid,current_user_id(),reward)); audit('loyalty',rid,'redeem',{'points':points})
            return jsonify({'ok':True,'redemption_id':rid,'balance':bal-points})
    except Exception as e:return error_response(e)

@app.route('/api/loyalty/<int:customer_id>/export')
def loyalty_export(customer_id):
    with conn() as c: rows=c.execute('SELECT created_at,source,delta_points,note FROM loyalty_ledger WHERE customer_id=? ORDER BY created_at DESC',(customer_id,)).fetchall()
    s=io.StringIO(); w=csv.writer(s); w.writerow(['timestamp','source','points','note']); [w.writerow([x['created_at'],x['source'],x['delta_points'],x['note']]) for x in rows]; return Response(s.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=loyalty.csv'})

@app.route('/api/upload',methods=['POST'])
def upload():
    files=request.files.getlist('files')
    if len(files)>6:return jresp({'error':'Maximum 6 files'},400)
    saved=[]
    for f in files:
        if not f.filename:continue
        if f.mimetype not in {'image/jpeg','image/png'}:return jresp({'error':'Only JPG/PNG allowed'},400)
        f.seek(0,2); size=f.tell(); f.seek(0)
        if size>8*1024*1024:return jresp({'error':'Each image max 8 MB'},400)
        name=f'{uuid.uuid4().hex}_{secure_filename(f.filename)}'; path=UPLOAD_DIR/name; f.save(path); saved.append('/uploads/'+name)
    return jsonify({'ok':True,'paths':saved})
@app.route('/uploads/<path:name>')
def uploads(name): return send_file(UPLOAD_DIR/name)

@app.route('/api/import',methods=['POST'])
def import_csv():
    f=request.files.get('file'); dry=str(request.form.get('dry_run','false')).lower()=='true'
    if not f:return jresp({'error':'file required'},400)
    tmp=STORAGE_DIR/f'import_{uuid.uuid4().hex}.csv'; f.save(tmp)
    try:return jsonify({'ok':True,**CatalogueImporter().importFile(tmp,dryRun=dry)})
    except Exception as e:return error_response(e)

@app.route('/api/audit')
def audit_api():
    with conn() as c:return jsonify({'ok':True,'items':[dict(x) for x in c.execute('SELECT a.*,u.name actor FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.created_at DESC LIMIT 200').fetchall()]})
@app.route('/api/blacklist',methods=['GET','POST'])
def blacklist():
    if request.method=='GET':
        with conn() as c:return jsonify({'ok':True,'items':[dict(x) for x in c.execute('SELECT * FROM counterfeit_blacklist ORDER BY added_at DESC').fetchall()]})
    d=request.get_json(silent=True) or {}
    try:
        with conn() as c:
            c.execute('INSERT INTO counterfeit_blacklist(matrix_runout_a,matrix_runout_b,artist_name,title,reason,source_authority,added_by) VALUES(?,?,?,?,?,?,?)',(d['matrix_a'],d.get('matrix_b'),d.get('artist_name'),d.get('title'),d['reason'],d['source_authority'],current_user_id())); return jsonify({'ok':True})
    except Exception as e:return error_response(e)

@app.route('/api/export/catalogue')
def export_catalogue():
    with conn() as c: rows=record_search_rows(c,request.args.get('q',''),10000)
    s=io.StringIO(); w=csv.writer(s); w.writerow(['artist','title','label','catalogue_number','country_code','year','format','rpm','media_grade','asking_price','stock']); [w.writerow([x['artist'],x['title'],x['label'],x['catalogue_number'],x['country_code'],x['year'],x['format'],x['rpm'],x['media_grade'],x['price'],x['stock']]) for x in rows]; return Response('\ufeff'+s.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=catalogue.csv'})

@app.route('/api/health')
def health():
    with conn() as c: c.execute('SELECT 1')
    return jsonify({'ok':True,'db':'sqlite','timestamp':now_iso()})

@app.errorhandler(Exception)
def unhandled(e): return error_response(e)

if __name__=='__main__':
    socketio.run(app,host='0.0.0.0',port=int(os.environ.get('PORT',5000)),debug=True)

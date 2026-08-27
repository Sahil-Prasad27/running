import csv, io, json, logging
from pathlib import Path
from .exceptions import ImportSchemaMismatchException, DuplicateCatalogueException, CounterfeitPressingException, DatabaseTransactionException
from .db import DB

EXPECTED = {'artist','title','label','catalogue_number','country_code','year','format','rpm','media_grade','sleeve_grade','asking_price','bin_code'}
class CatalogueImporter:
    def __init__(self, recordRepo=None, pressingRepo=None, blacklist=None, schema=EXPECTED):
        self.schema=set(schema)
    def _dialect(self,sample):
        try: return csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error: return csv.excel
    def validateRow(self,row):
        errors=[]
        required=self.schema-{'bin_code'}
        for k in required:
            if not str(row.get(k,'')).strip(): errors.append(f'{k}: required')
        try:
            year=int(row.get('year','0'))
            if not 1948 <= year <= __import__('datetime').date.today().year: errors.append('year: out of range')
        except: errors.append('year: invalid')
        try:
            if int(row.get('rpm','0')) not in {33,45,78}: errors.append('rpm: invalid')
        except: errors.append('rpm: invalid')
        if row.get('format') not in {'7in','10in','12in_lp','12in_maxi','box_set','picture_disc','coloured'}: errors.append('format: invalid')
        if row.get('country_code','').strip().upper().__len__()!=2: errors.append('country_code: must be ISO alpha-2')
        if row.get('media_grade') not in {'M','NM','VG+','VG','G+','G','F','P'}: errors.append('media_grade: invalid')
        if row.get('sleeve_grade') not in {'M','NM','VG+','VG','G+','G','F','P'}: errors.append('sleeve_grade: invalid')
        try:
            if float(str(row.get('asking_price','0')).replace(',','.'))<0: errors.append('asking_price: negative')
        except: errors.append('asking_price: invalid')
        return errors
    def resolveArtist(self,name,createMissing=False):
        with DB.connect() as c:
            x=c.execute('SELECT * FROM artists WHERE lower(name)=lower(?)',(name.strip(),)).fetchone()
            if x:return x
            if not createMissing: raise Exception(f'Unknown artist: {name}')
            c.execute('INSERT INTO artists(name) VALUES(?)',(name.strip(),)); return c.execute('SELECT * FROM artists WHERE id=last_insert_rowid()').fetchone()
    def resolveLabel(self,name,createMissing=False):
        with DB.connect() as c:
            x=c.execute('SELECT * FROM labels WHERE lower(name)=lower(?)',(name.strip(),)).fetchone()
            if x:return x
            if not createMissing: raise Exception(f'Unknown label: {name}')
            c.execute('INSERT INTO labels(name) VALUES(?)',(name.strip(),)); return c.execute('SELECT * FROM labels WHERE id=last_insert_rowid()').fetchone()
    def importFile(self,path,dryRun=False,create_missing=True):
        path=Path(path); raw=path.read_bytes()
        text=raw.decode('utf-8-sig')
        dialect=self._dialect(text[:4096])
        reader=csv.DictReader(io.StringIO(text), dialect=dialect)
        headers=set(reader.fieldnames or [])
        missing=EXPECTED-headers
        if missing: raise ImportSchemaMismatchException('Missing required CSV columns', {'missing':sorted(missing)})
        report={'total':0,'imported':0,'errors':0,'skipped':0,'error_breakdown':{},'samples':[]}
        log_path=path.with_name(path.stem+'_errors.log')
        log_lines=[]
        rows=[]
        for line_no,row in enumerate(reader,start=2):
            report['total']+=1; errors=self.validateRow(row)
            if errors:
                report['errors']+=1; report['skipped']+=1
                for e in errors: report['error_breakdown'][e.split(':')[0]]=report['error_breakdown'].get(e.split(':')[0],0)+1
                if len(report['samples'])<10: report['samples'].append({'row':line_no,'errors':errors})
                log_lines.append(json.dumps({'row':line_no,'errors':errors,'data':row},ensure_ascii=False)); continue
            rows.append((line_no,row))
        if dryRun:
            log_path.write_text('\n'.join(log_lines),encoding='utf-8')
            return report
        for chunk_start in range(0,len(rows),5000):
            chunk=rows[chunk_start:chunk_start+5000]
            conn=DB.connect()
            try:
                conn.execute('BEGIN')
                for line_no,row in chunk:
                    try:
                        aid=self.resolveArtist(row['artist'],create_missing)['id']; lid=self.resolveLabel(row['label'],create_missing)['id']
                        dup=conn.execute('SELECT id FROM records WHERE label_id=? AND catalogue_number=? AND country_code=? AND deleted_at IS NULL',(lid,row['catalogue_number'].strip(),row['country_code'].upper())).fetchone()
                        if dup: raise DuplicateCatalogueException('Duplicate label/catalogue/country',{'row':line_no})
                        conn.execute('INSERT INTO records(artist_id,title,label_id,catalogue_number,country_code,year,format,rpm) VALUES(?,?,?,?,?,?,?,?)',(aid,row['title'].strip(),lid,row['catalogue_number'].strip(),row['country_code'].upper(),int(row['year']),row['format'],int(row['rpm'])))
                        rid=conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                        conn.execute('INSERT INTO pressings(record_id,matrix_runout_a,matrix_runout_b,press_year) VALUES(?,?,?,?)',(rid,row.get('matrix_runout_a'),row.get('matrix_runout_b'),row.get('year')))
                        pid=conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                        price=float(str(row['asking_price']).replace(',','.'))
                        conn.execute('INSERT INTO inventory(pressing_id,store_id,bin_code,media_grade,sleeve_grade,has_original_inner_sleeve,asking_price,currency,listed_at) VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)',(pid,1,row.get('bin_code','').strip(),row['media_grade'],row['sleeve_grade'],1,price,'USD'))
                        report['imported']+=1
                    except DuplicateCatalogueException as e:
                        report['errors']+=1; report['skipped']+=1; report['error_breakdown']['DuplicateCatalogueException']=report['error_breakdown'].get('DuplicateCatalogueException',0)+1
                        if len(report['samples'])<10: report['samples'].append({'row':line_no,'errors':[str(e)]})
                conn.commit()
            except Exception:
                conn.rollback(); raise DatabaseTransactionException('CSV chunk rolled back',{'chunk_start':chunk_start})
            finally: conn.close()
        log_path.write_text('\n'.join(log_lines),encoding='utf-8')
        return report

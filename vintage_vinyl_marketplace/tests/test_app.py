import os, tempfile
from pathlib import Path
import pytest
from app import app
from services.db import Database, SCHEMA

@pytest.fixture
def client(tmp_path, monkeypatch):
    # isolate database for deterministic API tests
    from services import db as dbmod
    testdb=Database(tmp_path/'test.sqlite3')
    testdb.init()
    monkeypatch.setattr(dbmod,'DB',testdb)
    import app as appmod
    monkeypatch.setattr(appmod,'DB',testdb)
    appmod.app.config['TESTING']=True
    return appmod.app.test_client()

def test_health(client):
    r=client.get('/api/health'); assert r.status_code==200; assert r.get_json()['ok']

def test_records_search(client):
    r=client.get('/api/records?q=Velvet'); assert r.status_code==200; assert r.get_json()['count']>=1

def test_record_duplicate_block(client):
    payload={'artist_name':'The Velvet Echoes','label_name':'Moonlight Records','create_artist':False,'create_label':False,'title':'Duplicate','catalogue_number':'ML-101','country_code':'US','year':1980,'format':'12in_lp','rpm':33,'media_grade':'VG','sleeve_grade':'VG','asking_price':'10','bin_code':'X-1'}
    r=client.post('/api/records',json=payload); assert r.status_code==400; assert r.get_json()['errorCode']=='DUPLICATECATALOGUEEXCEPTION'

def test_grading_downgrade(client):
    r=client.post('/api/grading/validate',json={'media':'NM','sleeve':'NM','has_original_inner_sleeve':False,'defects':[]})
    assert r.status_code==200; assert r.get_json()['grading']['media']=='VG+'

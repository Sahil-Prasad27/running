from decimal import Decimal
import pickle
import pytest
from services.domain import *
from services.exceptions import *

def test_polymorphism_and_roundtrip():
    lp=LP(1,'A','T','L','C','US',1970,33,'12in_lp',gatefold=True)
    assert lp.defaultSleeveType()=='gatefold'
    one=lp.toDict(); one2=Record.fromDict(one); assert one2.describe()==lp.describe()

def test_box_set_requires_two():
    with pytest.raises(ValueError): BoxSet(1,'A','T','L','C','US',1970,33,'box_set',includedRecords=[])

def test_grading_downgrade_and_order():
    svc=GradingService(GoldmineRules(True)); g=svc.validate(Grading('NM','NM',False,[])); assert g.media=='VG+'
    assert GRADE_RANK['M']<GRADE_RANK['P']
    assert svc.compare(Grading('M','NM',True),Grading('VG+','NM',True))==-1

def test_grading_mismatch():
    with pytest.raises(GradingMismatchException): GradingService(GoldmineRules()).validate(Grading('NM','NM',True,['deep groove scratch']))

def test_exception_roundtrip():
    e=DuplicateCatalogueException('x',{'id':2}); e2=exception_from_dict(e.to_dict()); assert type(e2) is type(e); assert e2.context==e.context
    assert pickle.loads(pickle.dumps(e2)).context==e.context

def test_valuation_store_credit():
    repo=lambda pid:[100,110,90,105,95]
    v=TradeInValuator(repo,{1:1.0},set(),{})
    vals=v.estimateWholesale([ValuationInput(1,'VG+','VG+',True,['x'])])
    assert vals[0].confidence=='high'
    assert v.computeOffer(vals,'store_credit')==vals[0].mid*Decimal('1.20')

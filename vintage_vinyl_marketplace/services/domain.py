from __future__ import annotations
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from abc import ABC, abstractmethod
from typing import Optional, List, Dict
from pathlib import Path
from datetime import date
import json
from .exceptions import *

GRADES = ['M','NM','VG+','VG','G+','G','F','P']
GRADE_RANK = {g: i for i,g in enumerate(GRADES)}
FORMAT_VALUES = ['7in','10in','12in_lp','12in_maxi','box_set','picture_disc','coloured']
RPM_VALUES = {33,45,78}
FORMAT_LABELS = {
    '7in':'7" Single','10in':'10"','12in_lp':'12" LP','12in_maxi':'12" Maxi',
    'box_set':'Box Set','picture_disc':'Picture Disc','coloured':'Coloured Vinyl'
}
GRADE_DEFS = {
    'M':'Sealed, pristine; rarely assigned to opened items.',
    'NM':'Looks unopened; minimal handling marks.',
    'VG+':'Light surface marks, plays cleanly with occasional whispers.',
    'VG':'Visible marks, audible surface noise, music dominates.',
    'G+':'Significant wear, plays through.',
    'G':'Heavy wear, surface noise prominent.',
    'F':'Damaged, collector value only.',
    'P':'Barely playable; placeholder only.'
}

class Record(ABC):
    def __init__(self, recordId, artist, title, label, catalogueNumber, country, year, rpm, format, monoStereo='unknown'):
        if not 1948 <= int(year) <= date.today().year:
            raise ValueError('year must be between 1948 and current year')
        if int(rpm) not in RPM_VALUES:
            raise ValueError('rpm must be 33, 45 or 78')
        if format not in FORMAT_VALUES:
            raise ValueError('invalid format')
        self.recordId = int(recordId)
        self.artist = artist; self.title = title; self.label = label; self.catalogueNumber = catalogueNumber
        self.country = country.upper(); self.year = int(year); self.rpm = int(rpm); self.format = format; self.monoStereo = monoStereo
        self.pressings: List[dict] = []; self.inventory: List[dict] = []
    @abstractmethod
    def defaultSleeveType(self): ...
    @abstractmethod
    def suggestedJacketWeightG(self): ...
    def addPressing(self, p): self.pressings.append(p)
    def addInventory(self, i): self.inventory.append(i)
    def describe(self):
        return f"{self.artist} - {self.title} ({self.year}, {FORMAT_LABELS[self.format]}, {self.country}, {self.catalogueNumber})"
    def toDict(self):
        d = dict(asdict(self)) if hasattr(self,'__dataclass_fields__') else self.__dict__.copy()
        d['type'] = self.__class__.__name__; return d
    @classmethod
    def fromDict(cls, d):
        kind = d.get('type','Record')
        klass = {'LP':LP,'Single':Single,'BoxSet':BoxSet,'PictureDisc':PictureDisc,'ColouredVinyl':ColouredVinyl}.get(kind, LP)
        kwargs = dict(recordId=d['recordId'],artist=d['artist'],title=d['title'],label=d['label'],catalogueNumber=d['catalogueNumber'],country=d['country'],year=d['year'],rpm=d['rpm'],format=d['format'],monoStereo=d.get('monoStereo','unknown'))
        if klass is LP: obj = klass(**kwargs, gatefold=d.get('gatefold',False), sideCount=d.get('sideCount',2))
        elif klass is Single: obj = klass(**kwargs, aSideTitle=d.get('aSideTitle',''), bSideTitle=d.get('bSideTitle',''))
        elif klass is BoxSet: obj = klass(**kwargs, includedRecords=d.get('includedRecords',[]), booklets=d.get('booklets',[]))
        elif klass is PictureDisc: obj = klass(**kwargs, imageRef=d.get('imageRef',''))
        else: obj = klass(**kwargs, colourDescription=d.get('colourDescription',''), isLimited=d.get('isLimited',False), limitedCount=d.get('limitedCount'))
        obj.pressings = d.get('pressings',[]); obj.inventory = d.get('inventory',[]); return obj

class LP(Record):
    def __init__(self, *args, gatefold=False, sideCount=2, **kwargs): self.gatefold=gatefold; self.sideCount=sideCount; super().__init__(*args, **kwargs)
    def defaultSleeveType(self): return 'single sleeve' if not self.gatefold else 'gatefold'
    def suggestedJacketWeightG(self): return 140 if not self.gatefold else 200
class Single(Record):
    def __init__(self, *args, aSideTitle='', bSideTitle='', **kwargs): self.aSideTitle=aSideTitle; self.bSideTitle=bSideTitle; super().__init__(*args, **kwargs)
    def defaultSleeveType(self): return 'paper sleeve'
    def suggestedJacketWeightG(self): return 80
class BoxSet(Record):
    def __init__(self, *args, includedRecords=None, booklets=None, **kwargs):
        self.includedRecords = includedRecords or []; self.booklets = booklets or []
        if len(self.includedRecords) < 2: raise ValueError('BoxSet requires at least 2 included records')
        super().__init__(*args, **kwargs)
    def defaultSleeveType(self): return 'box'
    def suggestedJacketWeightG(self): return 300
class PictureDisc(Record):
    def __init__(self, *args, imageRef='', **kwargs): self.imageRef=imageRef; super().__init__(*args, **kwargs)
    def defaultSleeveType(self): return 'card sleeve'
    def suggestedJacketWeightG(self): return 120
class ColouredVinyl(Record):
    def __init__(self, *args, colourDescription='', isLimited=False, limitedCount=None, **kwargs):
        self.colourDescription=colourDescription; self.isLimited=isLimited; self.limitedCount=limitedCount; super().__init__(*args, **kwargs)
    def defaultSleeveType(self): return 'single sleeve'
    def suggestedJacketWeightG(self): return 140

@dataclass
class Grading:
    media: str
    sleeve: str
    hasOriginalInnerSleeve: bool
    defects: List[str] = field(default_factory=list)
    explanation: str = ''

class GoldmineRules:
    def __init__(self, allow_downgrade=True): self.allow_downgrade=allow_downgrade

class GradingService:
    CONTROLLED_DEFECTS = {'hairlines','deep groove scratch','warp','split seam','ring wear','water damage','missing insert','writing'}
    def __init__(self, rules: GoldmineRules): self.rules=rules
    def validate(self, g: Grading) -> Grading:
        if g.media not in GRADES or g.sleeve not in GRADES: raise InvalidGradeException('Unknown grade code', {'media':g.media,'sleeve':g.sleeve})
        bad = set(g.defects) - self.CONTROLLED_DEFECTS
        if bad: raise GradingMismatchException('Unknown defect(s)', {'defects':sorted(bad)})
        if g.media in ('M','NM') and 'deep groove scratch' in g.defects:
            raise GradingMismatchException('Deep groove scratch contradicts M/NM', {'grade':g.media})
        return self.downgradeIfNoOriginalSleeve(g)
    def downgradeIfNoOriginalSleeve(self, g: Grading) -> Grading:
        if g.media == 'NM' and not g.hasOriginalInnerSleeve:
            if not self.rules.allow_downgrade: raise DowngradeNotAuthorisedException('NM without original inner sleeve must be downgraded', {})
            g.media='VG+'; g.explanation='Downgraded from NM to VG+ because the original inner sleeve is missing.'
        return g
    def explain(self, g: Grading) -> str:
        text = GRADE_DEFS.get(g.media,'') + ' ' + (g.explanation or '')
        if g.defects: text += ' Defects: ' + ', '.join(g.defects) + '.'
        return text.strip()
    def compare(self, a: Grading, b: Grading) -> int:
        am, bm = GRADE_RANK[a.media], GRADE_RANK[b.media]
        if am != bm: return -1 if am < bm else 1
        as_, bs_ = GRADE_RANK[a.sleeve], GRADE_RANK[b.sleeve]
        return 0 if as_==bs_ else (-1 if as_<bs_ else 1)

@dataclass
class ValuationInput:
    pressingId: int
    mediaGrade: str
    sleeveGrade: str
    hasOriginalInner: bool
    condition_photos: List[str]
    matrix_a: str=''; matrix_b: str=''
@dataclass
class Valuation:
    pressingId: int
    low: Decimal
    mid: Decimal
    high: Decimal
    confidence: str='high'

class TradeInValuator:
    BASE = {'M':80,'NM':70,'VG+':55,'VG':40,'G+':28,'G':18,'F':8,'P':2}
    def __init__(self, sold_history_repo, rarityIndex=None, blacklist=None, settings=None):
        self.sold_history_repo=sold_history_repo; self.rarityIndex=rarityIndex or {}; self.blacklist=blacklist or set(); self.settings=settings or {'max_override_delta':20}
    def applyRarityMatrix(self, grade, rarity):
        if grade not in self.BASE: raise InvalidGradeException('Invalid grade')
        if not .1 <= float(rarity) <= 5.0: raise ValueError('rarity multiplier must be in [0.1,5.0]')
        return Decimal(str(rarity))
    def estimateWholesale(self, inputs):
        results=[]
        for i in inputs:
            if not i.condition_photos: raise ValueError('At least one condition photo required')
            key=(i.matrix_a.strip().upper(), i.matrix_b.strip().upper())
            if key in self.blacklist: raise CounterfeitPressingException('Counterfeit pressing - do not accept', {'pressingId':i.pressingId})
            comps=self.sold_history_repo(i.pressingId) or []
            rarity=self.rarityIndex.get(i.pressingId,1.0)
            base=Decimal(str(self.BASE[i.mediaGrade])) * self.applyRarityMatrix(i.mediaGrade,rarity)
            if len(comps)<5:
                conf='low confidence'
            else:
                nums=[Decimal(str(x)) for x in comps[-30:]]
                base=sum(nums)/Decimal(len(nums)) * Decimal('0.65')
                conf='high'
            results.append(Valuation(i.pressingId,(base*Decimal('.8')).quantize(Decimal('.01')),(base).quantize(Decimal('.01')),(base*Decimal('1.2')).quantize(Decimal('.01')),conf))
        return results
    def computeOffer(self, valuations, mode):
        total=sum((v.mid for v in valuations), Decimal('0'))
        return (total*Decimal('1.20')).quantize(Decimal('.01')) if mode=='store_credit' else total.quantize(Decimal('.01'))
    def flagSuspicious(self, inputs):
        out=[]
        for i in inputs:
            rarity=self.rarityIndex.get(i.pressingId,1.0)
            if rarity>=3 and len(i.condition_photos)<=1 and GRADE_RANK.get(i.mediaGrade,7)>=GRADE_RANK['G']:
                out.append(i)
        return out

@dataclass
class PayoutTier:
    fromDays:int; toDays:Optional[int]; payoutPct:Decimal
@dataclass
class Consignment:
    id:int; defaultPayoutPct:Decimal; tiers:List[PayoutTier]; saleFloor:bool=True
class ConsignmentPayoutCalculator:
    def __init__(self, consignment_repo, ledger_repo): self.consignment_repo=consignment_repo; self.ledger_repo=ledger_repo
    def applyTiers(self, daysOnSale, tiers, defaultPct):
        for t in sorted(tiers,key=lambda x:x.fromDays):
            if daysOnSale>=t.fromDays and (t.toDays is None or daysOnSale<=t.toDays): return t.payoutPct
        return defaultPct
    def payoutForSale(self, consignmentId, inventoryId, salePrice, saleDate, listingDate):
        c=self.consignment_repo(consignmentId, inventoryId)
        if c['sale_floor'] and Decimal(str(salePrice)) < Decimal(str(c['agreed_min_price'])): raise BelowFloorSaleException('Sale price below consignment floor', {'inventoryId':inventoryId})
        days=(saleDate-listingDate).days
        pct=self.applyTiers(days,c['tiers'],Decimal(str(c['default_payout_pct'])))
        payout=(Decimal(str(salePrice))*pct/Decimal('100')).quantize(Decimal('.01'))
        if self.ledger_repo: self.ledger_repo(consignmentId,inventoryId,payout,saleDate)
        return payout

class ServiceQueue:
    TRANSITIONS = {
        'received': {'diagnosing','abandoned'}, 'diagnosing': {'awaiting_parts','repair','awaiting_approval','abandoned'},
        'awaiting_parts': {'repair','awaiting_approval','abandoned'}, 'repair': {'test','awaiting_approval','abandoned'},
        'test': {'ready','repair','awaiting_approval','abandoned'}, 'ready': {'collected','abandoned'},
        'collected': set(), 'awaiting_approval': {'repair','abandoned'}, 'abandoned': set()
    }
    def __init__(self, ticketRepo, notifier): self.ticketRepo=ticketRepo; self.notifier=notifier
    def nextTicket(self, byTechnician=None): return self.ticketRepo.next_open(byTechnician)
    def updateStatus(self, ticketId, newStatus, by, note):
        t=self.ticketRepo.get(ticketId)
        if not t: raise StatusTransitionInvalidException('Ticket not found')
        if newStatus not in self.TRANSITIONS.get(t['status'],set()): raise StatusTransitionInvalidException('Invalid status transition', {'from':t['status'],'to':newStatus})
        return self.ticketRepo.status(ticketId,newStatus,by,note)
    def addPart(self,ticketId,part,supplier,cost,etaDays):
        if Decimal(str(cost))<0: raise QuoteRecalculationException('Cost cannot be negative')
        return self.ticketRepo.add_part(ticketId,part,supplier,cost,etaDays)
    def addLabour(self,ticketId,hours,rate,by):
        if Decimal(str(hours))<=0: raise QuoteRecalculationException('Hours must be > 0')
        return self.ticketRepo.add_labour(ticketId,hours,rate,by)
    def markAbandoned(self,asOfDate): return self.ticketRepo.mark_abandoned(asOfDate)

class WantlistMatcher:
    def __init__(self, wantlistRepo, notifierRegistry): self.repo=wantlistRepo; self.notifiers=notifierRegistry
    def subscribe(self,channel,notifier): self.notifiers[channel]=notifier
    def matchAgainstEntries(self,event): return self.repo.match(event)
    def onInventoryListed(self,event):
        matches=self.matchAgainstEntries(event)
        for m in matches:
            for ch in m.get('channels',[]):
                try: self.notifiers.get(ch, lambda *_: None)(m)
                except Exception as e: pass
        return matches
    def reserveForCustomer(self,customerId,inventoryId,hoursValid=48): return self.repo.reserve(customerId,inventoryId,hoursValid)
    def releaseExpiredReservations(self): return self.repo.release_expired()

from dataclasses import dataclass
@dataclass
class CartLine: inventoryId:int; unitPrice:Decimal; qty:int=1; lineDiscount:Decimal=Decimal('0')
@dataclass
class Tender: type:str; amount:Decimal; cardToken:Optional[str]=None; voucherCode:Optional[str]=None; storeCreditTxn:Optional[str]=None

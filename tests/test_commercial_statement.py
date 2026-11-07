from datetime import datetime, timezone
import pytest
from cow.domain.commercial_machine import CommercialError
from cow.domain.commercial_statement import render
from cow.domain.commercial_machine import (
    evaluate, Assignment, Context, GSTAssignment, GSTProfile, Measurement,
)


def fixtures():
    ctx=Context('ZONE','ROAMING','INSIDE',datetime(2026,11,7,tzinfo=timezone.utc),'SELLER',verified=True)
    tariff={'key':'STANDARD','version':1,'channel':'ROAMING','currency':'INR','price_mode':'EXCLUSIVE',
            'profiles':{'INSIDE':{'charging':{'basis':'ENERGY_KWH','rate':'20','tax_class':'CHARGE'}}}}
    gst=GSTProfile('GST',1,'SELLER','DECISION','APPROVED',{
        'CHARGE':{'treatment':'TAXABLE','supply_group':'SERVICE','rates':{'CGST':'9','SGST':'9','UTGST':'0','IGST':'0'}}})
    return dict(context=ctx,event='COMPLETED',assignments=[Assignment('A','STANDARD',1,'ZONE','ZONE','ROAMING','DEFAULT')],
        tariffs={('STANDARD',1):tariff},gst_assignments=[GSTAssignment('G','ZONE','ROAMING','GST',1)],
        gst_profiles={('GST',1):gst},measurements={'energy_wh':Measurement(2000,'VERIFIED_HAL')},mode='FINAL')


def test_preview_is_not_an_invoice():
    p=fixtures();p['mode']='PREVIEW'
    d=render(evaluate(**p))
    assert d['status']=='ESTIMATE' and d['statutory_gst_invoice_issued'] is False
    assert 'Not a GST Tax Invoice' in d['disclaimer']
    assert d['totals']['gross_inr']=='47.20'


def test_final_without_settlement_is_not_claimed_as_paid():
    r=evaluate(**fixtures())
    d=render(r)
    assert d['status']=='FINAL_UNSETTLED' and d['settlement'] is None
    with pytest.raises(CommercialError):
        render(r,settlement={'status':'SETTLED','final_gross_minor':1})


def test_settled_statement_only_after_matched_confirmed_fact():
    r=evaluate(**fixtures())
    d=render(r,settlement={'status':'SETTLED','final_gross_minor':4720,'ledger_id':'ledger-ref'})
    assert d['status']=='SETTLED'
    assert d['title']=='Commercial Charge Statement'
    assert d['totals']['gross_inr']=='47.20'
    assert len(d['lines'])==1 and d['lines'][0]['gst_inr']=='7.20'


def test_tampered_total_refused():
    r=evaluate(**fixtures())
    r['totals']['gross_minor']=5000
    with pytest.raises(CommercialError):render(r)

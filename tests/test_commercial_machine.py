"""Deterministic, side-effect-free commercial-machine acceptance scenarios."""
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cow.domain.commercial_machine import (
    Assignment, CommercialError, Context, GSTAssignment, GSTProfile, Measurement,
    evaluate, select_tariff, select_gst, split_tax, validate_tariff,
    validate_assignments,
)

NOW=datetime(2026,11,7,11,0,tzinfo=timezone.utc)


def tax():
    return GSTProfile(id='GST',version=1,seller_ref='SELLER_WB',legal_decision_ref='REVIEWED_DOCUMENT',status='APPROVED',tax_classes={
       'EV_SERVICE':{'treatment':'TAXABLE','supply_group':'EV_SUPPLY',
                     'rates':{'CGST':'9','SGST':'9','UTGST':'0','IGST':'0'}},
       'EXEMPT_CLASS':{'treatment':'EXEMPT','supply_group':'OTHER_SUPPLY',
                     'rates':{'CGST':'0','SGST':'0','UTGST':'0','IGST':'0'}},
    })


def plan(key='STANDARD',channel='ROAMING',basis='ENERGY_KWH',mode='EXCLUSIVE'):
    obj={'key':key,'version':1,'channel':channel,'currency':'INR','price_mode':mode,
         'profiles':{'INSIDE':{'charging':{'basis':basis,'rate':'20','tax_class':'EV_SERVICE'}}}}
    if basis=='TIME_MINUTE':obj['profiles']['INSIDE']['charging']['minute_rounding']='CEIL_STARTED_MINUTE'
    return obj


def fixtures(mode='EXCLUSIVE',channel='ROAMING',verified=True,basis='ENERGY_KWH'):
    p=plan(channel=channel,basis=basis,mode=mode)
    ass=[Assignment('A','STANDARD',1,'ZONE','NORTH',channel,'DEFAULT')]
    gs=[GSTAssignment('G','NORTH',channel,'GST',1)]
    ctx=Context('NORTH',channel,'INSIDE',NOW,'SELLER_WB',verified=verified)
    return dict(context=ctx,event='COMPLETED',assignments=ass,tariffs={('STANDARD',1):p},
                gst_assignments=gs,gst_profiles={('GST',1):tax()},
                measurements={'energy_wh':Measurement(2000,'HAL_VERIFIED')},mode='FINAL')


def fails(code, f):
    with pytest.raises(CommercialError) as exc:
        f()
    assert exc.value.code==code


def test_exclusive_breakdown_and_no_side_effects():
    arg=fixtures()
    arg['tariffs'][('STANDARD',1)]['profiles']['INSIDE']['visit']={'rate':'100','tax_class':'EV_SERVICE','applies_on':['COMPLETED']}
    result=evaluate(**arg)
    assert result['totals']=={'net_minor':14000,'tax_minor':2520,'gross_minor':16520,
                              'cgst_minor':1260,'sgst_minor':1260,'utgst_minor':0,'igst_minor':0}
    assert [l['component'] for l in result['lines']]==['CHARGING','VISIT']
    assert all(l['net_minor']+l['tax_minor']==l['gross_minor'] for l in result['lines'])
    assert not result['may_collect'] and not result['may_command_hal'] and not result['may_issue_tax_invoice']


def test_inclusive_and_mixed_modes_preserve_gross():
    arg=fixtures('INCLUSIVE')
    p=arg['tariffs'][('STANDARD',1)]
    p['profiles']['INSIDE']['charging']['rate']='118'
    p['profiles']['INSIDE']['visit']={'rate':'100','tax_class':'EV_SERVICE',
                                     'applies_on':['COMPLETED'],'price_mode':'EXCLUSIVE'}
    r=evaluate(**arg)
    assert r['totals']['gross_minor']==35400
    assert r['totals']['net_minor']==30000
    assert r['totals']['tax_minor']==5400
    assert r['totals']['cgst_minor']==2700
    assert r['totals']['sgst_minor']==2700


def test_group_precedence_and_fallback():
    args=fixtures()
    corporate=plan('CORPORATE')
    corporate['profiles']['INSIDE']['charging']['rate']='18'
    args['tariffs'][('CORPORATE',1)]=corporate
    group=Assignment('B','CORPORATE',1,'GROUP','C1','ROAMING','DEFAULT')
    args['assignments'].append(group)
    args['context']=Context('NORTH','ROAMING','INSIDE',NOW,'SELLER_WB',pricing_group_id='C1',verified=True)
    r=evaluate(**args)
    assert r['selection']['target_type']=='GROUP'
    assert r['totals']['net_minor']==3600
    args['context']=Context('NORTH','ROAMING','INSIDE',NOW,'SELLER_WB',pricing_group_id='UNKNOWN',verified=True)
    assert evaluate(**args)['selection']['target_type']=='ZONE'


def test_root_replacement_closed_at_exact_boundary():
    p1=plan()
    p2=plan('OTHER');p2['profiles']['INSIDE']['charging']['rate']='22'
    old=Assignment('A','STANDARD',1,'ZONE','NORTH','ROAMING','DEFAULT',ends_at=NOW)
    new=Assignment('B','OTHER',1,'ZONE','NORTH','ROAMING','DEFAULT',starts_at=NOW)
    ts={('STANDARD',1):p1,('OTHER',1):p2}
    validate_assignments([old,new],ts,operational_zones=[('NORTH','ROAMING')])
    picked,source=select_tariff(Context('NORTH','ROAMING','INSIDE',NOW,'SELLER_WB'),[old,new],ts)
    assert picked['key']=='OTHER'
    fails('ASSIGNMENT_CONFLICT',lambda:validate_assignments([
        Assignment('C','STANDARD',1,'ZONE','NORTH','ROAMING','DEFAULT'), new],ts))


def test_scheduled_override_and_interval_boundaries():
    args=fixtures()
    q=plan('NIGHT');q['profiles']['INSIDE']['charging']['rate']='30'
    args['tariffs'][('NIGHT',1)]=q
    interval=Assignment('S','NIGHT',1,'ZONE','NORTH','ROAMING','SCHEDULED',NOW,NOW+timedelta(hours=3))
    args['assignments'].append(interval)
    assert evaluate(**args)['selection']['tariff_key']=='NIGHT'
    args['context']=Context('NORTH','ROAMING','INSIDE',NOW+timedelta(hours=3),'SELLER_WB',verified=True)
    assert evaluate(**args)['selection']['tariff_key']=='STANDARD'


def test_no_operational_zone_default():
    args=fixtures()
    args['assignments'].clear()
    fails('ZONE_DEFAULT_REQUIRED',lambda:evaluate(**args))


def test_missing_or_ambiguous_gst_blocks():
    args=fixtures()
    args['gst_assignments'].clear()
    fails('GST_COVERAGE_INVALID',lambda:evaluate(**args))
    args=fixtures()
    args['gst_assignments'].append(GSTAssignment('SECOND','NORTH','ROAMING','GST',1))
    fails('GST_COVERAGE_INVALID',lambda:evaluate(**args))


def test_unapproved_or_wrong_seller_gst_blocks():
    args=fixtures()
    args['gst_profiles'][('GST',1)]=GSTProfile('GST',1,'SELLER_WB','LEGAL','DRAFT',tax().tax_classes)
    fails('GST_NOT_APPROVED',lambda:evaluate(**args))
    args=fixtures()
    args['context']=Context('NORTH','ROAMING','INSIDE',NOW,'DIFFERENT_SELLER',verified=True)
    fails('GST_SELLER_MISMATCH',lambda:evaluate(**args))


def test_gst_conflicting_tax_heads_and_supply_groups():
    base=tax().tax_classes
    item=copy.deepcopy(base)
    item['EV_SERVICE']['rates']['IGST']='18'
    fails('GST_HEAD_CONFLICT',lambda:GSTProfile('GST',1,'SELLER','LEGAL','APPROVED',item))
    item=copy.deepcopy(base)
    item['EXEMPT_CLASS']['supply_group']='EV_SUPPLY'
    fails('GST_SUPPLY_CONFLICT',lambda:GSTProfile('GST',1,'SELLER','LEGAL','APPROVED',item))


def test_unverified_final_rejected_preview_allowed():
    args=fixtures(verified=False)
    fails('UNVERIFIED_CONTEXT',lambda:evaluate(**args))
    args['mode']='PREVIEW'
    assert evaluate(**args)['status']=='PREVIEW_ONLY'
    assert evaluate(**args)['may_collect'] is False


def test_estimates_block_final_and_upfront_disclosure_not_false_zero():
    args=fixtures()
    args['measurements']['energy_wh']=Measurement(2000,'REQUESTED_ENERGY',estimated=True)
    fails('UNVERIFIED_MEASUREMENT',lambda:evaluate(**args))
    args=fixtures()
    args['event']='UPFRONT'; args['mode']='PREVIEW';args['measurements']={}
    result=evaluate(**args)
    assert result['lines']==[] and result['totals']['gross_minor']==0
    assert result['disclosures'][0]['state']=='NOT_YET_DELIVERED'


def test_qr_disallows_mobile_components_and_requires_proof_for_final():
    args=fixtures(channel='ON_SITE_QR')
    args['tariffs'][('STANDARD',1)]['profiles']['INSIDE']['visit']={'rate':'5','tax_class':'EV_SERVICE','applies_on':['COMPLETED']}
    fails('INVALID_CHANNEL_BLOCK',lambda:evaluate(**args))


def test_distance_progressive_and_waiting_grace():
    args=fixtures()
    profile=args['tariffs'][('STANDARD',1)]['profiles']['INSIDE']
    profile['distance']={'minimum_billable_m':0, 'tax_class':'EV_SERVICE','applies_on':['COMPLETED'],
                         'slabs':[{'up_to_m':5000,'rate_per_km':'10'},{'up_to_m':None,'rate_per_km':'5'}]}
    profile['extras']=[{'id':'WAIT','basis':'WAITING_MINUTE','rate':'2','tax_class':'EV_SERVICE',
                        'applies_on':['COMPLETED'],'grace_seconds':300,'minute_rounding':'CEIL_STARTED_MINUTE'}]
    args['measurements']['travel_m']=Measurement(7000,'ROUTE_RECEIPT')
    args['measurements']['waiting_seconds']=Measurement(480,'DUTY_FACT')
    r=evaluate(**args)
    amounts={l['component']:l['net_minor'] for l in r['lines']}
    assert amounts=={'CHARGING':4000,'DISTANCE_1':5000,'DISTANCE_2':1000,'WAIT':600}


def test_missing_actual_distance_fails_not_zero():
    args=fixtures()
    args['tariffs'][('STANDARD',1)]['profiles']['INSIDE']['distance']={'minimum_billable_m':0,'tax_class':'EV_SERVICE',
        'applies_on':['COMPLETED'],'slabs':[{'up_to_m':None,'rate_per_km':'10'}]}
    fails('MEASUREMENT_MISSING',lambda:evaluate(**args))


def test_session_fee_requires_completed_session_fact():
    args=fixtures(basis='SESSION')
    fails('MEASUREMENT_MISSING',lambda:evaluate(**args))
    args['measurements']['session_completed']=Measurement(1,'VERIFIED_HAL_COMPLETION')
    assert evaluate(**args)['totals']['net_minor']==2000


def test_zero_priced_taxable_not_missing():
    args=fixtures()
    args['tariffs'][('STANDARD',1)]['profiles']['INSIDE']['charging']['rate']='0'
    r=evaluate(**args)
    assert r['totals']['gross_minor']==0
    assert len(r['lines'])==1 and r['lines'][0]['treatment']=='TAXABLE'


def test_non_taxable_classification_is_preserved():
    args=fixtures()
    args['tariffs'][('STANDARD',1)]['profiles']['INSIDE']['charging']['tax_class']='EXEMPT_CLASS'
    r=evaluate(**args)
    assert r['totals']['net_minor']==4000 and r['totals']['tax_minor']==0
    assert r['lines'][0]['treatment']=='EXEMPT'


def test_rounding_remainder_and_minuscule_inclusive_amount():
    # An inclusive 1 paise amount cannot become 2 paise due to independently rounded tax heads.
    base, heads, gross=split_tax(1,{'CGST':Decimal(9),'SGST':Decimal(9),'UTGST':Decimal(0),'IGST':Decimal(0)},'INCLUSIVE')
    assert gross==1 and base+sum(heads.values())==1
    args=fixtures('INCLUSIVE')
    args['tariffs'][('STANDARD',1)]['profiles']['INSIDE']['charging']['rate']='0.01'
    args['measurements']['energy_wh']=Measurement(1000,'HAL_VERIFIED')
    r=evaluate(**args)
    assert r['totals']['gross_minor']==1
    assert sum(r['totals'][h+'_minor'] for h in ['cgst','sgst','igst','utgst'])==r['totals']['tax_minor']


def test_supply_group_rounding_once_across_lines():
    args=fixtures()
    pr=args['tariffs'][('STANDARD',1)]['profiles']['INSIDE']
    pr['charging']['rate']='0.03'
    pr['visit']={'rate':'0.03','tax_class':'EV_SERVICE','applies_on':['COMPLETED']}
    args['measurements']['energy_wh']=Measurement(1000,'HAL_VERIFIED')
    r=evaluate(**args)
    assert sum(l['gross_minor'] for l in r['lines'])==r['totals']['gross_minor']
    assert r['totals']['tax_minor']==1  # 6 paise * 18% rounds once to 1 paise


def test_invalid_inputs_never_silently_coerced():
    args=fixtures()
    plan0=args['tariffs'][('STANDARD',1)]
    plan0['profiles']['INSIDE']['charging']['rate']='1e3'
    fails('INVALID_DECIMAL',lambda:evaluate(**args))
    plan0['profiles']['INSIDE']['charging']['rate']='-1'
    fails('INVALID_DECIMAL',lambda:evaluate(**args))
    plan0['profiles']['INSIDE']['charging']['rate']='20'
    plan0['unexpected']='true'
    fails('INVALID_SHAPE',lambda:evaluate(**args))


def test_expired_or_outside_relation_blocks():
    args=fixtures()
    args['context']=Context('NORTH','ROAMING','OUTSIDE',NOW,'SELLER_WB',verified=True)
    fails('TARIFF_RELATION_UNAVAILABLE',lambda:evaluate(**args))
    args=fixtures()
    args['context']=Context('NORTH','ROAMING','INSIDE',NOW,'SELLER_WB',serviceable=False,verified=True)
    fails('SERVICE_UNAVAILABLE',lambda:evaluate(**args))


def test_identical_input_exactly_identical_output():
    args=fixtures()
    x=evaluate(**args); y=evaluate(**copy.deepcopy(args))
    assert x==y


def test_tax_head_structure_is_bounded_and_equal_intra_state():
    item=copy.deepcopy(tax().tax_classes)
    item['EV_SERVICE']['rates']['SGST']='8'
    fails('GST_HEAD_CONFLICT',lambda:GSTProfile('G',1,'S','L','APPROVED',item))
    item=copy.deepcopy(tax().tax_classes)
    item['EV_SERVICE']['rates']={'CGST':'0','SGST':'0','UTGST':'0','IGST':'18'}
    assert GSTProfile('G',1,'S','L','APPROVED',item)
    item['EV_SERVICE']['rates']={'CGST':'9','SGST':'0','UTGST':'9','IGST':'0'}
    assert GSTProfile('G',1,'S','L','APPROVED',item)


def test_group_tariff_cannot_mask_expired_zone_default():
    args=fixtures()
    corporate=plan('CORP')
    args['tariffs'][('CORP',1)]=corporate
    args['assignments']=[
      Assignment('OLD','STANDARD',1,'ZONE','NORTH','ROAMING','DEFAULT',ends_at=NOW-timedelta(seconds=1)),
      Assignment('GROUP','CORP',1,'GROUP','CORP','ROAMING','DEFAULT')]
    args['context']=Context('NORTH','ROAMING','INSIDE',NOW,'SELLER_WB',pricing_group_id='CORP',verified=True)
    fails('ZONE_DEFAULT_REQUIRED',lambda:evaluate(**args))


def test_unhashable_malformed_input_is_structured_rejection():
    args=fixtures()
    args['tariffs'][('STANDARD',1)]['channel']=[]
    fails('INVALID_TARIFF',lambda:evaluate(**args))
    args=fixtures()
    args['tariffs'][('STANDARD',1)]['profiles']['INSIDE']['charging']['basis']=[]
    fails('INVALID_TARIFF',lambda:evaluate(**args))
    args=fixtures()
    args['tariffs'][('STANDARD',1)]['profiles']['INSIDE']['charging']['price_mode']=[]
    fails('INVALID_PRICE_MODE',lambda:evaluate(**args))
    args=fixtures()
    args['tariffs'][('STANDARD',1)]['profiles']['INSIDE']['visit']={'rate':'5','tax_class':'EV_SERVICE','applies_on':[[]]}
    fails('INVALID_EVENT_RULE',lambda:evaluate(**args))

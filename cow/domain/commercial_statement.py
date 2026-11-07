"""Read-only customer presentation of already calculated/frozen financial facts.

Not an invoice issuer and cannot post financial effects. A legally compliant GST
invoice, if required, must be provided through a separate approved workflow.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Any
from cow.domain.commercial_machine import CommercialError, fail, MAX_MINOR

NOT_TAX_INVOICE = 'Not a GST Tax Invoice. GST breakdown for transparency only.'


def _money(paise: int) -> str:
    if type(paise) is not int or not 0 <= paise <= MAX_MINOR:
        fail('INVALID_STATEMENT', 'Malformed monetary total')
    return f'{paise // 100}.{paise % 100:02d}'


def render(result: Mapping[str,Any], *, settlement: Mapping[str,Any] | None = None) -> dict:
    if not isinstance(result,dict) or result.get('document_authority')!='COMMERCIAL_STATEMENT_ONLY':
        fail('INVALID_STATEMENT', 'Statement requires canonical non-tax-invoice pricing result')
    status=result.get('status')
    if status not in {'PREVIEW_ONLY','FINAL_CALCULATED_NOT_SETTLED'}:
        fail('INVALID_STATEMENT', 'Unknown calculation status')
    totals=result['totals']; rows=result['lines']
    if (type(totals.get('gross_minor')) is not int or
        sum(x['gross_minor'] for x in rows)!=totals['gross_minor'] or
        sum(x['tax_minor'] for x in rows)!=totals['tax_minor'] or
        totals['net_minor']+totals['tax_minor']!=totals['gross_minor']):
        fail('STATEMENT_RECONCILIATION_FAILED', 'Document lines do not match canonical calculation')
    if settlement is not None:
        if status!='FINAL_CALCULATED_NOT_SETTLED' or settlement.get('status')!='SETTLED' or (
            type(settlement.get('final_gross_minor')) is not int or
            settlement['final_gross_minor']!=totals['gross_minor']):
            fail('STATEMENT_NOT_SETTLED', 'Confirmed matching settlement fact required')
        doc_status='SETTLED';title='Commercial Charge Statement'
    elif status=='PREVIEW_ONLY':
        doc_status='ESTIMATE';title='Fare Preview'
    else:
        doc_status='FINAL_UNSETTLED';title='Final Fare Summary'
    display_lines=[]
    for x in rows:
        display_lines.append({'component':x['component'],'quantity':x['quantity'],'unit':x['unit'],
            'rate_inr':x['rate'],'mode':x['price_mode'],'tax_treatment':x['treatment'],
            'tax_class':x['tax_class'],'supply_group':x['supply_group'],
            'net_inr':_money(x['net_minor']),
            'gst':{head:_money(amount) for head,amount in x['tax_heads_minor'].items()},
            'gst_inr':_money(x['tax_minor']),'total_inr':_money(x['gross_minor']),
            'estimated':x['estimated'],'source':x['source']})
    return {'title':title,'status':doc_status,'currency':'INR','lines':display_lines,
        'disclosures':list(result['disclosures']),
        'totals':{k[:-6]+'_inr':_money(v) for k,v in totals.items() if k.endswith('_minor')},
        'selection':dict(result['selection']),'engine_version':result['engine_version'],
        'disclaimer':NOT_TAX_INVOICE,
        'settlement':dict(settlement) if settlement is not None else None,
        'statutory_gst_invoice_issued':False}

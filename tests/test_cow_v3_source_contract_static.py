"""Source-level guard tests. Runtime DB/HTTP testing is a distinct later gate."""
import ast
from pathlib import Path


def project_root():
    return Path(__file__).resolve().parents[1]


def source(path):
    p=project_root()/path
    ast.parse(p.read_text(),filename=str(p))
    return p.read_text()


def test_wallet_posting_single_authority():
    repo=source('cow/users/repository.py')
    booking=source('cow/services/booking_commercial.py')
    ledger=source('cow/services/wallet_authority.py')
    assert 'wallet.balance_paise += order.amount_paise' not in repo
    assert 'wallet.balance_paise-=total' not in booking
    assert 'credit_captured_topup(' in repo
    assert 'debit_booking_settlement(' in booking
    assert "holds_locked(wallet)" in booking
    assert 'CoWWalletOperation.objects.create(' in ledger


def test_hal_physical_start_is_operational_transition():
    booking=source('cow/services/booking_commercial.py')
    assert "if fact_type=='transaction.started':" in booking
    assert "job_lifecycle.transition_locked(job,'CHARGING'" in booking


def test_api_provider_and_model_contracts_exist():
    for name in ('cow/services/booking_trust_nomap.py',
                 'cow/services/nomap_location.py',
                 'cow/services/routing_provider.py',
                 'cow/services/wallet_refunds.py',
                 'cow/users/nomap_routes.py',
                 'cow/drivers/nomap_routes.py',
                 'cow/admin/nomap_routes.py'):
        source(name)
    models=source('cow/models.py')
    mig=source('cow/migrations/0019_cow_wallet_location_operational.py')
    for name in ('CoWWalletOperation','CoWLocationObservation','CoWBookingPreviewTicket',
                 'CoWHandoverTicket','CoWCompletionReview','CoWWalletRefund'):
        assert f'class {name}(' in models
        assert f"name='{name}'" in mig

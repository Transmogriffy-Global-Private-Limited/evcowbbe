"""Exact integer money, deterministic rate rounding, immutable policy snapshot."""
from decimal import Decimal, ROUND_HALF_UP


def quote_amount(service_paise: int, energy_wh: int, per_kwh_paise: int) -> int:
    if min(service_paise, energy_wh, per_kwh_paise) < 0:
        raise ValueError("Negative commercial inputs are invalid")
    # Only round once, at invoice-component boundary; no binary floating point.
    energy_fee = (Decimal(energy_wh) * Decimal(per_kwh_paise) / Decimal(1000)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP)
    return service_paise + int(energy_fee)

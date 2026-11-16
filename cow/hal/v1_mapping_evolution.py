"""CoW-only, pure preflight for the unchanged Go HAL's additive mapping PUT.

This helper does not alter remote mappings or infer physical charger state.
SYNCHRONIZED CoW snapshots represent a previously acknowledged exact Go echo.
The fixed Go API preserves existing connector UUID/number rows; omitting one
from a later PUT cannot retire it. Reject non-expressible mapping changes
*before* Django overwrites its last acknowledged snapshot or attempts Go I/O.
"""

from __future__ import annotations

from uuid import UUID


class UnsupportedMappingEvolution(ValueError):
    """A deterministic, actionable mapping transition failure."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code


_REQUIRED = frozenset(
    {
        "cpo_id",
        "cms_charger_id",
        "charger_ocpp_identity",
        "expected_serial",
        "enabled",
        "connectors",
    }
)


def _uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return parsed.int != 0 and str(parsed) == value


def _snapshot(raw: object, *, original: bool) -> tuple[tuple[str, str, str], set[tuple[str, int]]]:
    code = "HAL_MAPPING_BASELINE_INVALID" if original else "HAL_MAPPING_PROPOSAL_INVALID"
    if type(raw) is not dict or set(raw) != _REQUIRED:
        raise UnsupportedMappingEvolution(code, "Mapping snapshot has unexpected or missing fields")
    if not _uuid(raw["cpo_id"]) or not _uuid(raw["cms_charger_id"]):
        raise UnsupportedMappingEvolution(code, "Mapping scope or charger UUID is invalid")
    if (
        type(raw["charger_ocpp_identity"]) is not str
        or not raw["charger_ocpp_identity"]
        or type(raw["expected_serial"]) is not str
        or type(raw["enabled"]) is not bool
    ):
        raise UnsupportedMappingEvolution(code, "Mapping identity or mutable fields are invalid")

    entries = raw["connectors"]
    if type(entries) is not list or not entries:
        raise UnsupportedMappingEvolution(code, "Mapping requires a nonempty connector set")
    pairs: set[tuple[str, int]] = set()
    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    for entry in entries:
        if type(entry) is not dict or set(entry) != {"cms_connector_id", "ocpp_connector_number"}:
            raise UnsupportedMappingEvolution(code, "Connector mapping contains invalid fields")
        connector_id = entry["cms_connector_id"]
        number = entry["ocpp_connector_number"]
        if not _uuid(connector_id) or type(number) is not int or not 1 <= number <= 2147483647:
            raise UnsupportedMappingEvolution(code, "Connector identity or OCPP number is invalid")
        if connector_id in seen_ids or number in seen_numbers:
            raise UnsupportedMappingEvolution(code, "Connector UUID/number appears more than once")
        seen_ids.add(connector_id)
        seen_numbers.add(number)
        pairs.add((connector_id, number))
    identities = (raw["cpo_id"], raw["cms_charger_id"], raw["charger_ocpp_identity"])
    return identities, pairs


def verify_additive_mapping_change(acknowledged: object, proposed: object) -> str:
    """Validate a synced CoW mapping change against the existing Go v1 contract.

    Returns ``UNCHANGED`` or ``SUPPORTED``. Raises for every unsupported or
    malformed transition. Physical connector availability is not represented in
    Go mapping snapshots; Django's ``ChargerConnector.active`` controls that.

    The function makes no network, database, payment, or hardware calls.
    """
    old_ids, old_pairs = _snapshot(acknowledged, original=True)
    new_ids, new_pairs = _snapshot(proposed, original=False)
    if old_ids != new_ids:
        raise UnsupportedMappingEvolution(
            "HAL_MAPPING_IDENTITY_CHANGE_UNSUPPORTED",
            "The unchanged Go HAL cannot replace a mapped scope, charger UUID, or OCPP identity",
        )
    if not old_pairs.issubset(new_pairs):
        raise UnsupportedMappingEvolution(
            "HAL_MAPPING_CONNECTOR_CHANGE_UNSUPPORTED",
            "The unchanged Go HAL cannot remove, replace, or renumber an enrolled connector",
        )
    return "UNCHANGED" if acknowledged == proposed else "SUPPORTED"

"""Pure, replayable normalization: factual evidence is not a UI command result."""
from dataclasses import dataclass, field
from cow.hal.contract import fingerprint, validate_fact

class EvidenceConflict(ValueError):
    pass

@dataclass
class TraceProjection:
    seen: dict[str,str] = field(default_factory=dict)
    transactions: dict[str,dict] = field(default_factory=dict)
    pending: dict[str,list] = field(default_factory=dict)
    commands: dict[str,str] = field(default_factory=dict)

    def ingest(self, fact: dict):
        validate_fact(fact)
        id_, digest_ = fact["event_id"], fingerprint(fact)
        if id_ in self.seen:
            if self.seen[id_] != digest_:
                raise EvidenceConflict("HAL event id collision with differing payload")
            return "DUPLICATE"
        self.seen[id_] = digest_
        kind, data = fact["kind"], fact["data"]
        if kind.startswith("Command"):
            self.commands[data["command_id"]] = kind
            return "COMMAND_ONLY"
        if kind == "TransactionStarted":
            tx = data["transaction_id"]
            if tx in self.transactions:
                if self.transactions[tx]["command_id"] != data["command_id"]:
                    raise EvidenceConflict("Conflicting physical transaction owner")
                return "ALREADY_STARTED"
            self.transactions[tx] = {"command_id":data["command_id"],"state":"ACTIVE",
                                     "meter_wh":None,"end_wh":None}
            queued = self.pending.pop(tx, [])
            for q in sorted(queued, key=lambda p:p["emitted_at"]):
                self._apply(q)
            return "TRANSACTION_STARTED"
        if kind in ("MeterSampleObserved","TransactionEnded"):
            tx = data["transaction_id"]
            if tx not in self.transactions:
                self.pending.setdefault(tx, []).append(fact)
                return "WAITING_FOR_START"
            return self._apply(fact)
        return "OBSERVED"

    def _apply(self, fact):
        tx = self.transactions[fact["data"]["transaction_id"]]
        if fact["kind"] == "MeterSampleObserved":
            amount = fact["data"]["energy_wh"]
            # Samples may arrive out-of-order. Never let an older sample lower projection.
            tx["meter_wh"] = max(tx["meter_wh"] if tx["meter_wh"] is not None else amount, amount)
            return "METER_RECORDED"
        if fact["kind"] == "TransactionEnded":
            tx["state"] = "ENDED"
            return "TRANSACTION_ENDED"
        return "OBSERVED"

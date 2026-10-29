"""Deterministic fake HAL used by the contract suite, not by live billing."""
from datetime import datetime, timezone
from uuid import uuid4
from cow.hal.contract import VERSION, COMMANDS, make_command, validate_fact

class FakeHAL:
    def __init__(self):
        self.operations={}
        self.transactions={}
        self.events=[]

    def accept(self,command):
        if command["contract"]!=VERSION or command["kind"] not in COMMANDS:
            return 422,{"code":"UNSUPPORTED"}
        key=command["command_id"]
        existing=self.operations.get(key)
        if existing:
            return (202 if existing==command else 409), {"command_id":key,"state":"HAL_ACCEPTED" if existing==command else "CONFLICT"}
        self.operations[key]=command
        return 202,{"command_id":key,"state":"HAL_ACCEPTED"}

    def fact(self,kind,charger_identity,data):
        event={"contract":VERSION,"event_id":str(uuid4()),"kind":kind,
               "charger_identity":charger_identity,"emitted_at":datetime.now(timezone.utc).isoformat(),"data":data}
        validate_fact(event)
        self.events.append(event)
        return event

    def start_physical(self,command_id,transaction_id,connector_number):
        c=self.operations[command_id]
        if c["kind"]!="StartCharging":
            raise ValueError("Wrong command action")
        if transaction_id in self.transactions:
            raise ValueError("Physical transaction already exists")
        self.transactions[transaction_id]=c
        return self.fact("TransactionStarted",c["charger_identity"],{
            "command_id":command_id,"transaction_id":transaction_id,"connector_number":connector_number})

    def meter(self,transaction_id,energy_wh):
        c=self.transactions[transaction_id]
        return self.fact("MeterSampleObserved",c["charger_identity"],{"transaction_id":transaction_id,"energy_wh":energy_wh})

    def end(self,transaction_id):
        c=self.transactions[transaction_id]
        return self.fact("TransactionEnded",c["charger_identity"],{"transaction_id":transaction_id})

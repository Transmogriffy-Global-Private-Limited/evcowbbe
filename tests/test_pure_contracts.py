"""Runnable without Django/PostgreSQL; tests actual pure domain/adapter code."""
import unittest
from uuid import uuid4
from cow.hal.contract import (VERSION, canonical, fingerprint, sign, verify, validate_fact,
                              FactContractError,make_command)
from cow.hal.reducer import TraceProjection, EvidenceConflict
from cow.hal.simulator import FakeHAL
from cow.services.pricing import quote_amount

class PureContractTests(unittest.TestCase):
    def setUp(self):
        self.hal=FakeHAL()
        self.command_id=str(uuid4())
        self.cmd=make_command(self.command_id,"physical-001","StartCharging",{"connector_number":1})
        self.assertEqual(self.hal.accept(self.cmd)[0],202)

    def test_command_accepted_not_physical_start(self):
        response=self.hal.fact("CommandAccepted","physical-001",{"command_id":self.command_id})
        state=TraceProjection()
        self.assertEqual(state.ingest(response),"COMMAND_ONLY")
        self.assertFalse(state.transactions)

    def test_duplicate_retry_exactly_once(self):
        started=self.hal.start_physical(self.command_id,"txn-8",1)
        state=TraceProjection()
        self.assertEqual(state.ingest(started),"TRANSACTION_STARTED")
        self.assertEqual(state.ingest(started),"DUPLICATE")
        self.assertEqual(len(state.transactions),1)

    def test_same_event_id_conflicting_payload_is_rejected(self):
        started=self.hal.start_physical(self.command_id,"txn-9",1)
        state=TraceProjection()
        state.ingest(started)
        malicious={**started,"data":{**started["data"],"transaction_id":"DIFFERENT"}}
        with self.assertRaises(EvidenceConflict):state.ingest(malicious)

    def test_out_of_order_meter_before_start_is_caught_up(self):
        self.hal.start_physical(self.command_id,"txn-10",1)
        earlier=self.hal.meter("txn-10",2200)
        later=self.hal.meter("txn-10",3000)
        state=TraceProjection()
        self.assertEqual(state.ingest(later),"WAITING_FOR_START")
        self.assertEqual(state.ingest(earlier),"WAITING_FOR_START")
        start=self.hal.events[0]
        state.ingest(start)
        self.assertEqual(state.transactions["txn-10"]["meter_wh"],3000)

    def test_meter_is_not_energy_delta(self):
        self.assertEqual(quote_amount(5000,1460,1700),7482)
        self.assertEqual(quote_amount(0,1,500),1) # half-up once in paise
        with self.assertRaises(ValueError):quote_amount(0,-1,500)

    def test_hmac_freshness_and_tamper(self):
        payload=canonical(self.cmd)
        secret="X"*40
        signature=sign(secret,1000,payload)
        self.assertTrue(verify(secret,1000,payload,signature,1001))
        self.assertFalse(verify(secret,1000,payload+b"x",signature,1001))
        self.assertFalse(verify(secret,1000,payload,signature,2000))

    def test_hal_same_command_id_cannot_change_arguments(self):
        self.assertEqual(self.hal.accept(self.cmd)[0],202)
        modified={**self.cmd,"parameters":{"connector_number":2}}
        self.assertEqual(self.hal.accept(modified)[0],409)

    def test_invalid_fact_contract_never_accepted(self):
        event=self.hal.fact("CommandAccepted","physical-001",{"command_id":self.command_id})
        event["contract"]="cow-hal.v99"
        with self.assertRaises(FactContractError):validate_fact(event)

if __name__=="__main__":unittest.main()

import json
import pathlib
import unittest
from cow.hal.contract import validate_fact, make_command

ROOT=pathlib.Path(__file__).parents[1]/"contracts/hal/v1"

class ContractFixtures(unittest.TestCase):
    def test_versioned_wire_fixtures(self):
        from jsonschema import Draft202012Validator, FormatChecker
        Draft202012Validator.check_schema(json.loads((ROOT/"command.schema.json").read_text()))
        for basename in ("start_command","start_fact"):
            command=basename=="start_command"
            schema=json.loads((ROOT/("command.schema.json" if command else "fact.schema.json")).read_text())
            payload=json.loads((ROOT/"fixtures"/(basename+".json")).read_text())
            Draft202012Validator(schema,format_checker=FormatChecker()).validate(payload)
            if command:
                self.assertEqual(payload,make_command(payload["command_id"],payload["charger_identity"],payload["kind"],payload["parameters"]))
            else:
                self.assertEqual(payload,validate_fact(payload))

if __name__=="__main__":unittest.main()

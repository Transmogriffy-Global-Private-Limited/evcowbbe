import unittest
from scripts.compare_openapi import diff

class OpenAPIGate(unittest.TestCase):
    def test_missing_and_mutated_contract_is_blocking(self):
        old={"paths":{"/api/v1/app/me":{"get":{"responses":{"200":{"description":"ok"}}}}}}
        self.assertEqual(len(diff(old,{"paths":{}})[0]),1)
        changed={"paths":{"/api/v1/app/me":{"get":{"responses":{"200":{"description":"changed"}}}}}}
        self.assertEqual(len(diff(old,changed)[1]),1)
        self.assertEqual(diff(old,old),([],[],[]))

if __name__=="__main__":unittest.main()

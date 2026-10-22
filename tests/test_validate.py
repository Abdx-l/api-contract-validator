import json
import unittest
from pathlib import Path

from validate import check_schema, run, validate_case


ROOT = Path(__file__).resolve().parents[1] / "examples"
SPEC = json.loads((ROOT / "openapi.json").read_text())


class ContractTests(unittest.TestCase):
    def test_valid_fixture(self):
        cases = json.loads((ROOT / "cases.json").read_text())
        self.assertTrue(run(SPEC, cases)[0]["passed"])

    def test_bad_path_and_response(self):
        errors = validate_case(SPEC, {"method": "GET", "path": "/orders/0", "status": 200, "response": {"id": True, "status": "unknown", "extra": 1}})
        self.assertTrue(any("request.path.id: below minimum" in e for e in errors))
        self.assertTrue(any("response.body.id: expected integer" in e for e in errors))
        self.assertTrue(any("response.body.status: value is outside enum" in e for e in errors))
        self.assertTrue(any("response.body.extra: unexpected property" in e for e in errors))

    def test_missing_route_and_required_field(self):
        self.assertIn("no operation", validate_case(SPEC, {"method": "POST", "path": "/orders/3", "status": 200})[0])
        errors = check_schema(SPEC, {"$ref": "#/components/schemas/Order"}, {"id": 1})
        self.assertIn("$.status: required", errors)


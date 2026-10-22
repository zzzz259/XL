import json
import sys
import unittest
from pathlib import Path

from catalog_updates import validate_catalog, validate_source

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "xl_updata_server"))
from server_app.gacha_catalog import validate_catalog as validate_producer


class ContractTests(unittest.TestCase):
    def test_shared_catalog_boundaries(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/catalog-contract.json").read_text(
                encoding="utf-8"
            )
        )
        for validator in (validate_catalog, validate_producer):
            validator(fixture["valid"])
            for catalog in fixture["invalid_catalogs"]:
                with (
                    self.subTest(validator=validator, catalog=catalog),
                    self.assertRaises(ValueError),
                ):
                    validator(catalog)
        for source in fixture["invalid_sources"]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_source(source)

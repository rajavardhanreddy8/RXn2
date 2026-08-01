import json

from scripts.fetch_pubchem_seeded import finalize_partial, records_from_payload


def test_pubchem_payload_preserves_identifier_and_structure():
    records = records_from_payload({
        "PropertyTable": {"Properties": [{
            "CID": 1983,
            "Title": "Acetaminophen",
            "SMILES": "CC(=O)NC1=CC=C(C=C1)O",
            "InChI": "InChI=1S/C8H9NO2",
            "InChIKey": "RZVAJINKPMORJF-UHFFFAOYSA-N",
        }]}
    })
    assert records == [{
        "CID": 1983,
        "Title": "Acetaminophen",
        "IsomericSMILES": "CC(=O)NC1=CC=C(C=C1)O",
        "InChI": "InChI=1S/C8H9NO2",
        "InChIKey": "RZVAJINKPMORJF-UHFFFAOYSA-N",
    }]


def test_pubchem_finalization_removes_duplicate_rows(tmp_path):
    partial = tmp_path / "properties.jsonl.partial"
    output = tmp_path / "properties.jsonl"
    record = {
        "CID": 1983,
        "Title": "Acetaminophen",
        "InChIKey": "RZVAJINKPMORJF-UHFFFAOYSA-N",
    }
    partial.write_text((json.dumps(record) + "\n") * 2, encoding="utf-8")
    assert finalize_partial(partial, output) == (1, 1)

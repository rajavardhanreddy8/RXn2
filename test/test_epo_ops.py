import json

import pytest

from scripts.acquire_epo_ops import artifact_endpoints, batch_publications, docdb_identifier


def test_epo_batch_identifiers_are_deduplicated_and_validated(tmp_path):
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            {
                "completed_ocr_publications": ["WO-2025094207-A1"],
                "queued_documents": [
                    {"publication_number": "WO-2025094207-A1"},
                    {"publication_number": "WO-2008024820-A3"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert batch_publications(batch) == ["WO-2008024820-A3", "WO-2025094207-A1"]
    assert docdb_identifier("WO-2025094207-A1") == "WO.2025094207.A1"
    with pytest.raises(ValueError):
        docdb_identifier("not-a-publication")


def test_description_endpoint_is_explicitly_opt_in():
    identifier = "WO.2025094207.A1"
    default = artifact_endpoints(identifier)
    assert set(default) == {"bibliographic.xml", "family.xml"}

    full_text = artifact_endpoints(identifier, include_description=True)
    assert set(full_text) == {"bibliographic.xml", "family.xml", "description.xml"}
    assert full_text["description.xml"].endswith(
        "/published-data/publication/docdb/WO.2025094207.A1/description"
    )

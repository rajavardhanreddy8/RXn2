from scripts.import_epo_ops import parse_family


def test_parse_epo_family_docdb_members(tmp_path):
    path = tmp_path / "family.xml"
    path.write_text(
        """<?xml version="1.0"?>
        <ops:world-patent-data xmlns="http://www.epo.org/exchange" xmlns:ops="http://ops.epo.org">
          <ops:patent-family><ops:family-member><publication-reference>
            <document-id document-id-type="docdb"><country>WO</country><doc-number>2022144924</doc-number><kind>A1</kind><date>20220707</date></document-id>
          </publication-reference></ops:family-member></ops:patent-family>
        </ops:world-patent-data>""",
        encoding="utf-8",
    )
    assert parse_family(path) == [{
        "publication_number": "WO-2022144924-A1",
        "country_code": "WO",
        "kind_code": "A1",
        "publication_date": "2022-07-07",
    }]

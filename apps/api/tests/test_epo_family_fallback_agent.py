from pathlib import Path

from scripts.run_epo_family_fallback_agent import (
    candidate_rank,
    flexible_docdb_identifier,
    fulltext_candidates,
)


def test_flexible_docdb_accepts_legacy_kind_without_digit():
    assert flexible_docdb_identifier("US-5157156-A") == "US.5157156.A"
    assert flexible_docdb_identifier("CN-101575319-B") == "CN.101575319.B"


def test_family_candidates_prefer_fulltext_kind_and_ep(tmp_path: Path):
    xml = tmp_path / "family.xml"
    xml.write_text(
        """<ops:world-patent-data xmlns:ops='http://ops.epo.org' xmlns='http://www.epo.org/exchange'>
        <ops:family><family-member>
          <publication-reference><document-id document-id-type='docdb'><country>WO</country><doc-number>1</doc-number><kind>A3</kind></document-id></publication-reference>
          <publication-reference><document-id document-id-type='docdb'><country>US</country><doc-number>2</doc-number><kind>B2</kind></document-id></publication-reference>
          <publication-reference><document-id document-id-type='docdb'><country>EP</country><doc-number>3</doc-number><kind>A1</kind></document-id></publication-reference>
        </family-member></ops:family></ops:world-patent-data>""",
        encoding="utf-8",
    )
    assert fulltext_candidates(xml, "WO-1-A3", 5) == ["EP-3-A1", "US-2-B2"]


def test_candidate_rank_prefers_a1_to_b2():
    a1 = {"kind_code": "A1", "country_code": "US", "publication_date": None, "publication_number": "US-1-A1"}
    b2 = {"kind_code": "B2", "country_code": "EP", "publication_date": None, "publication_number": "EP-1-B2"}
    assert candidate_rank(a1) < candidate_rank(b2)

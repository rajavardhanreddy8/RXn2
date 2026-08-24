from scripts.ingest_performed_route import canonical_compound_id
from test_ingest_performed_route import make_database


def test_derived_hydrate_reuses_validated_parent_active_moiety(tmp_path):
    db = make_database(tmp_path / "fixture.sqlite")
    try:
        compound_id, inserted = canonical_compound_id(
            db,
            {
                "compound_id": "DERIVED:FIXTURE-MONOHYDRATE",
                "preferred_name": "Fixture drug monohydrate",
                "smiles": "CCO.O",
                "inchi": "InChI=1S/C2H6O.H2O/c1-2-3;/h3H,2H2,1H3;1H2",
                "inchi_key": "FIXTUREMONOHYD-RATEPARENT-X",
                "connectivity_key": "FIXTUREMONOHYD",
                "molecular_formula": "C2H8O2",
                "molecular_weight": 64.08,
                "material_form": "hydrate",
                "active_moiety_compound_id": "CHEMBL-FIXTURE",
                "review_status": "needs_review",
                "compound_source_id": "epo_ops",
                "structure_source": "fixture",
                "toolkit_name": "fixture",
                "toolkit_version": "1",
            },
        )
        assert inserted is True
        row = db.execute(
            "SELECT active_moiety_id, material_form FROM compound WHERE compound_id = ?",
            (compound_id,),
        ).fetchone()
        assert dict(row) == {"active_moiety_id": "moiety:fixture", "material_form": "hydrate"}
    finally:
        db.close()

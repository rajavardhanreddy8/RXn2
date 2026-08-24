from scripts.ingest_performed_route import canonical_compound_id
from test_ingest_performed_route import make_database


def test_explicit_hydrate_material_form_overrides_disconnected_smiles_fallback(tmp_path):
    db = make_database(tmp_path / "fixture.sqlite")
    try:
        compound_id, inserted = canonical_compound_id(
            db,
            {
                "compound_id": "DERIVED:FIXTURE-HYDRATE",
                "preferred_name": "fixture monohydrate",
                "smiles": "CCO.O",
                "inchi": "InChI=1S/C2H6O.H2O/c1-2-3;/h3H,2H2,1H3;1H2",
                "inchi_key": "FIXTUREHYDRATE-EXPLICITXX-X",
                "connectivity_key": "FIXTUREHYDRATE",
                "molecular_formula": "C2H8O2",
                "molecular_weight": 64.08,
                "material_form": "hydrate",
                "review_status": "needs_review",
                "compound_source_id": "epo_ops",
                "structure_source": "fixture",
                "toolkit_name": "fixture",
                "toolkit_version": "1",
            },
        )
        assert inserted is True
        assert compound_id == "DERIVED:FIXTURE-HYDRATE"
        row = db.execute(
            "SELECT material_form FROM compound WHERE compound_id = ?", (compound_id,)
        ).fetchone()
        assert row["material_form"] == "hydrate"
    finally:
        db.close()

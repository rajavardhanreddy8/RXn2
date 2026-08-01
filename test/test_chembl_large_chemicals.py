from scripts.fetch_chembl_large_chemicals import normalize


def test_large_chemical_record_normalizes_structure_and_modality():
    record = normalize({
        "molecule_chembl_id": "CHEMBL502097",
        "molecule_type": "Oligonucleotide",
        "pref_name": "MIPOMERSEN SODIUM",
        "molecule_hierarchy": {"parent_chembl_id": "CHEMBL2219536"},
        "molecule_structures": {
            "canonical_smiles": "CC",
            "standard_inchi": "InChI=1S/C2H6",
            "standard_inchi_key": "OTMSDBZUPAUEDD-UHFFFAOYSA-N",
        },
        "molecule_synonyms": [
            {"molecule_synonym": "Kynamro", "syn_type": "TRADE_NAME"}
        ],
    })
    assert record["modality"] == "oligonucleotide"
    assert record["identifiers"] == {"CHEMBL": "CHEMBL2219536"}
    assert record["compound"]["material_form"] == "salt_or_form"
    assert record["aliases"] == [{"value": "Kynamro", "type": "TRADE_NAME"}]

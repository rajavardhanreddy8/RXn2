from scripts.build_epo_reaction_candidates import resolve_mention, role_candidates


def test_role_requires_explicit_adjacent_cue():
    text = "The mixture was filtered. Ethanol was mentioned later."
    start = text.index("Ethanol")
    assert role_candidates(text, start, start + len("Ethanol")) == []


def test_explicit_product_and_solvent_cues_are_detected():
    product = "The reaction afforded paracetamol as a solid."
    start = product.index("paracetamol")
    assert [row["role"] for row in role_candidates(product, start, start + 11)] == [
        "produced"
    ]

    solvent = "The residue was dissolved in ethanol and cooled."
    start = solvent.index("ethanol")
    assert [row["role"] for row in role_candidates(solvent, start, start + 7)] == [
        "solvent"
    ]


def test_duplicate_names_collapse_through_exact_structure():
    mention = {
        "candidate_entities": [
            {"entity_type": "drug", "entity_id": "drug:a"},
            {"entity_type": "drug", "entity_id": "drug:b"},
        ]
    }
    drug_compounds = {
        "drug:a": [
            {
                "compound_id": "CHEMBL1",
                "inchi_key": "AAAA-BBBB-C",
                "connectivity_key": "AAAA",
            }
        ],
        "drug:b": [
            {
                "compound_id": "CHEMBL2",
                "inchi_key": "AAAA-BBBB-C",
                "connectivity_key": "AAAA",
            }
        ],
    }
    resolved = resolve_mention(mention, "CHEMBL2", {}, drug_compounds)
    assert resolved["resolution_level"] == "exact_structure"
    assert resolved["canonical_compound_id"] == "CHEMBL2"
    assert resolved["candidate_compound_ids"] == ["CHEMBL1", "CHEMBL2"]


def test_stereochemical_ambiguity_is_not_exact_resolution():
    mention = {
        "candidate_entities": [
            {"entity_type": "compound", "entity_id": "CHEMBL1"},
            {"entity_type": "compound", "entity_id": "CHEMBL2"},
        ]
    }
    compounds = {
        "CHEMBL1": {
            "compound_id": "CHEMBL1",
            "inchi_key": "AAAA-STEREO1-N",
            "connectivity_key": "AAAA",
        },
        "CHEMBL2": {
            "compound_id": "CHEMBL2",
            "inchi_key": "AAAA-STEREO2-N",
            "connectivity_key": "AAAA",
        },
    }
    resolved = resolve_mention(mention, "CHEMBL1", compounds, {})
    assert resolved["resolution_level"] == "connectivity_only"

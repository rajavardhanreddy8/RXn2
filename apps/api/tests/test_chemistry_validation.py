from apps.api.app.chemistry import (
    screen_atom_conservation,
    validate_mapped_reaction,
    validate_provisional_reaction,
)


def test_provisional_reaction_validation_rejects_self_transformation():
    result = validate_provisional_reaction(["CCO"], "CCO")
    assert result.status == "rejected"
    assert result.reason == "self_transformation"
    assert result.atom_mapping_status == "pending"


def test_provisional_reaction_validation_requires_parseable_structures():
    result = validate_provisional_reaction(["not-smiles"], "CCO")
    assert result.status == "rejected"
    assert result.reason == "unparseable_resolved_structure"


def test_atom_conservation_screen_is_conservative_and_not_atom_mapping():
    passed = screen_atom_conservation(["CC(=O)OC(=O)C", "Nc1ccc(O)cc1"], "CC(=O)Nc1ccc(O)cc1")
    assert passed.status == "validated"
    assert passed.reason == "element_conservation_passed"
    assert passed.atom_mapping_status == "pending"

    missing = screen_atom_conservation(["OC(=O)C=CC(=O)O"], "COC(=O)C=CC(=O)OC")
    assert missing.status == "unresolved"
    assert missing.reason == "missing_consumed_atom_source"
    assert missing.missing_product_atoms == {"C": 2}


def test_mapped_reaction_validation_requires_complete_structure_preserving_maps():
    result = validate_mapped_reaction(
        "CCO>>CC=O", "[CH3:1][CH2:2][OH:3]>>[CH3:1][CH:2]=[O:3]"
    )
    assert result.status == "validated"
    assert result.product_atom_coverage == 1.0

    incomplete = validate_mapped_reaction(
        "CCO>>CC=O", "[CH3:1][CH2:2][OH:3]>>[CH3:1]C=[O:3]"
    )
    assert incomplete.status == "unresolved"
    assert incomplete.reason == "incomplete_product_atom_mapping"

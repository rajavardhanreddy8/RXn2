from apps.api.app.chemistry import validate_provisional_reaction


def test_provisional_reaction_validation_rejects_self_transformation():
    result = validate_provisional_reaction(["CCO"], "CCO")
    assert result.status == "rejected"
    assert result.reason == "self_transformation"
    assert result.atom_mapping_status == "pending"


def test_provisional_reaction_validation_requires_parseable_structures():
    result = validate_provisional_reaction(["not-smiles"], "CCO")
    assert result.status == "rejected"
    assert result.reason == "unparseable_resolved_structure"

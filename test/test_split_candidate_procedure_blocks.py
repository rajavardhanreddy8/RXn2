from scripts.split_candidate_procedure_blocks import is_relation_ready, procedure_blocks


def test_procedure_blocks_preserve_each_heading_delimited_source_block():
    source = (
        "Preamble that is not a procedure.\n"
        "Example 1\nA was added to B and stirred. Product C was obtained. " + "x" * 160 + "\n"
        "Preparation 2\nD was charged and heated. Product E was isolated. " + "y" * 160
    )
    blocks = procedure_blocks(source)
    assert len(blocks) == 2
    assert blocks[0][2].startswith("Example 1")
    assert blocks[1][2].startswith("Preparation 2")
    assert source[blocks[0][0]:blocks[0][1]] == blocks[0][2]


def test_procedure_blocks_do_not_split_a_single_heading():
    assert procedure_blocks("Example 1\nA was added and product obtained. " + "x" * 200) == []


def test_relation_ready_requires_executed_operation_and_outcome_not_reference_only():
    assert is_relation_ready("A was charged, stirred, and product was obtained.")
    assert not is_relation_ready("A was charged and stirred.")
    assert not is_relation_ready("Product was prepared as described in Example 4.")

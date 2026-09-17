from pathlib import Path

from scripts.extract_epo_example_blocks import extract_blocks


def test_extracts_separate_native_example_blocks(tmp_path: Path):
    directory = tmp_path / "EP-123-A1"
    directory.mkdir()
    path = directory / "description.xml"
    path.write_text(
        """<?xml version='1.0'?>
        <root><description lang='EN'>
          <p>[0001] Background text.</p>
          <p>Example 1</p>
          <p>[0010] Material A was added to material B.</p>
          <p>[0011] Product C was obtained in 80% yield.</p>
          <p>Example 2: Preparation of D</p>
          <p>[0012] D was isolated.</p>
        </description></root>""",
        encoding="utf-8",
    )

    rows = extract_blocks(path)

    assert [row["heading"] for row in rows] == ["Example 1", "Example 2"]
    assert rows[0]["paragraph_start"] == "[0010]"
    assert "Product C was obtained" in rows[0]["text"]
    assert rows[0]["source_artifact_sha256"]
    assert rows[0]["human_review_required"] is True


def test_ignores_description_before_first_example(tmp_path: Path):
    directory = tmp_path / "WO-9-A1"
    directory.mkdir()
    path = directory / "description.xml"
    path.write_text(
        "<root><description lang='ES'><p>[1] Antecedentes.</p>"
        "<p>Ejemplo 7</p><p>[2] Se obtuvo el producto.</p></description></root>",
        encoding="utf-8",
    )

    rows = extract_blocks(path)

    assert len(rows) == 1
    assert rows[0]["heading"] == "Ejemplo 7"
    assert "Antecedentes" not in rows[0]["text"]


def test_does_not_treat_plural_examples_section_as_one_example(tmp_path: Path):
    directory = tmp_path / "EP-7-A1"
    directory.mkdir()
    path = directory / "description.xml"
    path.write_text(
        "<root><description lang='EN'><p>Examples</p><p>Overview.</p>"
        "<p>Example 1</p><p>Material was charged.</p></description></root>",
        encoding="utf-8",
    )
    rows = extract_blocks(path)
    assert len(rows) == 1
    assert rows[0]["heading"] == "Example 1"

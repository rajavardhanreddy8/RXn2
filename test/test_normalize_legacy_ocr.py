import json

from scripts.normalize_legacy_ocr import normalize_legacy_result, split_pages


def test_split_pages_removes_image_links():
    assert split_pages("<PAGE>\nfirst\n![](images/a.jpg)\n<PAGE>\nsecond") == ["first", "second"]


def test_normalize_legacy_result_creates_importable_bundle(tmp_path):
    directory = tmp_path / "WO-2022144924-A1"
    directory.mkdir()
    (directory / "result.md").write_text("<PAGE>\none\n<PAGE>\ntwo\n", encoding="utf-8")
    (directory / "_SUCCESS").write_text(
        json.dumps({"completed_at": "2026-08-07T00:00:00+00:00", "model": "legacy"}),
        encoding="utf-8",
    )
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")

    result = normalize_legacy_result(directory, "WO-2022144924-A1", source)

    assert result["pages"] == 2
    payload = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "succeeded"
    assert payload["text"] == "one\n\ntwo"
    assert len(list(tmp_path.glob(".WO-2022144924-A1.legacy-*"))) == 1

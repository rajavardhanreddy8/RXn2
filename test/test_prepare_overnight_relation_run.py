from scripts.prepare_overnight_relation_run import launcher_notebook


def test_launcher_notebook_is_self_contained_and_pins_its_bundle_path():
    notebook = launcher_notebook("/content/drive/MyDrive/RXN2/relation-extraction/overnight-v3")
    assert notebook["nbformat"] == 4
    code = "".join(notebook["cells"][1]["source"])
    assert "drive.mount" in code
    assert "RXN2_RELATION_DRIVE_ROOT" in code
    assert "colab_overnight_runner.py" in code
    assert "overnight-v3" in code

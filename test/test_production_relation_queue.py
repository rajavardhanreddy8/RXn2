from types import SimpleNamespace

from scripts import run_production_relation_queue as runner


def test_storage_preflight_requires_available_raw_store_and_capacity(monkeypatch, tmp_path):
    calls = []

    class Policy:
        def require_raw_root(self):
            calls.append("raw")

    monkeypatch.setattr(runner.StoragePolicy, "load", lambda _: Policy())
    monkeypatch.setattr(runner, "ensure_capacity", lambda policy, incoming: calls.append((policy, incoming)))
    runner.assert_storage_ready(tmp_path / "policy.json")
    assert calls[0] == "raw"
    assert calls[1][1] == 0

import hashlib
import io
import json

from scripts.acquire_lowe_uspto import ARTICLE_URL, ASSET_NAMES, download_snapshot
from scripts.local_automation import single_instance
from scripts.prepare_lowe_uspto import record


class Response(io.BytesIO):
    def __init__(self, payload, code=200):
        super().__init__(payload)
        self.code = code

    def getcode(self):
        return self.code

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def test_download_snapshot_writes_verified_drive_release(tmp_path):
    payloads = {name: f"reaction>{name}".encode() for name in ASSET_NAMES}
    assets = [
        {"id": index, "name": name, "size": len(payload), "download_url": f"https://example.test/{index}", "supplied_md5": hashlib.md5(payload).hexdigest()}
        for index, (name, payload) in enumerate(payloads.items(), 1)
    ]
    article = {"id": 5104873, "version": 1, "doi": "doi", "license": {"name": "CC0"}, "files": assets}

    def opener(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        if url == ARTICLE_URL:
            return Response(json.dumps(article).encode())
        return Response(next(payload for asset, payload in zip(assets, payloads.values()) if asset["download_url"] == url))

    result = download_snapshot(tmp_path, opener=opener)
    release = tmp_path / "lowe_uspto_reactions" / "2017-06-13"
    assert [item["status"] for item in result["files"]] == ["downloaded", "downloaded"]
    assert (release / "release-metadata.json").is_file()
    assert all((release / name).read_bytes() == payloads[name] for name in ASSET_NAMES)


def test_single_instance_recovers_a_dead_pid_lock(tmp_path):
    lock = tmp_path / "automation.lock"
    lock.write_text("pid=999999 started=old\n", encoding="utf-8")
    with single_instance(lock):
        assert lock.is_file()
    assert not lock.exists()


def test_lowe_record_preserves_weak_provenance():
    parsed = record("A>B>C\tUS-1-A1\t12\t2016\t80\t79\n", "grants.7z", 4)
    assert parsed["source_record_id"] == "grants.7z:4"
    assert parsed["patent_number"] == "US-1-A1"
    assert parsed["calculated_yield"] == 79
    assert parsed["supervision_tier"] == "weak_pretraining"

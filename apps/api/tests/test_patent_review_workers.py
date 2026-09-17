from scripts.run_patent_review_workers import interleave_publications, shard_records


def test_shards_are_distinct_and_cover_every_record():
    records = [{"example_id": f"example-{index}"} for index in range(7)]
    shards = shard_records(records, 3)
    flattened = [record["example_id"] for shard in shards for record in shard]
    assert sorted(flattened) == sorted(record["example_id"] for record in records)
    assert [len(shard) for shard in shards] == [3, 2, 2]


def test_interleaves_publications_before_worker_limits_apply():
    records = [
        {"example_id": "a1", "publication_number": "A"},
        {"example_id": "a2", "publication_number": "A"},
        {"example_id": "b1", "publication_number": "B"},
        {"example_id": "c1", "publication_number": "C"},
    ]
    assert [row["example_id"] for row in interleave_publications(records)] == [
        "a1", "b1", "c1", "a2"
    ]

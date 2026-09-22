import json
import os
import tempfile

from bot_app import outbox


def make_version(outbox_dir: str, version: str, characters):
    vdir = os.path.join(outbox_dir, version)
    os.makedirs(vdir, exist_ok=True)
    manifest = {
        "version": version,
        "generated_at": "2026-09-20T12:00:00+08:00",
        "characters": characters,
    }
    with open(os.path.join(vdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    for c in characters:
        open(os.path.join(vdir, c["file_name"]), "w").close()
    return vdir


def test_list_version_batches():
    with tempfile.TemporaryDirectory() as outbox_dir:
        vdir = make_version(
            outbox_dir,
            "20260920120000",
            [{"id": "1", "name": "Alice", "file_name": "1_Alice_角色档案_长图.png"}],
        )
        batches = outbox.list_version_batches(outbox_dir)
        assert len(batches) == 1
        batch = batches[0]
        assert batch.version == "20260920120000"
        assert batch.version_dir == vdir
        assert len(batch.images) == 1
        assert batch.images[0].name == "Alice"
        assert batch.images[0].version_dir == vdir


def test_skip_missing_manifest():
    with tempfile.TemporaryDirectory() as outbox_dir:
        os.makedirs(os.path.join(outbox_dir, "noversion"))
        open(os.path.join(outbox_dir, "file.txt"), "w").close()
        assert outbox.list_version_batches(outbox_dir) == []


def test_done_marking():
    with tempfile.TemporaryDirectory() as vdir:
        assert not outbox.is_image_done(vdir, "foo.png")
        outbox.mark_image_done(vdir, "foo.png")
        assert outbox.is_image_done(vdir, "foo.png")


def test_batch_complete():
    with tempfile.TemporaryDirectory() as vdir:
        batch = outbox.VersionBatch(
            version="v1",
            version_dir=vdir,
            generated_at="",
            images=[
                outbox.CharacterImage("1", "A", "a.png", os.path.join(vdir, "a.png"), vdir),
                outbox.CharacterImage("2", "B", "b.png", os.path.join(vdir, "b.png"), vdir),
            ],
        )
        assert not outbox.is_batch_complete(batch)
        outbox.mark_image_done(vdir, "a.png")
        assert not outbox.is_batch_complete(batch)
        outbox.mark_image_done(vdir, "b.png")
        assert outbox.is_batch_complete(batch)


def test_sent_record_store():
    with tempfile.TemporaryDirectory() as data_dir:
        store = outbox.SentRecordStore(data_dir)
        store.mark_sent_to_group("v1", "foo.png", "g1")
        assert store.is_sent_to_group("v1", "foo.png", "g1")
        assert not store.is_sent_to_group("v1", "foo.png", "g2")
        store.save_manifest("v1", {"version": "v1"})
        assert os.path.exists(os.path.join(data_dir, "sent", "v1", "manifest.json"))
        assert os.path.exists(os.path.join(data_dir, "sent", "v1", "status.json"))

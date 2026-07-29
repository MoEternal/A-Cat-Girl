import json
from pathlib import Path

import pytest

from catgirl.plugins import FileMemoryStore


def test_legacy_state_migration_rejects_an_empty_conversation_id(tmp_path: Path) -> None:
    store = FileMemoryStore(
        tmp_path / "memory_plugin",
        default_factory=dict,
        normalize=lambda value: dict(value) if isinstance(value, dict) else {},
    )

    with pytest.raises(ValueError, match="聊天记录 ID 为空"):
        store.migrate_conversation_state("  ", {"turn": 3})

    assert not store.root.exists()


def test_reconcile_archives_empty_orphans_but_keeps_nonempty_memory(tmp_path: Path) -> None:
    store = FileMemoryStore(
        tmp_path / "memory_plugin",
        default_factory=dict,
        normalize=lambda value: dict(value) if isinstance(value, dict) else {},
    )
    rich_id, _ = store.create_and_bind(
        "record-live",
        name="旧名称 的记忆",
        state={"summary": "需要保留"},
        automatic_name=True,
    )
    store.bind("record-deleted-rich", rich_id)
    empty_bound_id, _ = store.create_and_bind("record-deleted-empty", name="空记忆")
    empty_orphan_id, _ = store.create_and_bind("record-temporary", name="另一个空记忆")
    store.unbind("record-temporary")

    result = store.reconcile(
        {"record-live"},
        delete_if_unbound=lambda state: not state,
    )

    overview = store.overview()
    assert overview["bindings"] == {"record-live": rich_id}
    assert [item["id"] for item in overview["memories"]] == [rich_id]
    assert set(result["removed_bindings"]) == {
        "record-deleted-rich",
        "record-deleted-empty",
    }
    assert set(result["removed_memories"]) == {empty_bound_id, empty_orphan_id}
    assert len(list((store.root / "deleted_memories").glob("*.json"))) == 2


def test_delete_bound_memory_creates_replacement_and_preserves_manual_name(tmp_path: Path) -> None:
    store = FileMemoryStore(
        tmp_path / "memory_plugin",
        default_factory=dict,
        normalize=lambda value: dict(value) if isinstance(value, dict) else {},
    )
    memory_id, _ = store.ensure_bound(
        "record-live",
        name="旧标题 的记忆",
        automatic_name=True,
    )
    assert store.sync_bound_name(
        "record-live",
        "新标题 的记忆",
        legacy_suffixes=(" 的记忆",),
    )
    store.rename(memory_id, "我手动起的名字")
    assert not store.sync_bound_name(
        "record-live",
        "再次改名 的记忆",
        legacy_suffixes=(" 的记忆",),
    )

    replacement_id = store.delete_memory(
        memory_id,
        conversation_id="record-live",
        replacement_name="再次改名 的记忆",
        replacement_automatic_name=True,
    )

    assert replacement_id and replacement_id != memory_id
    overview = store.overview()
    assert overview["bindings"]["record-live"] == replacement_id
    assert [item["id"] for item in overview["memories"]] == [replacement_id]
    assert overview["memories"][0]["name"] == "再次改名 的记忆"
    assert len(list((store.root / "deleted_memories").glob("*.json"))) == 1


def test_delete_shared_memory_is_rejected(tmp_path: Path) -> None:
    store = FileMemoryStore(
        tmp_path / "memory_plugin",
        default_factory=dict,
        normalize=lambda value: dict(value) if isinstance(value, dict) else {},
    )
    memory_id, _ = store.ensure_bound("record-a", name="共享记忆")
    store.bind("record-b", memory_id)

    with pytest.raises(ValueError, match="多个聊天记录共用"):
        store.delete_memory(memory_id, conversation_id="record-a", replacement_name="新记忆")

    assert store.overview()["bindings"] == {
        "record-a": memory_id,
        "record-b": memory_id,
    }


def test_overview_revision_tracks_direct_memory_file_changes(tmp_path: Path) -> None:
    store = FileMemoryStore(
        tmp_path / "memory_plugin",
        default_factory=dict,
        normalize=lambda value: dict(value) if isinstance(value, dict) else {},
    )
    memory_id, _ = store.ensure_bound("record-a", name="可编辑记忆")
    before = store.overview()["memories"][0]["file_revision"]

    store.write(memory_id, {"summary": "手动修改后的内容更长"})

    after = store.overview()["memories"][0]["file_revision"]
    assert after != before


def test_direct_json_edit_is_read_without_restarting_the_store(tmp_path: Path) -> None:
    store = FileMemoryStore(
        tmp_path / "memory_plugin",
        default_factory=dict,
        normalize=lambda value: dict(value) if isinstance(value, dict) else {},
    )
    memory_id, _ = store.ensure_bound("record-a", name="可编辑记忆")
    path = store.root / "memories" / f"{memory_id}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["state"] = {"summary": "文件外部手动修改"}
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert store.read_for_conversation("record-a")[1] == {"summary": "文件外部手动修改"}


def test_snapshot_restore_reverts_its_own_unchanged_write(tmp_path: Path) -> None:
    store = FileMemoryStore(
        tmp_path / "memory_plugin",
        default_factory=dict,
        normalize=lambda value: dict(value) if isinstance(value, dict) else {},
    )
    store.create_and_bind("record-a", name="撤回测试", state={"turn": 0})
    store.capture_snapshot("record-a", "turn-0001")
    store.write_for_conversation(
        "record-a",
        {"turn": 1},
        snapshot_turn_id="turn-0001",
    )

    assert store.restore_snapshot("record-a", "turn-0001") is True
    assert store.read_for_conversation("record-a")[1] == {"turn": 0}


def test_snapshot_restore_preserves_a_later_shared_memory_write(tmp_path: Path) -> None:
    store = FileMemoryStore(
        tmp_path / "memory_plugin",
        default_factory=dict,
        normalize=lambda value: dict(value) if isinstance(value, dict) else {},
    )
    memory_id, _ = store.create_and_bind("record-a", name="共享记忆", state={"events": []})
    store.bind("record-b", memory_id)
    store.capture_snapshot("record-a", "turn-0001")
    store.write_for_conversation(
        "record-a",
        {"events": ["甲"]},
        snapshot_turn_id="turn-0001",
    )
    store.write_for_conversation("record-b", {"events": ["甲", "乙"]})

    assert store.restore_snapshot("record-a", "turn-0001") is False
    assert store.read(memory_id) == {"events": ["甲", "乙"]}
    conflicts = list((store.root / "rollback_conflicts").glob("*.json"))
    assert len(conflicts) == 1


def test_snapshot_restore_preserves_a_manual_memory_rename(tmp_path: Path) -> None:
    store = FileMemoryStore(
        tmp_path / "memory_plugin",
        default_factory=dict,
        normalize=lambda value: dict(value) if isinstance(value, dict) else {},
    )
    memory_id, _ = store.create_and_bind("record-a", name="旧名称", state={"turn": 0})
    store.capture_snapshot("record-a", "turn-0001")
    store.write_for_conversation(
        "record-a",
        {"turn": 1},
        snapshot_turn_id="turn-0001",
    )
    store.rename(memory_id, "用户手动名称")

    assert store.restore_snapshot("record-a", "turn-0001") is False
    assert store.read(memory_id) == {"turn": 1}
    assert store.overview()["memories"][0]["name"] == "用户手动名称"

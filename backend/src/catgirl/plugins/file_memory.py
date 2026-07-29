from __future__ import annotations

import hashlib
import json
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


INDEX_SCHEMA_VERSION = 1
MEMORY_SCHEMA_VERSION = 1
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _safe_name(value: Any, fallback: str) -> str:
    text = re.sub(r"[\x00-\x1f]+", " ", str(value or "")).strip()
    return text[:160] or fallback


class FileMemoryStore:
    """Readable, file-backed memory records with explicit conversation bindings."""

    def __init__(
        self,
        plugin_path: Path,
        *,
        default_factory: Callable[[], dict[str, Any]],
        normalize: Callable[[Any], dict[str, Any]],
    ) -> None:
        self.root = Path(plugin_path).resolve() / "memory_records"
        self.index_path = self.root / "bindings.json"
        self.default_factory = default_factory
        self.normalize = normalize
        self._lock = _lock_for(self.root)

    def _empty_index(self) -> dict[str, Any]:
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "bindings": {},
            "memories": {},
            "updated_at": "",
        }

    def _read_json(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"记忆文件损坏：{path.name}：{exc}") from exc

    def _write_json(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return self._empty_index()
        raw = self._read_json(self.index_path)
        source = raw if isinstance(raw, dict) else {}
        bindings = source.get("bindings") if isinstance(source.get("bindings"), dict) else {}
        memories = source.get("memories") if isinstance(source.get("memories"), dict) else {}
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "bindings": {
                str(conversation_id): str(memory_id)
                for conversation_id, memory_id in bindings.items()
                if str(conversation_id).strip() and str(memory_id).strip()
            },
            "memories": {
                str(memory_id): deepcopy(metadata)
                for memory_id, metadata in memories.items()
                if str(memory_id).strip() and isinstance(metadata, dict)
            },
            "updated_at": str(source.get("updated_at") or ""),
        }

    def _write_index(self, index: dict[str, Any]) -> None:
        index["schema_version"] = INDEX_SCHEMA_VERSION
        index["updated_at"] = _now()
        self._write_json(self.index_path, index)

    def _memory_path(self, memory_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", memory_id):
            raise ValueError("记忆 ID 无效")
        return self.root / "memories" / f"{memory_id}.json"

    def _snapshot_path(self, turn_id: str) -> Path:
        normalized = str(turn_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,120}", normalized):
            raise ValueError("回合 ID 无效")
        return self.root / "turn_snapshots" / f"{normalized}.json"

    def _new_memory_locked(
        self,
        index: dict[str, Any],
        name: str,
        state: dict[str, Any] | None = None,
        *,
        automatic_name: bool = False,
    ) -> str:
        memory_id = uuid4().hex
        now = _now()
        normalized = self.normalize(state if isinstance(state, dict) else self.default_factory())
        self._write_json(
            self._memory_path(memory_id),
            {
                "schema_version": MEMORY_SCHEMA_VERSION,
                "memory_id": memory_id,
                "updated_at": now,
                "state": normalized,
            },
        )
        index["memories"][memory_id] = {
            "id": memory_id,
            "name": _safe_name(name, "未命名记忆"),
            "automatic_name": bool(automatic_name),
            "created_at": now,
            "updated_at": now,
        }
        return memory_id

    def _archive_memory_locked(self, index: dict[str, Any], memory_id: str) -> str:
        path = self._memory_path(memory_id)
        metadata = deepcopy(index["memories"].get(memory_id) or {})
        document = self._read_json(path) if path.is_file() else None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive_name = f"{stamp}-{memory_id}.json"
        self._write_json(
            self.root / "deleted_memories" / archive_name,
            {
                "schema_version": 1,
                "deleted_at": _now(),
                "metadata": metadata,
                "document": document,
            },
        )
        index["memories"].pop(memory_id, None)
        path.unlink(missing_ok=True)
        return archive_name

    def _read_memory_locked(self, memory_id: str) -> dict[str, Any]:
        path = self._memory_path(memory_id)
        if not path.is_file():
            raise ValueError("绑定的记忆文件不存在")
        raw = self._read_json(path)
        source = raw if isinstance(raw, dict) else {}
        return self.normalize(source.get("state"))

    def _memory_revision_locked(self, memory_id: str) -> str:
        path = self._memory_path(memory_id)
        if not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _mark_snapshot_write_locked(
        self,
        conversation_id: str,
        turn_id: str | None,
        memory_id: str,
    ) -> None:
        normalized_turn_id = str(turn_id or "").strip()
        if not normalized_turn_id:
            return
        snapshot_path = self._snapshot_path(normalized_turn_id)
        if not snapshot_path.is_file():
            return
        raw = self._read_json(snapshot_path)
        snapshot = raw if isinstance(raw, dict) else {}
        if str(snapshot.get("conversation_id") or "") != str(conversation_id or "").strip():
            raise ValueError("回合快照与记忆写入的聊天记录不匹配")
        snapshot["post_memory_id"] = memory_id
        snapshot["post_revision"] = self._memory_revision_locked(memory_id)
        index = self._read_index()
        snapshot["post_metadata"] = deepcopy(index["memories"].get(memory_id, {}))
        snapshot["written_at"] = _now()
        self._write_json(snapshot_path, snapshot)

    def ensure_bound(
        self,
        conversation_id: str,
        *,
        name: str = "",
        automatic_name: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            raise ValueError("聊天记录 ID 为空")
        with self._lock:
            index = self._read_index()
            memory_id = str(index["bindings"].get(conversation_id) or "")
            if not memory_id or memory_id not in index["memories"] or not self._memory_path(memory_id).is_file():
                memory_id = self._new_memory_locked(
                    index,
                    name or "独立记忆",
                    automatic_name=automatic_name,
                )
                index["bindings"][conversation_id] = memory_id
                self._write_index(index)
            return memory_id, self._read_memory_locked(memory_id)

    def create_and_bind(
        self,
        conversation_id: str,
        *,
        name: str,
        state: dict[str, Any] | None = None,
        automatic_name: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            raise ValueError("聊天记录 ID 为空")
        with self._lock:
            index = self._read_index()
            memory_id = self._new_memory_locked(
                index,
                name,
                state,
                automatic_name=automatic_name,
            )
            index["bindings"][conversation_id] = memory_id
            self._write_index(index)
            return memory_id, self._read_memory_locked(memory_id)

    def bind(self, conversation_id: str, memory_id: str) -> dict[str, Any]:
        conversation_id = str(conversation_id or "").strip()
        memory_id = str(memory_id or "").strip()
        if not conversation_id:
            raise ValueError("聊天记录 ID 为空")
        with self._lock:
            index = self._read_index()
            if memory_id not in index["memories"] or not self._memory_path(memory_id).is_file():
                raise ValueError("要绑定的记忆不存在")
            index["bindings"][conversation_id] = memory_id
            self._write_index(index)
            return self._read_memory_locked(memory_id)

    def bound_memory_id(self, conversation_id: str) -> str | None:
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            return None
        with self._lock:
            index = self._read_index()
            memory_id = str(index["bindings"].get(conversation_id) or "")
            if memory_id in index["memories"] and self._memory_path(memory_id).is_file():
                return memory_id
            return None

    def read(self, memory_id: str) -> dict[str, Any]:
        with self._lock:
            index = self._read_index()
            if memory_id not in index["memories"]:
                raise ValueError("记忆不存在")
            return self._read_memory_locked(memory_id)

    def read_for_conversation(
        self,
        conversation_id: str,
        *,
        name: str = "",
        automatic_name: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        return self.ensure_bound(
            conversation_id,
            name=name,
            automatic_name=automatic_name,
        )

    def write_for_conversation(
        self,
        conversation_id: str,
        state: dict[str, Any],
        *,
        name: str = "",
        automatic_name: bool = False,
        snapshot_turn_id: str | None = None,
    ) -> str:
        with self._lock:
            memory_id, _ = self.ensure_bound(
                conversation_id,
                name=name,
                automatic_name=automatic_name,
            )
            normalized = self.normalize(state)
            now = _now()
            self._write_json(
                self._memory_path(memory_id),
                {
                    "schema_version": MEMORY_SCHEMA_VERSION,
                    "memory_id": memory_id,
                    "updated_at": now,
                    "state": normalized,
                },
            )
            index = self._read_index()
            if memory_id in index["memories"]:
                index["memories"][memory_id]["updated_at"] = now
                self._write_index(index)
            self._mark_snapshot_write_locked(
                conversation_id,
                snapshot_turn_id,
                memory_id,
            )
            return memory_id

    def write(
        self,
        memory_id: str,
        state: dict[str, Any],
        *,
        snapshot_conversation_id: str = "",
        snapshot_turn_id: str | None = None,
    ) -> None:
        with self._lock:
            index = self._read_index()
            if memory_id not in index["memories"]:
                raise ValueError("记忆不存在")
            normalized = self.normalize(state)
            now = _now()
            self._write_json(
                self._memory_path(memory_id),
                {
                    "schema_version": MEMORY_SCHEMA_VERSION,
                    "memory_id": memory_id,
                    "updated_at": now,
                    "state": normalized,
                },
            )
            index["memories"][memory_id]["updated_at"] = now
            self._write_index(index)
            self._mark_snapshot_write_locked(
                snapshot_conversation_id,
                snapshot_turn_id,
                memory_id,
            )

    def reset_conversation(
        self,
        conversation_id: str,
        *,
        name: str = "",
        automatic_name: bool = False,
        snapshot_turn_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            raise ValueError("聊天记录 ID 为空")
        with self._lock:
            index = self._read_index()
            memory_id = self._new_memory_locked(
                index,
                name or "新记忆",
                automatic_name=automatic_name,
            )
            index["bindings"][conversation_id] = memory_id
            self._write_index(index)
            self._mark_snapshot_write_locked(
                conversation_id,
                snapshot_turn_id,
                memory_id,
            )
            return memory_id, self._read_memory_locked(memory_id)

    def rename(self, memory_id: str, name: str) -> None:
        with self._lock:
            index = self._read_index()
            if memory_id not in index["memories"]:
                raise ValueError("记忆不存在")
            index["memories"][memory_id]["name"] = _safe_name(name, "未命名记忆")
            index["memories"][memory_id]["automatic_name"] = False
            index["memories"][memory_id]["updated_at"] = _now()
            self._write_index(index)

    def sync_bound_name(
        self,
        conversation_id: str,
        name: str,
        *,
        legacy_suffixes: tuple[str, ...] = (),
    ) -> bool:
        """Refresh generated names without overwriting a name edited by the user."""
        conversation_id = str(conversation_id or "").strip()
        with self._lock:
            index = self._read_index()
            memory_id = str(index["bindings"].get(conversation_id) or "")
            metadata = index["memories"].get(memory_id)
            if not isinstance(metadata, dict):
                return False
            bound_count = sum(1 for value in index["bindings"].values() if value == memory_id)
            current_name = str(metadata.get("name") or "")
            looks_generated = bool(metadata.get("automatic_name")) or any(
                current_name.endswith(suffix) for suffix in legacy_suffixes
            )
            next_name = _safe_name(name, "未命名记忆")
            if bound_count != 1 or not looks_generated:
                return False
            if current_name == next_name and metadata.get("automatic_name") is True:
                return False
            metadata["name"] = next_name
            metadata["automatic_name"] = True
            metadata["updated_at"] = _now()
            self._write_index(index)
            return True

    def unbind(self, conversation_id: str) -> str | None:
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            return None
        with self._lock:
            index = self._read_index()
            memory_id = index["bindings"].pop(conversation_id, None)
            if memory_id is not None:
                self._write_index(index)
            return str(memory_id) if memory_id is not None else None

    def delete_memory(
        self,
        memory_id: str,
        *,
        conversation_id: str | None = None,
        replacement_name: str = "",
        replacement_automatic_name: bool = False,
    ) -> str | None:
        """Archive and remove a memory, replacing its sole binding when requested."""
        memory_id = str(memory_id or "").strip()
        with self._lock:
            index = self._read_index()
            if memory_id not in index["memories"] or not self._memory_path(memory_id).is_file():
                raise ValueError("记忆不存在")
            bound_ids = [
                bound_conversation_id
                for bound_conversation_id, bound_memory_id in index["bindings"].items()
                if bound_memory_id == memory_id
            ]
            if len(bound_ids) > 1:
                raise ValueError("这份记忆仍被多个聊天记录共用，请先解除其他绑定")

            replacement_id: str | None = None
            if bound_ids:
                requested_id = str(conversation_id or "").strip()
                if requested_id != bound_ids[0]:
                    raise ValueError("这份记忆仍绑定在其他聊天记录上")
                replacement_id = self._new_memory_locked(
                    index,
                    replacement_name or "新记忆",
                    automatic_name=replacement_automatic_name,
                )
                index["bindings"][requested_id] = replacement_id

            self._archive_memory_locked(index, memory_id)
            self._write_index(index)
            return replacement_id

    def reconcile(
        self,
        conversation_ids: set[str] | list[str] | tuple[str, ...],
        *,
        delete_if_unbound: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, list[str]]:
        """Drop stale bindings and optionally archive empty orphan memories."""
        valid_ids = {str(item).strip() for item in conversation_ids if str(item).strip()}
        with self._lock:
            index = self._read_index()
            removed_bindings: list[str] = []
            removed_memories: list[str] = []
            changed = False

            for conversation_id, memory_id in list(index["bindings"].items()):
                if (
                    conversation_id not in valid_ids
                    or memory_id not in index["memories"]
                    or not self._memory_path(memory_id).is_file()
                ):
                    index["bindings"].pop(conversation_id, None)
                    removed_bindings.append(conversation_id)
                    changed = True

            bound_memory_ids = set(index["bindings"].values())
            for memory_id in list(index["memories"]):
                path = self._memory_path(memory_id)
                if not path.is_file():
                    index["memories"].pop(memory_id, None)
                    removed_memories.append(memory_id)
                    changed = True
                    continue
                if memory_id in bound_memory_ids or delete_if_unbound is None:
                    continue
                state = self._read_memory_locked(memory_id)
                if delete_if_unbound(state):
                    self._archive_memory_locked(index, memory_id)
                    removed_memories.append(memory_id)
                    changed = True

            if changed:
                self._write_index(index)
            return {
                "removed_bindings": removed_bindings,
                "removed_memories": removed_memories,
            }

    def overview(self) -> dict[str, Any]:
        with self._lock:
            index = self._read_index()
            bound_counts: dict[str, int] = {}
            for memory_id in index["bindings"].values():
                bound_counts[memory_id] = bound_counts.get(memory_id, 0) + 1
            memories = []
            for memory_id, metadata in index["memories"].items():
                path = self._memory_path(memory_id)
                if not path.is_file():
                    continue
                stat = path.stat()
                memories.append(
                    {
                        **deepcopy(metadata),
                        "bound_count": bound_counts.get(memory_id, 0),
                        "file_revision": f"{stat.st_mtime_ns}:{stat.st_size}",
                    }
                )
            memories.sort(key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("id") or "")))
            return {"bindings": deepcopy(index["bindings"]), "memories": memories}

    def capture_snapshot(self, conversation_id: str, turn_id: str) -> None:
        """Persist rollback data beside the memories instead of in the application database."""
        conversation_id = str(conversation_id or "").strip()
        with self._lock:
            index = self._read_index()
            memory_id = str(index["bindings"].get(conversation_id) or "")
            snapshot: dict[str, Any] = {
                "schema_version": 1,
                "conversation_id": conversation_id,
                "captured_at": _now(),
                "bound": False,
            }
            if memory_id in index["memories"] and self._memory_path(memory_id).is_file():
                snapshot.update(
                    {
                        "bound": True,
                        "memory_id": memory_id,
                        "metadata": deepcopy(index["memories"][memory_id]),
                        "state": self._read_memory_locked(memory_id),
                    }
                )
            path = self._snapshot_path(turn_id)
            self._write_json(path, snapshot)
            snapshots = sorted(
                path.parent.glob("*.json"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for stale in snapshots[500:]:
                stale.unlink(missing_ok=True)

    def restore_snapshot(self, conversation_id: str, turn_id: str) -> bool:
        conversation_id = str(conversation_id or "").strip()
        path = self._snapshot_path(turn_id)
        with self._lock:
            if not path.is_file():
                return False
            raw = self._read_json(path)
            snapshot = raw if isinstance(raw, dict) else {}
            if str(snapshot.get("conversation_id") or "") != conversation_id:
                raise ValueError("回合快照与聊天记录不匹配")
            index = self._read_index()
            current_id = str(index["bindings"].get(conversation_id) or "")
            post_memory_id = str(snapshot.get("post_memory_id") or "")
            post_revision = str(snapshot.get("post_revision") or "")
            if not post_memory_id or not post_revision:
                path.unlink(missing_ok=True)
                return True
            current_revision = self._memory_revision_locked(current_id)
            post_metadata = (
                snapshot.get("post_metadata")
                if isinstance(snapshot.get("post_metadata"), dict)
                else {}
            )
            current_metadata = index["memories"].get(current_id, {})
            if (
                current_id != post_memory_id
                or current_revision != post_revision
                or current_metadata != post_metadata
            ):
                conflict = {
                    **snapshot,
                    "conflict_detected_at": _now(),
                    "current_memory_id": current_id,
                    "current_revision": current_revision,
                }
                conflict_path = self.root / "rollback_conflicts" / path.name
                self._write_json(conflict_path, conflict)
                path.unlink(missing_ok=True)
                return False
            if snapshot.get("bound") is True:
                memory_id = str(snapshot.get("memory_id") or "")
                metadata = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {}
                if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", memory_id):
                    raise ValueError("回合快照中的记忆 ID 无效")
                now = _now()
                restored = self.normalize(snapshot.get("state"))
                self._write_json(
                    self._memory_path(memory_id),
                    {
                        "schema_version": MEMORY_SCHEMA_VERSION,
                        "memory_id": memory_id,
                        "updated_at": now,
                        "state": restored,
                    },
                )
                index["memories"][memory_id] = {
                    "id": memory_id,
                    "name": _safe_name(metadata.get("name"), "已恢复记忆"),
                    "automatic_name": bool(metadata.get("automatic_name", False)),
                    "created_at": str(metadata.get("created_at") or now),
                    "updated_at": now,
                }
                index["bindings"][conversation_id] = memory_id
            else:
                index["bindings"].pop(conversation_id, None)

            if current_id and current_id != index["bindings"].get(conversation_id):
                still_bound = current_id in index["bindings"].values()
                if not still_bound:
                    index["memories"].pop(current_id, None)
                    self._memory_path(current_id).unlink(missing_ok=True)
            self._write_index(index)
            path.unlink(missing_ok=True)
            return True

    def migrate_conversation_state(
        self,
        conversation_id: str,
        state: dict[str, Any],
        *,
        name: str = "",
    ) -> tuple[str, dict[str, Any]]:
        """Move a legacy database-backed state into a new file only when no binding exists."""
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            raise ValueError("聊天记录 ID 为空")
        with self._lock:
            index = self._read_index()
            existing = str(index["bindings"].get(conversation_id) or "")
            if existing and existing in index["memories"] and self._memory_path(existing).is_file():
                return existing, self._read_memory_locked(existing)
            memory_id = self._new_memory_locked(
                index,
                name or "迁移记忆",
                state,
                automatic_name=True,
            )
            index["bindings"][conversation_id] = memory_id
            self._write_index(index)
            return memory_id, self._read_memory_locked(memory_id)

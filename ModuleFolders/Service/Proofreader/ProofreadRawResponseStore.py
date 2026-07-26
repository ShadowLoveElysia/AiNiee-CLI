"""Persistent raw LLM response log for AI proofread suggestion runs."""

from __future__ import annotations

import errno
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator


_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _get_path_lock(path: Path) -> threading.RLock:
    """Return the process-wide lock shared by stores targeting the same file."""
    normalized_path = os.path.normcase(os.fspath(path.resolve(strict=False)))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(normalized_path, threading.RLock())


def _lock_windows_file(lock_file: BinaryIO) -> None:
    import msvcrt

    while True:
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN) and getattr(
                error, "winerror", None
            ) not in (33, 36):
                raise
            time.sleep(0.05)


@contextmanager
def _interprocess_path_lock(path: Path) -> Iterator[None]:
    """Serialize migration and appends across processes on Windows and POSIX."""
    canonical_path = path.resolve(strict=False)
    lock_path = canonical_path.with_name(f".{canonical_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()

        if os.name == "nt":
            import msvcrt

            _lock_windows_file(lock_file)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class ProofreadRawResponseStore:
    VERSION = 2
    FILENAME = "AIProofreadLLMResponses.jsonl"
    LEGACY_FILENAME = "AIProofreadLLMResponses.json"

    def __init__(self, root_path: str | Path) -> None:
        root = Path(root_path)
        output_dir = root.parent if root.name == "cache" else root
        proofread_dir = output_dir / "proofread"
        self.path = proofread_dir / self.FILENAME
        self.legacy_path = proofread_dir / self.LEGACY_FILENAME
        self._lock = _get_path_lock(self.path)

    def append(self, entry: dict[str, Any]) -> None:
        normalized = dict(entry)
        normalized.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S%z"))

        # Serialize before touching the log so invalid entries cannot cause partial writes.
        serialized = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        encoded_line = f"{serialized}\n".encode("utf-8")

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _interprocess_path_lock(self.path):
                self._migrate_legacy_log()
                self._recover_incomplete_tail()
                with self.path.open("ab") as writer:
                    written = writer.write(encoded_line)
                    if written != len(encoded_line):
                        raise OSError(
                            f"Incomplete proofread response write: {self.path}"
                        )
                    writer.flush()

    def read_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.parent.exists():
                return []
            with _interprocess_path_lock(self.path):
                if not self.path.is_file():
                    if self.legacy_path.is_file():
                        return self._read_legacy_entries()
                    return []
                self._recover_incomplete_tail()
                return self._read_jsonl_entries()

    def _read_jsonl_entries(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []

        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as reader:
            for line_number, line in enumerate(reader, start=1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"Invalid JSONL entry at line {line_number} in {self.path}"
                    ) from error
                if not isinstance(entry, dict):
                    raise ValueError(
                        f"Expected a JSON object at line {line_number} in {self.path}"
                    )
                entries.append(entry)
        return entries

    def _migrate_legacy_log(self) -> None:
        if self.path.exists():
            if self.legacy_path.is_file():
                os.replace(self.legacy_path, self._next_legacy_backup_path())
            return
        if not self.legacy_path.is_file():
            return

        entries = self._read_legacy_entries()
        tmp_path = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        try:
            with tmp_path.open("xb") as writer:
                for entry in entries:
                    line = json.dumps(
                        entry,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    writer.write(f"{line}\n".encode("utf-8"))
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(tmp_path, self.path)
        finally:
            tmp_path.unlink(missing_ok=True)

        os.replace(self.legacy_path, self._next_legacy_backup_path())

    def _recover_incomplete_tail(self) -> None:
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return

        with self.path.open("rb") as reader:
            reader.seek(-1, os.SEEK_END)
            if reader.read(1) == b"\n":
                return

        tail_offset = self._last_complete_line_offset()
        with self.path.open("rb") as reader:
            reader.seek(tail_offset)
            tail = reader.read()
        try:
            tail_entry = json.loads(tail.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            tail_entry = None
        if isinstance(tail_entry, dict):
            with self.path.open("ab") as writer:
                writer.write(b"\n")
                writer.flush()
            return

        recovery_path = self._next_recovery_path()
        try:
            with self.path.open("rb") as reader, recovery_path.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
        except Exception:
            recovery_path.unlink(missing_ok=True)
            raise

        with self.path.open("r+b") as writer:
            writer.truncate(tail_offset)
            writer.flush()
            os.fsync(writer.fileno())

    def _last_complete_line_offset(self) -> int:
        block_size = 64 * 1024
        with self.path.open("rb") as reader:
            position = reader.seek(0, os.SEEK_END)
            while position > 0:
                chunk_size = min(block_size, position)
                position -= chunk_size
                reader.seek(position)
                chunk = reader.read(chunk_size)
                newline_index = chunk.rfind(b"\n")
                if newline_index >= 0:
                    return position + newline_index + 1
        return 0

    def _read_legacy_entries(self) -> list[dict[str, Any]]:
        try:
            with self.legacy_path.open("r", encoding="utf-8") as reader:
                payload = json.load(reader)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Unable to read legacy proofread log: {self.legacy_path}") from error

        if isinstance(payload, dict):
            entries = payload.get("entries")
        elif isinstance(payload, list):
            entries = payload
        else:
            entries = None
        if not isinstance(entries, list) or not all(
            isinstance(entry, dict) for entry in entries
        ):
            raise ValueError(
                f"Legacy proofread log must contain a list of JSON objects: {self.legacy_path}"
            )
        return entries

    def _next_legacy_backup_path(self) -> Path:
        candidate = self.legacy_path.with_name("AIProofreadLLMResponses.legacy.json")
        suffix = 1
        while candidate.exists():
            candidate = self.legacy_path.with_name(
                f"AIProofreadLLMResponses.legacy.{suffix}.json"
            )
            suffix += 1
        return candidate

    def _next_recovery_path(self) -> Path:
        candidate = self.path.with_name("AIProofreadLLMResponses.recovery.jsonl")
        suffix = 1
        while candidate.exists():
            candidate = self.path.with_name(
                f"AIProofreadLLMResponses.recovery.{suffix}.jsonl"
            )
            suffix += 1
        return candidate

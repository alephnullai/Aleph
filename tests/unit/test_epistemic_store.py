"""Unit tests for the shared atomic EpistemicStore.

Covers corruption recovery (.bak fallback, no silent reset), atomic saves
(no partial file on simulated failure), and concurrent transactions
(no lost updates).
"""

from __future__ import annotations

import glob
import json
import os
import threading

import pytest

from aleph.epistemic.store import EpistemicStore


@pytest.fixture
def store(tmp_path):
    return EpistemicStore(str(tmp_path / "project.aleph.epistemic"))


class TestLoadSave:
    def test_load_missing_returns_empty(self, store):
        assert store.load() == {}

    def test_save_and_load_roundtrip(self, store):
        data = {"inferences": [{"symbol_id": "f_abc", "conclusion": "x"}]}
        store.save(data)
        assert store.load() == data

    def test_save_creates_parent_dirs(self, tmp_path):
        store = EpistemicStore(str(tmp_path / "nested" / "dir" / "epistemic.json"))
        store.save({"flags": []})
        assert store.load() == {"flags": []}

    def test_save_backs_up_previous_good_file(self, store):
        store.save({"version": 1})
        store.save({"version": 2})
        assert os.path.isfile(store.bak_path)
        with open(store.bak_path, "r", encoding="utf-8") as f:
            assert json.load(f) == {"version": 1}
        assert store.load() == {"version": 2}


class TestCorruptionRecovery:
    def test_corrupt_file_recovers_from_bak(self, store, capsys):
        # Two saves: first one becomes the .bak
        store.save({"inferences": [{"symbol_id": "f_a", "conclusion": "keep me"}]})
        store.save({"inferences": [{"symbol_id": "f_a", "conclusion": "keep me"}]})
        # Corrupt the live file (e.g. truncated write from a crash)
        with open(store.path, "w", encoding="utf-8") as f:
            f.write('{"inferences": [{"symbol_')

        data = store.load()

        # Data recovered from backup — NOT silently reset to {}
        assert data["inferences"][0]["conclusion"] == "keep me"
        # Corrupt file preserved for forensics
        assert glob.glob(store.path + ".corrupt.*")
        # Warned on stderr
        assert "WARNING" in capsys.readouterr().err

    def test_corrupt_file_without_bak_warns_and_returns_empty(self, store, capsys):
        with open(store.path, "w", encoding="utf-8") as f:
            f.write("not json{{{")
        data = store.load()
        assert data == {}
        assert glob.glob(store.path + ".corrupt.*")
        assert "WARNING" in capsys.readouterr().err

    def test_corrupt_file_is_not_copied_to_bak_on_save(self, store):
        """Saving over a corrupt file must not clobber a good .bak."""
        store.save({"good": True})
        store.save({"good": True})  # .bak now holds good data
        with open(store.path, "w", encoding="utf-8") as f:
            f.write("garbage")
        store.save({"new": True})
        with open(store.bak_path, "r", encoding="utf-8") as f:
            assert json.load(f) == {"good": True}
        assert store.load() == {"new": True}


class TestAtomicity:
    def test_failed_replace_leaves_original_intact(self, store, monkeypatch):
        """Simulated crash between temp-file write and replace: no partial file."""
        store.save({"state": "original"})

        real_replace = os.replace

        def failing_replace(src, dst):
            if dst == store.path:
                raise OSError("simulated crash")
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", failing_replace)
        with pytest.raises(OSError):
            store.save({"state": "partial"})
        monkeypatch.undo()

        # Original file untouched and still valid JSON
        assert store.load() == {"state": "original"}
        # No temp file left behind
        assert not glob.glob(store.path + ".tmp.*")


class TestConcurrency:
    def test_concurrent_transactions_no_lost_update(self, store):
        """Two threads appending under transaction() must not lose entries."""
        per_thread = 25

        def worker(tag: str):
            for i in range(per_thread):
                with store.transaction() as data:
                    data.setdefault("entries", []).append(f"{tag}-{i}")

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        entries = store.load()["entries"]
        assert len(entries) == 2 * per_thread
        assert sum(1 for e in entries if e.startswith("a-")) == per_thread
        assert sum(1 for e in entries if e.startswith("b-")) == per_thread

    def test_concurrent_transactions_without_fcntl(self, store, monkeypatch):
        """Simulates Windows (no fcntl module): the per-path threading
        lock alone must still prevent lost updates between threads.
        (This was a real windows-latest CI failure: flock was the only
        intra-process serialization, and it silently no-opped there.)"""
        import aleph.epistemic.store as store_mod
        monkeypatch.setattr(store_mod, "fcntl", None)

        per_thread = 25

        def worker(tag: str):
            for i in range(per_thread):
                with store.transaction() as data:
                    data.setdefault("entries", []).append(f"{tag}-{i}")

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        entries = store.load()["entries"]
        assert len(entries) == 2 * per_thread

    def test_transaction_exception_does_not_save(self, store):
        store.save({"state": "before"})
        with pytest.raises(RuntimeError):
            with store.transaction() as data:
                data["state"] = "mutated"
                raise RuntimeError("abort")
        assert store.load() == {"state": "before"}

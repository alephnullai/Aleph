"""SqliteStore maintenance behavior."""

from __future__ import annotations

import os

from aleph.store.sqlite_store import SqliteStore


class TestVacuum:
    def test_vacuum_reclaims_freed_pages(self, tmp_path):
        """Deleted rows leave pages on the freelist; vacuum() returns the
        file to its live-data size (the full-rebuild path relies on this —
        observed 41% dead space on a real store without it)."""
        db_path = str(tmp_path / "aleph.db")
        store = SqliteStore(db_path, create=True)
        try:
            conn = store._conn
            conn.execute("CREATE TABLE scratch (id INTEGER, blob TEXT)")
            conn.executemany(
                "INSERT INTO scratch VALUES (?, ?)",
                [(i, "x" * 4096) for i in range(2000)],
            )
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            grown = os.path.getsize(db_path)

            conn.execute("DELETE FROM scratch")
            conn.commit()
            freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
            assert freelist > 0  # pages freed but file not shrunk

            store.vacuum()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
            assert os.path.getsize(db_path) < grown
        finally:
            store.close()

import fcntl
from importlib.machinery import SourceFileLoader
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4


loader = SourceFileLoader('codex_launcher_index', str(Path(__file__).resolve().parents[1] / 'bin/codex+1'))
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)


def row(thread, name):
    return json.dumps({'id': str(thread), 'thread_name': name, 'updated_at': '2026-01-01T00:00:00Z'}) + '\n'


class LegacyIndexTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        shared = root / 'shared'
        overlay = root / 'overlay'
        shared.mkdir()
        overlay.mkdir()
        self.source = shared / 'session_index.jsonl'
        self.destination = overlay / self.source.name
        self.lock = shared / 'session_index.jsonl.lock'
        self.lock.touch()
        self.private_lock = overlay / self.lock.name
        self.private_lock.symlink_to(self.lock)
        self.deleted = uuid4()
        self.retained = uuid4()
        self.kept = row(self.retained, 'keep')
        self.original = row(self.deleted, 'old') + self.kept + row(self.deleted, 'renamed')
        self.source.write_text(self.original)
        self.source.chmod(0o600)
        self.destination.write_text(self.kept)
        self.db = sqlite3.connect(shared / 'state_5.sqlite')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY)')
        self.add_state(self.retained)

    def add_state(self, thread):
        with self.db:
            self.db.execute('INSERT INTO threads (id) VALUES (?)', (str(thread),))

    def repair(self):
        return launcher.repair_legacy_session_index(self.source, self.destination)

    def test_replays_only_confirmed_deletions_and_relinks(self):
        lock_inode = self.lock.stat().st_ino
        self.assertTrue(self.repair())
        self.assertEqual(self.source.read_text(), self.kept)
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.destination.is_symlink())
        self.assertTrue(self.destination.samefile(self.source))
        self.assertEqual(self.lock.stat().st_ino, lock_inode)

    def test_does_not_drop_existing_thread_or_independent_writes(self):
        self.add_state(self.deleted)
        self.assertFalse(self.repair())
        self.destination.write_text(self.kept + row(uuid4(), 'independent write'))
        self.assertFalse(self.repair())
        self.assertEqual(self.source.read_text(), self.original)
        self.assertFalse(self.destination.is_symlink())

    def test_does_not_repair_without_the_same_available_lock(self):
        with self.lock.open('r+b') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(self.repair())
        self.private_lock.unlink()
        self.private_lock.touch()
        self.assertFalse(self.repair())
        self.assertEqual(self.source.read_text(), self.original)

    def test_relinks_identical_copies_without_rewriting(self):
        self.destination.write_text(self.original)
        index_inode = self.source.stat().st_ino
        self.assertTrue(self.repair())
        self.assertTrue(self.destination.samefile(self.source))
        self.assertEqual(self.source.stat().st_ino, index_inode)

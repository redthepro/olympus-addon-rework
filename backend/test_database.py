from pathlib import Path
import tempfile
import unittest

from database import Database


class Tests(unittest.TestCase):
    def test_sqlite_transactions_rollback_and_close(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / 'test.db')
            with database.connect() as db:
                db.execute('CREATE TABLE entries (id INTEGER PRIMARY KEY, value TEXT)')
            with self.assertRaises(RuntimeError):
                with database.connect(immediate=True) as db:
                    db.execute('INSERT INTO entries VALUES (?,?)', (1, 'value?'))
                    raise RuntimeError()
            with database.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM entries').fetchone()[0], 0)

    def test_sqlite_values_are_bound_and_committed(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / 'test.db')
            with database.connect() as db:
                db.execute('CREATE TABLE entries (id INTEGER PRIMARY KEY, value TEXT)')
                db.execute('INSERT INTO entries VALUES (?,?)', (1, "value'); DROP TABLE entries;--"))
            with database.connect() as db:
                self.assertEqual(db.execute('SELECT value FROM entries').fetchone()[0], "value'); DROP TABLE entries;--")

    def test_only_file_database_is_accepted(self):
        for value in ('', ':memory:', 'postgresql://example/test', 'rds-iam'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Database(value)


if __name__ == '__main__':
    unittest.main()

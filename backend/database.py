"""Local SQLite transactions for the Olympus desktop companion."""
from contextlib import contextmanager
import sqlite3


class Database:
    def __init__(self, location):
        self.location = str(location)
        if not self.location or '://' in self.location or self.location in ('rds-iam', ':memory:'):
            raise ValueError('Census database must be a local SQLite file')

    @contextmanager
    def connect(self, immediate=False):
        raw = sqlite3.connect(self.location, timeout=10)
        try:
            raw.execute('PRAGMA secure_delete=ON')
            if immediate:
                raw.execute('BEGIN IMMEDIATE')
            with raw:
                yield raw
        finally:
            raw.close()

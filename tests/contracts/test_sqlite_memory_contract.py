"""
Concrete contract tests for SqliteRequestMemory.

Runs the full RequestMemoryContract suite against the real SQLite adapter.
"""

from src.adapters.sqlite_memory import SqliteRequestMemory
from tests.contracts.request_memory_contract import RequestMemoryContract


class TestSqliteMemoryContract(RequestMemoryContract):
    def create_memory(self) -> SqliteRequestMemory:
        return SqliteRequestMemory(":memory:")

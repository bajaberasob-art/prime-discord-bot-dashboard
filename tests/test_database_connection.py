import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import database


class DatabaseConnectionCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_connection_setup_releases_semaphore(self):
        original_db_name = database.DB_NAME
        database.DB_NAME = f"/tmp/prime_db_cancel_{os.getpid()}.db"
        try:
            async with database.connect():
                pass

            semaphore = database._db_semaphore
            before = semaphore._value
            with patch.object(
                database,
                "_configure",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    async with database.connect():
                        pass

            self.assertEqual(
                semaphore._value,
                before,
                "Cancelled connection setup must release its semaphore slot",
            )
        finally:
            database.DB_NAME = original_db_name


if __name__ == "__main__":
    unittest.main()

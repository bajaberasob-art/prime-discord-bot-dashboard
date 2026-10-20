import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

import discord

from interaction_runtime import defer_if_needed


class InteractionAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    async def test_defer_returns_true_when_acknowledged(self):
        response = Mock()
        response.is_done.return_value = False
        response.defer = AsyncMock()
        interaction = Mock()
        interaction.response = response
        interaction.guild = None
        interaction.command = None

        self.assertTrue(await defer_if_needed(interaction))
        response.defer.assert_awaited_once_with(ephemeral=False)

    async def test_defer_timeout_is_reported_as_failure(self):
        response = Mock()
        response.is_done.return_value = False

        async def slow_defer(**kwargs):
            await asyncio.sleep(2.1)

        response.defer = AsyncMock(side_effect=slow_defer)
        interaction = Mock()
        interaction.response = response
        interaction.guild = None
        interaction.command = None

        self.assertFalse(await defer_if_needed(interaction))

    async def test_already_acknowledged_interaction_does_not_defer_twice(self):
        response = Mock()
        response.is_done.return_value = True
        response.defer = AsyncMock()
        interaction = Mock()
        interaction.response = response
        interaction.guild = None
        interaction.command = None

        self.assertTrue(await defer_if_needed(interaction))
        response.defer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

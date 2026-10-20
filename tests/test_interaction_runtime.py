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

    async def test_deferred_component_edit_updates_source_message(self):
        from interaction_runtime import InteractionProxy

        message = Mock()
        message.edit = AsyncMock(return_value="edited-message")
        response = Mock()
        response.edit_message = AsyncMock()
        interaction = Mock()
        interaction.response = response
        interaction.message = message
        interaction.followup = Mock()
        interaction.followup.edit_message = AsyncMock()

        proxy = InteractionProxy(interaction, deferred=True)
        result = await proxy.response.edit_message(content="updated", view=None)

        self.assertEqual(result, "edited-message")
        message.edit.assert_awaited_once_with(content="updated", view=None)
        interaction.followup.edit_message.assert_not_awaited()
        response.edit_message.assert_not_awaited()

    async def test_deferred_component_edit_falls_back_to_original_response(self):
        from interaction_runtime import InteractionProxy

        response = Mock()
        response.edit_message = AsyncMock()
        interaction = Mock()
        interaction.response = response
        interaction.message = None
        interaction.followup = Mock()
        interaction.followup.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock(return_value="original-edited")

        proxy = InteractionProxy(interaction, deferred=True)
        result = await proxy.response.edit_message(content="updated")

        self.assertEqual(result, "original-edited")
        interaction.edit_original_response.assert_awaited_once_with(content="updated")
        interaction.followup.edit_message.assert_not_awaited()

    async def test_unacknowledged_component_edit_uses_initial_response(self):
        from interaction_runtime import InteractionProxy

        response = Mock()
        response.edit_message = AsyncMock(return_value="initial-edited")
        interaction = Mock()
        interaction.response = response
        interaction.message = Mock()
        interaction.message.edit = AsyncMock()
        interaction.followup = Mock()
        interaction.followup.edit_message = AsyncMock()

        proxy = InteractionProxy(interaction, deferred=False)
        result = await proxy.response.edit_message(embed="embed")

        self.assertEqual(result, "initial-edited")
        response.edit_message.assert_awaited_once_with(embed="embed")
        interaction.message.edit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.cv_checker.client import add_source, ask_notebook


class NotebookLMClientWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_source_passes_wait_timeout(self) -> None:
        add_file = AsyncMock(return_value=SimpleNamespace(id="source-1"))
        client = SimpleNamespace(sources=SimpleNamespace(add_file=add_file))

        source_id = await add_source(
            client,
            "notebook-1",
            Path("candidate.pdf"),
            wait=True,
            wait_timeout=123.0,
        )

        self.assertEqual(source_id, "source-1")
        add_file.assert_awaited_once_with(
            "notebook-1",
            Path("candidate.pdf"),
            wait=True,
            wait_timeout=123.0,
        )

    async def test_ask_notebook_passes_source_ids_and_conversation_id(self) -> None:
        ask = AsyncMock(return_value=SimpleNamespace(answer="answer", conversation_id="conversation-2"))
        client = SimpleNamespace(chat=SimpleNamespace(ask=ask))

        answer, conversation_id = await ask_notebook(
            client,
            "notebook-1",
            "prompt",
            source_ids=["source-1"],
            conversation_id="conversation-1",
        )

        self.assertEqual(answer, "answer")
        self.assertEqual(conversation_id, "conversation-2")
        ask.assert_awaited_once_with(
            "notebook-1",
            "prompt",
            source_ids=["source-1"],
            conversation_id="conversation-1",
        )

    async def test_ask_notebook_retries_retryable_error(self) -> None:
        ask = AsyncMock(
            side_effect=[
                RuntimeError("server disconnected"),
                SimpleNamespace(answer="answer", conversation_id="conversation-1"),
            ]
        )
        client = SimpleNamespace(chat=SimpleNamespace(ask=ask))

        with patch("src.cv_checker.client.asyncio.sleep", AsyncMock()) as sleep_mock:
            answer, _ = await ask_notebook(client, "notebook-1", "prompt")

        self.assertEqual(answer, "answer")
        self.assertEqual(ask.await_count, 2)
        sleep_mock.assert_awaited_once_with(10)

    async def test_ask_notebook_does_not_retry_non_retryable_error(self) -> None:
        ask = AsyncMock(side_effect=ValueError("bad request"))
        client = SimpleNamespace(chat=SimpleNamespace(ask=ask))

        with patch("src.cv_checker.client.asyncio.sleep", AsyncMock()) as sleep_mock:
            with self.assertRaisesRegex(ValueError, "bad request"):
                await ask_notebook(client, "notebook-1", "prompt")

        self.assertEqual(ask.await_count, 1)
        sleep_mock.assert_not_awaited()

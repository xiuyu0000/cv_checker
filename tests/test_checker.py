import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.cv_checker.checker import check_cv


class CheckCVTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_cv_uploads_cv_and_asks_review_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            cv_path = base_path / "candidate.pdf"
            cv_path.write_text("cv", encoding="utf-8")
            config_path = base_path / "prompt.yaml"
            config_path.write_text("review_prompt: Review carefully\n", encoding="utf-8")

            with (
                patch("src.cv_checker.checker.add_source", AsyncMock(return_value="source-1")) as add_mock,
                patch("src.cv_checker.checker.ask_notebook", AsyncMock(return_value=("result", "conversation-1"))) as ask_mock,
            ):
                fake_client = object()
                result = await check_cv(fake_client, "notebook-1", cv_path, config_path)

            self.assertEqual(result, "result")
            add_mock.assert_awaited_once_with(fake_client, "notebook-1", cv_path)
            ask_mock.assert_awaited_once()
            self.assertEqual(ask_mock.await_args.args[1], "notebook-1")
            self.assertEqual(ask_mock.await_args.args[2], "Review carefully")

    async def test_check_cv_rejects_empty_review_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            cv_path = base_path / "candidate.pdf"
            cv_path.write_text("cv", encoding="utf-8")
            config_path = base_path / "prompt.yaml"
            config_path.write_text("review_prompt: ''\n", encoding="utf-8")

            with (
                patch("src.cv_checker.checker.add_source", AsyncMock()) as add_mock,
                patch("src.cv_checker.checker.ask_notebook", AsyncMock()) as ask_mock,
            ):
                with self.assertRaisesRegex(ValueError, "review_prompt is empty"):
                    await check_cv(object(), "notebook-1", cv_path, config_path)

            add_mock.assert_not_awaited()
            ask_mock.assert_not_awaited()

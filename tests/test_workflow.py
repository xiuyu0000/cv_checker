import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.cv_checker.config import load_workflow_config
from src.cv_checker.workflow import InterviewPromptError, run_interview_prompts, run_interview_workflow


class WorkflowConfigTests(unittest.TestCase):
    def test_load_workflow_config_rejects_missing_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "workflow.yaml"
            config_path.write_text("workflow:\n  prompts:\n    fit_analysis: x\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Missing workflow prompts"):
                load_workflow_config(config_path)


class InterviewWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_interview_prompts_reuses_conversation_and_source_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "out"
            ask_mock = AsyncMock(
                side_effect=[
                    ("fit", "conversation-1"),
                    ("gaps", "conversation-2"),
                    ("template", "conversation-3"),
                ]
            )

            with patch("src.cv_checker.workflow.ask_notebook", ask_mock):
                await run_interview_prompts(
                    object(),
                    notebook_id="notebook-1",
                    candidate_name="张三",
                    output_dir=output_dir,
                    source_ids=["default-source", "candidate-source"],
                )

            self.assertEqual(ask_mock.await_count, 3)
            self.assertIsNone(ask_mock.await_args_list[0].kwargs["conversation_id"])
            self.assertEqual(ask_mock.await_args_list[1].kwargs["conversation_id"], "conversation-1")
            self.assertEqual(ask_mock.await_args_list[2].kwargs["conversation_id"], "conversation-2")
            for call in ask_mock.await_args_list:
                self.assertEqual(call.kwargs["source_ids"], ["default-source", "candidate-source"])

    async def test_run_interview_prompts_writes_completed_stage_before_stage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "out"
            ask_mock = AsyncMock(
                side_effect=[
                    ("fit", "conversation-1"),
                    RuntimeError("timeout"),
                ]
            )

            with patch("src.cv_checker.workflow.ask_notebook", ask_mock):
                with self.assertRaises(InterviewPromptError) as raised:
                    await run_interview_prompts(
                        object(),
                        notebook_id="notebook-1",
                        candidate_name="张三",
                        output_dir=output_dir,
                    )

            self.assertEqual(raised.exception.stage, "information_gaps")
            self.assertEqual((output_dir / "01_fit_analysis.md").read_text(encoding="utf-8"), "fit")
            self.assertFalse((output_dir / "02_information_gaps.md").exists())

    async def test_run_interview_workflow_writes_reports_and_deletes_candidate_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            candidate_cv = base_path / "candidate.md"
            candidate_cv.write_text("candidate", encoding="utf-8")
            output_dir = base_path / "out"

            ask_mock = AsyncMock(
                side_effect=[
                    ("fit", "conversation-1"),
                    ("gaps", "conversation-1"),
                    ("template", "conversation-1"),
                ]
            )

            with (
                patch("src.cv_checker.workflow.add_source", AsyncMock(return_value="source-1")) as add_mock,
                patch("src.cv_checker.workflow.ask_notebook", ask_mock),
                patch("src.cv_checker.workflow.delete_source", AsyncMock(return_value=True)) as delete_mock,
            ):
                fake_client = object()
                result = await run_interview_workflow(
                    fake_client,
                    notebook_id="notebook-1",
                    candidate_cv_path=candidate_cv,
                    candidate_name="张三",
                    output_dir=output_dir,
                )

            add_mock.assert_awaited_once()
            self.assertEqual(ask_mock.await_count, 3)
            delete_mock.assert_awaited_once_with(fake_client, "notebook-1", "source-1")
            self.assertTrue(result.candidate_source_deleted)
            self.assertEqual((output_dir / "01_fit_analysis.md").read_text(encoding="utf-8"), "fit")
            self.assertEqual((output_dir / "02_information_gaps.md").read_text(encoding="utf-8"), "gaps")
            self.assertEqual((output_dir / "03_interview_template.md").read_text(encoding="utf-8"), "template")

    async def test_run_interview_workflow_attempts_cleanup_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            candidate_cv = base_path / "candidate.md"
            candidate_cv.write_text("candidate", encoding="utf-8")

            with (
                patch("src.cv_checker.workflow.add_source", AsyncMock(return_value="source-1")),
                patch("src.cv_checker.workflow.ask_notebook", AsyncMock(side_effect=RuntimeError("boom"))),
                patch("src.cv_checker.workflow.delete_source", AsyncMock(return_value=True)) as delete_mock,
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    await run_interview_workflow(
                        object(),
                        notebook_id="notebook-1",
                        candidate_cv_path=candidate_cv,
                        candidate_name="张三",
                        output_dir=base_path / "out",
                    )

            self.assertEqual(delete_mock.await_count, 1)

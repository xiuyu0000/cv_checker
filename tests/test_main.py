import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main as cli_main


class RunWithClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_with_client_dispatches_legacy_mode(self) -> None:
        args = SimpleNamespace(
            batch_cv_dir=None,
            candidate_cv=None,
            notebook_id="notebook-1",
            cv=Path("candidate.pdf"),
            config=Path("prompt.yaml"),
        )

        with patch("main.check_cv", AsyncMock(return_value="review result")) as check_mock:
            result = await cli_main._run_with_client(args, object())

        self.assertEqual(result, "review result")
        check_mock.assert_awaited_once()
        self.assertEqual(check_mock.await_args.args[1], "notebook-1")
        self.assertEqual(check_mock.await_args.args[2], Path("candidate.pdf"))
        self.assertEqual(check_mock.await_args.args[3], Path("prompt.yaml"))

    async def test_run_with_client_dispatches_interview_workflow_and_reports_kept_source(self) -> None:
        args = SimpleNamespace(
            batch_cv_dir=None,
            candidate_cv=Path("candidate.pdf"),
            candidate_name="张三",
            candidate_role="候选人",
            requirement=[Path("requirements.md")],
            output_dir=None,
            config=Path("workflow.yaml"),
            keep_candidate_source=True,
            notebook_id="notebook-1",
        )
        workflow_result = SimpleNamespace(
            notebook_id="notebook-1",
            output_dir=Path("outputs/interview_review"),
            candidate_source_deleted=False,
            fit_analysis_path=Path("outputs/interview_review/01_fit_analysis.md"),
            information_gaps_path=Path("outputs/interview_review/02_information_gaps.md"),
            interview_template_path=Path("outputs/interview_review/03_interview_template.md"),
        )

        with patch("main.run_interview_workflow", AsyncMock(return_value=workflow_result)) as workflow_mock:
            result = await cli_main._run_with_client(args, object())

        self.assertIn("Candidate source: kept", result)
        self.assertFalse(workflow_mock.await_args.kwargs["delete_candidate_source_after_run"])
        self.assertEqual(workflow_mock.await_args.kwargs["workflow_config_path"], Path("workflow.yaml"))

    async def test_run_with_client_dispatches_batch_mode(self) -> None:
        args = SimpleNamespace(
            batch_cv_dir=Path("data/raw/cv"),
            source_dir=Path("data/sources"),
            default_notebook_config=Path("config/default_notebook.yaml"),
            output_dir=None,
            processed_dir=Path("data/raw/cv/processed"),
            notebook_id=None,
            config=Path("workflow.yaml"),
            candidate_cv=None,
        )
        batch_result = SimpleNamespace(
            notebook_id="notebook-1",
            manifest_path=Path("outputs/interview_batch/run_manifest.jsonl"),
            processed_count=1,
            failed_count=0,
            results=[object()],
        )

        with patch("main.run_batch_interview_workflow", AsyncMock(return_value=batch_result)) as batch_mock:
            result = await cli_main._run_with_client(args, object())

        self.assertIn("Batch interview workflow completed.", result)
        self.assertIn("Processed: 1", result)
        self.assertEqual(batch_mock.await_args.kwargs["workflow_config_path"], Path("workflow.yaml"))

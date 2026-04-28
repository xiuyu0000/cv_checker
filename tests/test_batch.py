import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

from src.cv_checker.batch import (
    candidate_name_from_filename,
    cleanup_unrecorded_sources,
    ensure_default_notebook,
    iter_cv_files,
    run_batch_interview_workflow,
)
from src.cv_checker.workflow import InterviewPromptError


class BatchFileScanTests(unittest.TestCase):
    def test_iter_cv_files_ignores_hidden_files_and_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cv_dir = Path(temp_dir)
            (cv_dir / "b.pdf").write_text("b", encoding="utf-8")
            (cv_dir / "a.pdf").write_text("a", encoding="utf-8")
            (cv_dir / ".hidden.pdf").write_text("hidden", encoding="utf-8")
            (cv_dir / ".DS_Store").write_text("system", encoding="utf-8")
            (cv_dir / "processed").mkdir()

            self.assertEqual(
                [path.name for path in iter_cv_files(cv_dir)],
                ["a.pdf", "b.pdf"],
            )

    def test_candidate_name_from_filename_uses_chinese_name_prefix_only(self) -> None:
        self.assertEqual(candidate_name_from_filename(Path("邱恒 27年应届生.pdf")), "邱恒")
        self.assertEqual(candidate_name_from_filename(Path("candidate.pdf")), "")


class DefaultNotebookTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_default_notebook_creates_notebook_and_records_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            source_dir = base_path / "sources"
            source_dir.mkdir()
            source_file = source_dir / "requirements.md"
            source_file.write_text("requirements", encoding="utf-8")
            config_path = base_path / "default_notebook.yaml"

            with (
                patch("src.cv_checker.batch.create_notebook", AsyncMock(return_value="notebook-1")) as create_mock,
                patch("src.cv_checker.batch.add_source", AsyncMock(return_value="source-1")) as add_mock,
            ):
                result = await ensure_default_notebook(
                    object(),
                    source_dir=source_dir,
                    config_path=config_path,
                )

            create_mock.assert_awaited_once()
            add_mock.assert_awaited_once()
            self.assertEqual(result.notebook_id, "notebook-1")
            self.assertEqual(result.source_count, 1)
            saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["notebook"]["id"], "notebook-1")
            self.assertEqual(saved["sources"][0]["id"], "source-1")


class BatchWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_batch_interview_workflow_writes_outputs_deletes_source_and_moves_cv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            cv_dir = base_path / "cv"
            cv_dir.mkdir()
            cv_path = cv_dir / "candidate.pdf"
            cv_path.write_text("cv", encoding="utf-8")
            source_dir = base_path / "sources"
            source_dir.mkdir()
            source_file = source_dir / "requirements.md"
            source_file.write_text("requirements", encoding="utf-8")
            output_dir = base_path / "out"
            processed_dir = cv_dir / "processed"
            config_path = base_path / "default_notebook.yaml"
            config_path.write_text(
                (
                    "notebook:\n"
                    "  id: notebook-1\n"
                    "  title: Existing\n"
                    "sources:\n"
                    f"- path: {source_file}\n"
                    "  id: default-source\n"
                ),
                encoding="utf-8",
            )

            profile = {
                "candidate_name": "张三",
                "candidate_role": "后端工程师",
                "summary": "候选人画像",
                "confidence": "high",
            }
            with (
                patch("src.cv_checker.batch.add_source", AsyncMock(return_value="candidate-source")),
                patch("src.cv_checker.batch.ask_notebook", AsyncMock(return_value=(json.dumps(profile), "profile-c"))) as profile_ask_mock,
                patch("src.cv_checker.workflow.ask_notebook", AsyncMock(side_effect=[
                    ("fit", "conversation-1"),
                    ("gaps", "conversation-1"),
                    ("template", "conversation-1"),
                ])) as workflow_ask_mock,
                patch("src.cv_checker.batch.delete_source", AsyncMock(return_value=True)),
            ):
                result = await run_batch_interview_workflow(
                    object(),
                    cv_dir=cv_dir,
                    source_dir=source_dir,
                    default_notebook_config_path=config_path,
                    output_dir=output_dir,
                    processed_dir=processed_dir,
                )

            self.assertEqual(result.processed_count, 1)
            self.assertEqual(result.failed_count, 0)
            self.assertEqual(profile_ask_mock.await_args.kwargs["source_ids"], ["candidate-source"])
            self.assertEqual(
                workflow_ask_mock.await_args_list[0].kwargs["source_ids"],
                ["default-source", "candidate-source"],
            )
            candidate_output_dir = output_dir / "张三"
            self.assertEqual((candidate_output_dir / "01_fit_analysis.md").read_text(encoding="utf-8"), "fit")
            self.assertEqual((candidate_output_dir / "02_information_gaps.md").read_text(encoding="utf-8"), "gaps")
            self.assertEqual((candidate_output_dir / "03_interview_template.md").read_text(encoding="utf-8"), "template")
            saved_profile = json.loads(
                (candidate_output_dir / "00_candidate_profile.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_profile["candidate_name"], "张三")
            self.assertFalse(cv_path.exists())
            self.assertTrue((processed_dir / "candidate.pdf").exists())

            manifest_records = [
                json.loads(line)
                for line in (output_dir / "run_manifest.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(manifest_records[0]["status"], "success")
            self.assertTrue(manifest_records[0]["candidate_source_deleted"])
            self.assertIsNone(manifest_records[0]["failure_stage"])

    async def test_run_batch_interview_workflow_keeps_cv_when_candidate_name_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            cv_dir = base_path / "cv"
            cv_dir.mkdir()
            cv_path = cv_dir / "candidate.pdf"
            cv_path.write_text("cv", encoding="utf-8")
            source_dir = base_path / "sources"
            source_dir.mkdir()
            output_dir = base_path / "out"
            config_path = base_path / "default_notebook.yaml"
            config_path.write_text("notebook:\n  id: notebook-1\nsources: []\n", encoding="utf-8")

            with (
                patch("src.cv_checker.batch.add_source", AsyncMock(return_value="candidate-source")),
                patch(
                    "src.cv_checker.batch.ask_notebook",
                    AsyncMock(return_value=('{"candidate_name": ""}', "profile-c")),
                ),
                patch("src.cv_checker.workflow.ask_notebook", AsyncMock()) as workflow_ask_mock,
                patch("src.cv_checker.batch.delete_source", AsyncMock(return_value=True)) as delete_mock,
            ):
                result = await run_batch_interview_workflow(
                    object(),
                    cv_dir=cv_dir,
                    source_dir=source_dir,
                    default_notebook_config_path=config_path,
                    output_dir=output_dir,
                    processed_dir=cv_dir / "processed",
                )

            self.assertEqual(result.processed_count, 0)
            self.assertEqual(result.failed_count, 1)
            self.assertTrue(cv_path.exists())
            self.assertFalse((cv_dir / "processed" / "candidate.pdf").exists())
            workflow_ask_mock.assert_not_awaited()
            delete_mock.assert_awaited_once()
            manifest = json.loads((output_dir / "run_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(manifest["status"], "failed")
            self.assertIn("candidate_name", manifest["error"])

    async def test_run_batch_interview_workflow_uses_filename_fallback_for_chinese_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            cv_dir = base_path / "cv"
            cv_dir.mkdir()
            cv_path = cv_dir / "邱恒 27年应届生.pdf"
            cv_path.write_text("cv", encoding="utf-8")
            source_dir = base_path / "sources"
            source_dir.mkdir()
            output_dir = base_path / "out"
            config_path = base_path / "default_notebook.yaml"
            config_path.write_text("notebook:\n  id: notebook-1\nsources: []\n", encoding="utf-8")

            with (
                patch("src.cv_checker.batch.add_source", AsyncMock(return_value="candidate-source")),
                patch(
                    "src.cv_checker.batch.ask_notebook",
                    AsyncMock(return_value=('{"candidate_name": ""}', "profile-c")),
                ),
                patch("src.cv_checker.workflow.ask_notebook", AsyncMock(side_effect=[
                    ("fit", "conversation-1"),
                    ("gaps", "conversation-1"),
                    ("template", "conversation-1"),
                ])),
                patch("src.cv_checker.batch.delete_source", AsyncMock(return_value=True)),
            ):
                result = await run_batch_interview_workflow(
                    object(),
                    cv_dir=cv_dir,
                    source_dir=source_dir,
                    default_notebook_config_path=config_path,
                    output_dir=output_dir,
                    processed_dir=cv_dir / "processed",
                )

            self.assertEqual(result.processed_count, 1)
            candidate_output_dir = output_dir / "邱恒"
            saved_profile = json.loads(
                (candidate_output_dir / "00_candidate_profile.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_profile["candidate_name"], "邱恒")
            self.assertEqual(saved_profile["confidence"], "filename_fallback")

    async def test_run_batch_interview_workflow_reports_empty_directory_as_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            cv_dir = base_path / "cv"
            cv_dir.mkdir()
            source_dir = base_path / "sources"
            source_dir.mkdir()
            output_dir = base_path / "out"
            config_path = base_path / "default_notebook.yaml"
            config_path.write_text("notebook:\n  id: notebook-1\nsources: []\n", encoding="utf-8")

            result = await run_batch_interview_workflow(
                object(),
                cv_dir=cv_dir,
                source_dir=source_dir,
                default_notebook_config_path=config_path,
                output_dir=output_dir,
                processed_dir=cv_dir / "processed",
            )

            self.assertEqual(result.processed_count, 0)
            self.assertEqual(result.failed_count, 0)
            self.assertEqual(result.skipped_count, 1)
            self.assertFalse((output_dir / "run_manifest.jsonl").exists())

    async def test_run_batch_interview_workflow_keeps_cv_when_source_delete_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            cv_dir = base_path / "cv"
            cv_dir.mkdir()
            cv_path = cv_dir / "candidate.pdf"
            cv_path.write_text("cv", encoding="utf-8")
            source_dir = base_path / "sources"
            source_dir.mkdir()
            output_dir = base_path / "out"
            config_path = base_path / "default_notebook.yaml"
            config_path.write_text("notebook:\n  id: notebook-1\nsources: []\n", encoding="utf-8")
            profile = {"candidate_name": "李四", "candidate_role": "", "summary": "", "confidence": "medium"}

            with (
                patch("src.cv_checker.batch.add_source", AsyncMock(return_value="candidate-source")),
                patch("src.cv_checker.batch.ask_notebook", AsyncMock(return_value=(json.dumps(profile), "profile-c"))),
                patch("src.cv_checker.workflow.ask_notebook", AsyncMock(side_effect=[
                    ("fit", "conversation-1"),
                    ("gaps", "conversation-1"),
                    ("template", "conversation-1"),
                ])),
                patch("src.cv_checker.batch.delete_source", AsyncMock(return_value=False)),
            ):
                result = await run_batch_interview_workflow(
                    object(),
                    cv_dir=cv_dir,
                    source_dir=source_dir,
                    default_notebook_config_path=config_path,
                    output_dir=output_dir,
                    processed_dir=cv_dir / "processed",
                )

            self.assertEqual(result.processed_count, 0)
            self.assertEqual(result.failed_count, 1)
            self.assertTrue(cv_path.exists())
            self.assertFalse((cv_dir / "processed" / "candidate.pdf").exists())
            manifest = json.loads((output_dir / "run_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(manifest["error"], "candidate source deletion failed")
            self.assertEqual(manifest["failure_stage"], "delete_source")

    async def test_run_batch_interview_workflow_does_not_overwrite_processed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            cv_dir = base_path / "cv"
            cv_dir.mkdir()
            cv_path = cv_dir / "candidate.pdf"
            cv_path.write_text("new cv", encoding="utf-8")
            processed_dir = cv_dir / "processed"
            processed_dir.mkdir()
            (processed_dir / "candidate.pdf").write_text("existing cv", encoding="utf-8")
            source_dir = base_path / "sources"
            source_dir.mkdir()
            output_dir = base_path / "out"
            config_path = base_path / "default_notebook.yaml"
            config_path.write_text("notebook:\n  id: notebook-1\nsources: []\n", encoding="utf-8")
            profile = {"candidate_name": "王五", "candidate_role": "", "summary": "", "confidence": "high"}

            with (
                patch("src.cv_checker.batch.add_source", AsyncMock(return_value="candidate-source")),
                patch("src.cv_checker.batch.ask_notebook", AsyncMock(return_value=(json.dumps(profile), "profile-c"))),
                patch("src.cv_checker.workflow.ask_notebook", AsyncMock(side_effect=[
                    ("fit", "conversation-1"),
                    ("gaps", "conversation-1"),
                    ("template", "conversation-1"),
                ])),
                patch("src.cv_checker.batch.delete_source", AsyncMock(return_value=True)),
            ):
                result = await run_batch_interview_workflow(
                    object(),
                    cv_dir=cv_dir,
                    source_dir=source_dir,
                    default_notebook_config_path=config_path,
                    output_dir=output_dir,
                    processed_dir=processed_dir,
                )

            self.assertEqual(result.processed_count, 1)
            self.assertEqual((processed_dir / "candidate.pdf").read_text(encoding="utf-8"), "existing cv")
            self.assertEqual((processed_dir / "candidate_2.pdf").read_text(encoding="utf-8"), "new cv")

    async def test_run_batch_interview_workflow_records_prompt_failure_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            cv_dir = base_path / "cv"
            cv_dir.mkdir()
            cv_path = cv_dir / "candidate.pdf"
            cv_path.write_text("cv", encoding="utf-8")
            source_dir = base_path / "sources"
            source_dir.mkdir()
            output_dir = base_path / "out"
            config_path = base_path / "default_notebook.yaml"
            config_path.write_text("notebook:\n  id: notebook-1\nsources: []\n", encoding="utf-8")
            profile = {"candidate_name": "赵六", "candidate_role": "", "summary": "", "confidence": "high"}

            with (
                patch("src.cv_checker.batch.add_source", AsyncMock(return_value="candidate-source")),
                patch("src.cv_checker.batch.ask_notebook", AsyncMock(return_value=(json.dumps(profile), "profile-c"))),
                patch(
                    "src.cv_checker.batch.run_interview_prompts",
                    AsyncMock(side_effect=InterviewPromptError("information_gaps", RuntimeError("timeout"))),
                ),
                patch("src.cv_checker.batch.delete_source", AsyncMock(return_value=True)) as delete_mock,
            ):
                result = await run_batch_interview_workflow(
                    object(),
                    cv_dir=cv_dir,
                    source_dir=source_dir,
                    default_notebook_config_path=config_path,
                    output_dir=output_dir,
                    processed_dir=cv_dir / "processed",
                )

            self.assertEqual(result.processed_count, 0)
            self.assertEqual(result.failed_count, 1)
            self.assertTrue(cv_path.exists())
            delete_mock.assert_awaited_once()
            manifest = json.loads((output_dir / "run_manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(manifest["failure_stage"], "information_gaps")

    async def test_cleanup_unrecorded_sources_deletes_only_non_default_sources(self) -> None:
        class Source:
            def __init__(self, source_id: str, title: str) -> None:
                self.id = source_id
                self.title = title

        class Sources:
            async def list(self, notebook_id: str) -> list[Source]:
                return [
                    Source("default-source", "requirements.md"),
                    Source("candidate-source", "candidate.pdf"),
                ]

        class Client:
            sources = Sources()

        client = Client()
        with patch("src.cv_checker.batch.delete_source", AsyncMock(return_value=True)) as delete_mock:
            deleted_count = await cleanup_unrecorded_sources(
                client,
                "notebook-1",
                ["default-source"],
            )

        self.assertEqual(deleted_count, 1)
        delete_mock.assert_awaited_once_with(client, "notebook-1", "candidate-source")

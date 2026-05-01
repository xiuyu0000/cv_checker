import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.cv_checker.config import load_workflow_config
from src.cv_checker.workflow import InterviewPromptError, STAGE_DONE_MARKERS, run_interview_prompts, run_interview_workflow


FIT_OUTPUT = "fit"
GAPS_OUTPUT = "## 信息缺口\n缺口\n## 矛盾\n矛盾\n## 可疑与造假包装\n可疑造假包装\n## 真实性和重点考察\n真实性重点考察"
TEMPLATE_OUTPUT = "template"


def stage_output(stage: str, content: str) -> str:
    return f"{content}\n\n{STAGE_DONE_MARKERS[stage]}"


class WorkflowConfigTests(unittest.TestCase):
    def test_load_workflow_config_rejects_missing_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "workflow.yaml"
            config_path.write_text("workflow:\n  prompts:\n    fit_analysis: x\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Missing workflow prompts"):
                load_workflow_config(config_path)

    def test_default_information_gaps_prompt_sets_structured_evidence_boundaries(self) -> None:
        config = load_workflow_config()
        prompt = config["workflow"]["prompts"]["information_gaps"]

        for expected_text in [
            "信息缺口",
            "矛盾内容",
            "可疑/造假/包装",
            "真实性/重点考察",
            "证据边界",
            "时间线",
            "职责范围",
            "成果指标",
            "技术细节",
            "业务常识",
            "项目复杂度与资历匹配度",
            "默认不作为真实性、候选人特质、职业严谨度或造假包装依据",
            "若影响关键事实读取，只列为信息缺口",
        ]:
            self.assertIn(expected_text, prompt)

        self.assertNotIn("这些通常是简历从招聘平台或文档工具导出造成的", prompt)
        self.assertNotIn("ATS 规避", prompt)
        self.assertNotIn("不要把乱码本身归入可疑/造假分析", prompt)

    def test_default_fit_analysis_prompt_sets_structured_evidence_boundaries(self) -> None:
        config = load_workflow_config()
        prompt = config["workflow"]["prompts"]["fit_analysis"]

        for expected_text in [
            "适配证据",
            "不适配证据",
            "结论与风险",
            "直接引用",
            "材料未提供",
            "确定事实",
            "合理推断",
            "面试核实",
            "PDF/OCR 解析异常",
            "默认不作为真实性、候选人特质、职业严谨度或适配性的判断依据",
        ]:
            self.assertIn(expected_text, prompt)

    def test_default_interview_template_prompt_sets_structured_question_contract(self) -> None:
        config = load_workflow_config()
        prompt = config["workflow"]["prompts"]["interview_template"]

        for expected_text in [
            "专业能力",
            "学习能力",
            "综合素质",
            "面试问题",
            "考察点",
            "参考优秀回答要点",
            "评价记录",
            "fit_analysis",
            "information_gaps",
            "实质证据",
            "信息缺口",
            "矛盾内容",
            "可疑/造假/包装风险",
            "真实性/重点考察项",
        ]:
            self.assertIn(expected_text, prompt)

        self.assertIn("不要围绕简历乱码/导出异常设计面试问题", prompt)
        self.assertIn("除非 information_gaps 明确指出关键事实不可读", prompt)


class InterviewWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_interview_prompts_reuses_conversation_and_source_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "out"
            ask_mock = AsyncMock(
                side_effect=[
                    (stage_output("fit_analysis", FIT_OUTPUT), "conversation-1"),
                    (stage_output("information_gaps", GAPS_OUTPUT), "conversation-2"),
                    (stage_output("interview_template", TEMPLATE_OUTPUT), "conversation-3"),
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
            self.assertIn(FIT_OUTPUT, ask_mock.await_args_list[1].args[2])
            self.assertIn(GAPS_OUTPUT, ask_mock.await_args_list[2].args[2])

    async def test_run_interview_prompts_writes_completed_stage_before_stage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "out"
            ask_mock = AsyncMock(
                side_effect=[
                    (stage_output("fit_analysis", FIT_OUTPUT), "conversation-1"),
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
            self.assertEqual((output_dir / "01_fit_analysis.md").read_text(encoding="utf-8"), FIT_OUTPUT)
            self.assertFalse((output_dir / "02_information_gaps.md").exists())

    async def test_run_interview_prompts_rejects_incomplete_information_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "out"
            ask_mock = AsyncMock(
                side_effect=[
                    (stage_output("fit_analysis", FIT_OUTPUT), "conversation-1"),
                    ("## 信息缺口\n只写到在", "conversation-2"),
                    ("## 信息缺口\n仍然没有结束标记", "conversation-2"),
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
            self.assertEqual((output_dir / "01_fit_analysis.md").read_text(encoding="utf-8"), FIT_OUTPUT)
            self.assertFalse((output_dir / "02_information_gaps.md").exists())
            self.assertEqual(ask_mock.await_count, 3)

    async def test_run_interview_prompts_retries_incomplete_stage_then_writes_complete_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "out"
            ask_mock = AsyncMock(
                side_effect=[
                    (stage_output("fit_analysis", FIT_OUTPUT), "conversation-1"),
                    ("## 信息缺口\n只写到在", "conversation-2"),
                    (stage_output("information_gaps", GAPS_OUTPUT), "conversation-2"),
                    (stage_output("interview_template", TEMPLATE_OUTPUT), "conversation-3"),
                ]
            )

            with patch("src.cv_checker.workflow.ask_notebook", ask_mock):
                await run_interview_prompts(
                    object(),
                    notebook_id="notebook-1",
                    candidate_name="张三",
                    output_dir=output_dir,
                )

            self.assertEqual(ask_mock.await_count, 4)
            self.assertIn("上一次回答未通过完整性校验", ask_mock.await_args_list[2].args[2])
            self.assertEqual((output_dir / "02_information_gaps.md").read_text(encoding="utf-8"), GAPS_OUTPUT)

    async def test_run_interview_workflow_writes_reports_and_deletes_candidate_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            candidate_cv = base_path / "candidate.md"
            candidate_cv.write_text("candidate", encoding="utf-8")
            output_dir = base_path / "out"

            ask_mock = AsyncMock(
                side_effect=[
                    (stage_output("fit_analysis", FIT_OUTPUT), "conversation-1"),
                    (stage_output("information_gaps", GAPS_OUTPUT), "conversation-1"),
                    (stage_output("interview_template", TEMPLATE_OUTPUT), "conversation-1"),
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
            self.assertEqual((output_dir / "01_fit_analysis.md").read_text(encoding="utf-8"), FIT_OUTPUT)
            self.assertEqual((output_dir / "02_information_gaps.md").read_text(encoding="utf-8"), GAPS_OUTPUT)
            self.assertEqual((output_dir / "03_interview_template.md").read_text(encoding="utf-8"), TEMPLATE_OUTPUT)

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

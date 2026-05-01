from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from notebooklm import NotebookLMClient

from .client import add_source, ask_notebook, create_notebook, delete_source
from .config import load_workflow_config


STAGE_OUTPUT_FILES = {
    "fit_analysis": "01_fit_analysis.md",
    "information_gaps": "02_information_gaps.md",
    "interview_template": "03_interview_template.md",
}

STAGE_DONE_MARKERS = {
    stage: f"<!-- CV_CHECKER_DONE:{stage} -->"
    for stage in STAGE_OUTPUT_FILES
}

MAX_STAGE_VALIDATION_ATTEMPTS = 2


@dataclass(frozen=True)
class InterviewWorkflowResult:
    """Result metadata for a completed interview workflow run."""

    notebook_id: str
    output_dir: Path
    fit_analysis_path: Path
    information_gaps_path: Path
    interview_template_path: Path
    candidate_source_id: str | None
    candidate_source_deleted: bool


@dataclass(frozen=True)
class InterviewPromptOutput:
    """Output paths for the three-step interview prompt sequence."""

    fit_analysis_path: Path
    information_gaps_path: Path
    interview_template_path: Path


class InterviewPromptError(RuntimeError):
    """Error raised when one interview prompt stage fails."""

    def __init__(self, stage: str, original_error: Exception):
        self.stage = stage
        self.original_error = original_error
        super().__init__(f"{stage} failed: {original_error}")


class StageOutputValidationError(ValueError):
    """Error raised when a NotebookLM stage response is structurally incomplete."""


async def run_interview_prompts(
    client: NotebookLMClient,
    *,
    notebook_id: str,
    candidate_name: str,
    output_dir: Path,
    candidate_role: str = "候选人",
    workflow_config_path: Path | None = None,
    source_ids: list[str] | None = None,
) -> InterviewPromptOutput:
    """Run the configured three-step interview prompts and write Markdown outputs."""
    config = load_workflow_config(workflow_config_path)
    prompts = config["workflow"]["prompts"]
    output_dir.mkdir(parents=True, exist_ok=True)

    format_context = {
        "candidate_name": candidate_name,
        "candidate_role": candidate_role,
    }
    conversation_id: str | None = None

    try:
        fit_prompt = _build_stage_prompt("fit_analysis", prompts["fit_analysis"], format_context)
        fit_analysis, conversation_id = await _ask_validated_stage(
            client,
            notebook_id,
            "fit_analysis",
            fit_prompt,
            conversation_id=conversation_id,
            source_ids=source_ids,
        )
    except Exception as exc:
        raise InterviewPromptError("fit_analysis", exc) from exc
    fit_analysis_path = output_dir / STAGE_OUTPUT_FILES["fit_analysis"]
    fit_analysis_path.write_text(fit_analysis, encoding="utf-8")

    try:
        gaps_prompt = _build_stage_prompt(
            "information_gaps",
            prompts["information_gaps"],
            format_context,
            fit_analysis=fit_analysis,
        )
        information_gaps, conversation_id = await _ask_validated_stage(
            client,
            notebook_id,
            "information_gaps",
            gaps_prompt,
            conversation_id=conversation_id,
            source_ids=source_ids,
        )
    except Exception as exc:
        raise InterviewPromptError("information_gaps", exc) from exc
    information_gaps_path = output_dir / STAGE_OUTPUT_FILES["information_gaps"]
    information_gaps_path.write_text(information_gaps, encoding="utf-8")

    try:
        template_prompt = _build_stage_prompt(
            "interview_template",
            prompts["interview_template"],
            format_context,
            fit_analysis=fit_analysis,
            information_gaps=information_gaps,
        )
        interview_template, _ = await _ask_validated_stage(
            client,
            notebook_id,
            "interview_template",
            template_prompt,
            conversation_id=conversation_id,
            source_ids=source_ids,
        )
    except Exception as exc:
        raise InterviewPromptError("interview_template", exc) from exc
    interview_template_path = output_dir / STAGE_OUTPUT_FILES["interview_template"]
    interview_template_path.write_text(interview_template, encoding="utf-8")

    return InterviewPromptOutput(
        fit_analysis_path=fit_analysis_path,
        information_gaps_path=information_gaps_path,
        interview_template_path=interview_template_path,
    )


async def _ask_validated_stage(
    client: NotebookLMClient,
    notebook_id: str,
    stage: str,
    prompt: str,
    *,
    conversation_id: str | None,
    source_ids: list[str] | None,
) -> tuple[str, str | None]:
    last_error: StageOutputValidationError | None = None
    current_conversation_id = conversation_id
    for attempt in range(MAX_STAGE_VALIDATION_ATTEMPTS):
        answer, next_conversation_id = await ask_notebook(
            client,
            notebook_id,
            prompt if attempt == 0 else _retry_prompt(stage, prompt, last_error),
            conversation_id=current_conversation_id,
            source_ids=source_ids,
        )
        current_conversation_id = next_conversation_id
        try:
            return validate_stage_output(stage, answer), next_conversation_id
        except StageOutputValidationError as exc:
            last_error = exc
    raise last_error or StageOutputValidationError(f"{stage} output validation failed")


def validate_stage_output(stage: str, answer: str) -> str:
    """Validate a stage response and return Markdown with the completion marker removed."""
    marker = STAGE_DONE_MARKERS[stage]
    if marker not in answer:
        raise StageOutputValidationError(f"missing completion marker {marker}")

    content = answer.replace(marker, "").strip()
    if not content:
        raise StageOutputValidationError("empty stage output")

    if stage == "information_gaps":
        _validate_information_gaps(content)
    return content


def _validate_information_gaps(content: str) -> None:
    required_groups = {
        "信息缺口": ("信息缺口",),
        "矛盾": ("矛盾", "冲突", "不一致"),
        "可疑/造假/包装": ("可疑", "造假", "包装", "夸大"),
        "真实性/重点考察": ("真实性", "重点考察", "进一步考察", "核实", "验证"),
    }
    missing = [
        name
        for name, keywords in required_groups.items()
        if not any(keyword in content for keyword in keywords)
    ]
    if missing:
        raise StageOutputValidationError(
            "information_gaps missing required sections: " + ", ".join(missing)
        )


def _build_stage_prompt(
    stage: str,
    prompt_template: str,
    format_context: dict[str, str],
    *,
    fit_analysis: str | None = None,
    information_gaps: str | None = None,
) -> str:
    prompt_parts = [prompt_template.format(**format_context).strip()]
    if fit_analysis is not None:
        prompt_parts.append("以下是上一阶段已校验的适配分析，请以此为准继续分析：\n\n" + fit_analysis)
    if information_gaps is not None:
        prompt_parts.append("以下是上一阶段已校验的信息缺口分析，请以此为准继续设计模板：\n\n" + information_gaps)
    prompt_parts.append(
        "请完整回答。回答末尾必须单独追加以下结束标记，不要改写或省略："
        f"\n{STAGE_DONE_MARKERS[stage]}"
    )
    return "\n\n".join(prompt_parts)


def _retry_prompt(stage: str, prompt: str, last_error: StageOutputValidationError | None) -> str:
    reason = str(last_error) if last_error else "stage output validation failed"
    return (
        f"{prompt}\n\n上一次回答未通过完整性校验：{reason}。\n"
        "请重新输出该阶段的完整 Markdown，并确保覆盖所有要求，末尾保留指定结束标记。"
    )


async def run_interview_workflow(
    client: NotebookLMClient,
    *,
    candidate_cv_path: Path,
    candidate_name: str,
    output_dir: Path,
    notebook_id: str | None = None,
    requirement_paths: list[Path] | None = None,
    candidate_role: str = "候选人",
    workflow_config_path: Path | None = None,
    delete_candidate_source_after_run: bool = True,
) -> InterviewWorkflowResult:
    """Run the three-step NotebookLM interview review workflow."""
    if notebook_id is None and not requirement_paths:
        raise ValueError("requirement_paths is required when notebook_id is not provided")

    load_workflow_config(workflow_config_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    active_notebook_id = notebook_id or await create_notebook(
        client,
        f"CV review - {candidate_name} - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    )

    if notebook_id is None:
        for requirement_path in requirement_paths or []:
            await add_source(client, active_notebook_id, requirement_path, wait=True)

    candidate_source_id: str | None = None
    candidate_source_deleted = False
    try:
        candidate_source_id = await add_source(
            client,
            active_notebook_id,
            candidate_cv_path,
            wait=True,
        )
        await run_interview_prompts(
            client,
            notebook_id=active_notebook_id,
            candidate_name=candidate_name,
            candidate_role=candidate_role,
            output_dir=output_dir,
            workflow_config_path=workflow_config_path,
            source_ids=None,
        )
    finally:
        if delete_candidate_source_after_run and candidate_source_id:
            try:
                candidate_source_deleted = await delete_source(
                    client,
                    active_notebook_id,
                    candidate_source_id,
                )
            except Exception:
                candidate_source_deleted = False

    return InterviewWorkflowResult(
        notebook_id=active_notebook_id,
        output_dir=output_dir,
        fit_analysis_path=output_dir / "01_fit_analysis.md",
        information_gaps_path=output_dir / "02_information_gaps.md",
        interview_template_path=output_dir / "03_interview_template.md",
        candidate_source_id=candidate_source_id,
        candidate_source_deleted=candidate_source_deleted,
    )

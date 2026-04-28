from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from notebooklm import NotebookLMClient

from .client import add_source, ask_notebook, create_notebook, delete_source
from .config import load_workflow_config


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
        fit_analysis, conversation_id = await ask_notebook(
            client,
            notebook_id,
            prompts["fit_analysis"].format(**format_context),
            conversation_id=conversation_id,
            source_ids=source_ids,
        )
    except Exception as exc:
        raise InterviewPromptError("fit_analysis", exc) from exc
    fit_analysis_path = output_dir / "01_fit_analysis.md"
    fit_analysis_path.write_text(fit_analysis, encoding="utf-8")

    try:
        information_gaps, conversation_id = await ask_notebook(
            client,
            notebook_id,
            prompts["information_gaps"].format(**format_context),
            conversation_id=conversation_id,
            source_ids=source_ids,
        )
    except Exception as exc:
        raise InterviewPromptError("information_gaps", exc) from exc
    information_gaps_path = output_dir / "02_information_gaps.md"
    information_gaps_path.write_text(information_gaps, encoding="utf-8")

    try:
        interview_template, _ = await ask_notebook(
            client,
            notebook_id,
            prompts["interview_template"].format(**format_context),
            conversation_id=conversation_id,
            source_ids=source_ids,
        )
    except Exception as exc:
        raise InterviewPromptError("interview_template", exc) from exc
    interview_template_path = output_dir / "03_interview_template.md"
    interview_template_path.write_text(interview_template, encoding="utf-8")

    return InterviewPromptOutput(
        fit_analysis_path=fit_analysis_path,
        information_gaps_path=information_gaps_path,
        interview_template_path=interview_template_path,
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

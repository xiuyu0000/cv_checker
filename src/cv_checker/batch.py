import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from notebooklm import NotebookLMClient

from .client import add_source, ask_notebook, create_notebook, delete_source
from .config import load_workflow_config
from .workflow import InterviewPromptError, run_interview_prompts


DEFAULT_NOTEBOOK_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default_notebook.yaml"
DEFAULT_SOURCE_DIR = Path(__file__).resolve().parents[2] / "data" / "sources"
DEFAULT_BATCH_CV_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "cv"
DEFAULT_BATCH_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "outputs" / "interview_batch"
DEFAULT_PROCESSED_DIR = DEFAULT_BATCH_CV_DIR / "processed"
DEFAULT_NOTEBOOK_TITLE = "CV Checker Interview Batch"

CANDIDATE_PROFILE_PROMPT = """
请只基于当前 Notebook 中刚上传的候选人简历，提取候选人必要信息。

只返回一个 JSON 对象，不要输出 Markdown、解释或代码块。JSON 字段如下：

{
  "candidate_name": "候选人姓名；无法可靠判断时为空字符串",
  "candidate_role": "候选人的当前角色、目标岗位或最贴近的岗位标签；无法判断时为空字符串",
  "summary": "不超过 120 字的候选人画像；无法判断时为空字符串",
  "confidence": "high、medium、low 或简短说明"
}

不要把文件名当作候选人姓名的首选来源。
""".strip()


@dataclass(frozen=True)
class DefaultNotebook:
    """Default NotebookLM notebook metadata used by batch runs."""

    notebook_id: str
    title: str
    config_path: Path
    source_dir: Path
    source_count: int
    source_ids: list[str]


@dataclass(frozen=True)
class CandidateProfile:
    """Candidate metadata extracted from the uploaded CV source."""

    candidate_name: str
    candidate_role: str
    summary: str
    confidence: str
    raw_response: str


@dataclass(frozen=True)
class BatchItemResult:
    """Result for one candidate CV in a batch run."""

    cv_path: Path
    status: str
    candidate_name: str | None
    candidate_role: str | None
    notebook_id: str
    candidate_source_id: str | None
    candidate_source_deleted: bool
    output_dir: Path | None
    processed_path: Path | None
    error: str | None
    failure_stage: str | None


@dataclass(frozen=True)
class BatchWorkflowResult:
    """Summary for a batch interview workflow run."""

    notebook_id: str
    manifest_path: Path
    processed_count: int
    failed_count: int
    skipped_count: int
    results: list[BatchItemResult]


def iter_source_files(source_dir: Path) -> list[Path]:
    """Return stable, non-hidden source files from a directory."""
    if not source_dir.exists():
        return []
    return sorted(
        path
        for path in source_dir.iterdir()
        if path.is_file() and not path.name.startswith(".")
    )


def iter_cv_files(cv_dir: Path) -> list[Path]:
    """Return stable, non-hidden candidate CV files from a directory."""
    if not cv_dir.exists():
        return []
    return sorted(
        path
        for path in cv_dir.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.name != ".DS_Store"
    )


async def ensure_default_notebook(
    client: NotebookLMClient,
    *,
    notebook_id: str | None = None,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    config_path: Path = DEFAULT_NOTEBOOK_CONFIG_PATH,
    title: str = DEFAULT_NOTEBOOK_TITLE,
) -> DefaultNotebook:
    """Create, load, or update the default NotebookLM notebook metadata."""
    now = _now_iso()
    source_dir = source_dir.resolve()
    config_path = config_path.resolve()

    config = _load_notebook_config(config_path)
    active_notebook_id = notebook_id or _notebook_id_from_config(config)
    active_title = _notebook_title_from_config(config) or title

    if active_notebook_id is None:
        source_files = iter_source_files(source_dir)
        if not source_files:
            raise ValueError(f"No source files found in source directory: {source_dir}")
        active_notebook_id = await create_notebook(client, title)
        active_title = title
        config = {
            "notebook": {
                "id": active_notebook_id,
                "title": active_title,
            },
            "source_dir": str(source_dir),
            "sources": [],
            "created_at": now,
            "updated_at": now,
        }

    recorded_sources = _recorded_sources(config)
    uploaded_sources = dict(recorded_sources)
    source_files = iter_source_files(source_dir)
    for source_path in source_files:
        source_key = str(source_path)
        if source_key in uploaded_sources:
            continue
        source_id = await add_source(client, active_notebook_id, source_path, wait=True)
        uploaded_sources[source_key] = source_id

    if notebook_id or config_path.exists() or uploaded_sources:
        created_at = config.get("created_at") or now
        _write_notebook_config(
            config_path,
            {
                "notebook": {
                    "id": active_notebook_id,
                    "title": active_title,
                },
                "source_dir": str(source_dir),
                "sources": [
                    {"path": path, "id": source_id}
                    for path, source_id in sorted(uploaded_sources.items())
                ],
                "created_at": created_at,
                "updated_at": now,
            },
        )

    return DefaultNotebook(
        notebook_id=active_notebook_id,
        title=active_title,
        config_path=config_path,
        source_dir=source_dir,
        source_count=len(uploaded_sources),
        source_ids=[source_id for _, source_id in sorted(uploaded_sources.items())],
    )


async def extract_candidate_profile(
    client: NotebookLMClient,
    *,
    notebook_id: str,
    source_ids: list[str],
) -> CandidateProfile:
    """Extract candidate metadata from the currently uploaded candidate source."""
    answer, _ = await ask_notebook(
        client,
        notebook_id,
        CANDIDATE_PROFILE_PROMPT,
        source_ids=source_ids,
    )
    data = _parse_json_object(answer)
    return CandidateProfile(
        candidate_name=str(data.get("candidate_name") or "").strip(),
        candidate_role=str(data.get("candidate_role") or "").strip(),
        summary=str(data.get("summary") or "").strip(),
        confidence=str(data.get("confidence") or "").strip(),
        raw_response=answer,
    )


async def run_batch_interview_workflow(
    client: NotebookLMClient,
    *,
    cv_dir: Path = DEFAULT_BATCH_CV_DIR,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    default_notebook_config_path: Path = DEFAULT_NOTEBOOK_CONFIG_PATH,
    output_dir: Path = DEFAULT_BATCH_OUTPUT_DIR,
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    notebook_id: str | None = None,
    workflow_config_path: Path | None = None,
) -> BatchWorkflowResult:
    """Process each CV in a directory through the NotebookLM interview workflow."""
    load_workflow_config(workflow_config_path)
    notebook = await ensure_default_notebook(
        client,
        notebook_id=notebook_id,
        source_dir=source_dir,
        config_path=default_notebook_config_path,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "run_manifest.jsonl"
    cv_files = iter_cv_files(cv_dir)
    results: list[BatchItemResult] = []

    for cv_path in cv_files:
        await cleanup_unrecorded_sources(client, notebook.notebook_id, notebook.source_ids)
        result = await _process_cv(
            client,
            notebook_id=notebook.notebook_id,
            default_source_ids=notebook.source_ids,
            cv_path=cv_path,
            output_dir=output_dir,
            processed_dir=processed_dir,
            manifest_path=manifest_path,
            workflow_config_path=workflow_config_path,
        )
        results.append(result)

    return BatchWorkflowResult(
        notebook_id=notebook.notebook_id,
        manifest_path=manifest_path,
        processed_count=sum(1 for result in results if result.status == "success"),
        failed_count=sum(1 for result in results if result.status == "failed"),
        skipped_count=0 if cv_files else 1,
        results=results,
    )


async def _process_cv(
    client: NotebookLMClient,
    *,
    notebook_id: str,
    cv_path: Path,
    default_source_ids: list[str],
    output_dir: Path,
    processed_dir: Path,
    manifest_path: Path,
    workflow_config_path: Path | None,
) -> BatchItemResult:
    started_at = _now_iso()
    candidate_source_id: str | None = None
    candidate_source_deleted = False
    profile: CandidateProfile | None = None
    candidate_output_dir: Path | None = None
    processed_path: Path | None = None
    status = "failed"
    error: str | None = None
    failure_stage: str | None = None

    try:
        failure_stage = "add_source"
        candidate_source_id = await add_source(client, notebook_id, cv_path, wait=True)
        active_source_ids = [*default_source_ids, candidate_source_id]

        failure_stage = "profile"
        profile = await extract_candidate_profile(
            client,
            notebook_id=notebook_id,
            source_ids=[candidate_source_id],
        )
        if not profile.candidate_name:
            fallback_name = candidate_name_from_filename(cv_path)
            if fallback_name:
                profile = CandidateProfile(
                    candidate_name=fallback_name,
                    candidate_role=profile.candidate_role,
                    summary=profile.summary,
                    confidence="filename_fallback",
                    raw_response=profile.raw_response,
                )
            else:
                error = "candidate_name could not be extracted"
                return _record_result(
                    manifest_path,
                    cv_path=cv_path,
                    status=status,
                    candidate_name=None,
                    candidate_role=None,
                    notebook_id=notebook_id,
                    candidate_source_id=candidate_source_id,
                    candidate_source_deleted=await _delete_candidate_source(
                        client,
                        notebook_id,
                        candidate_source_id,
                    ),
                    output_dir=None,
                    processed_path=None,
                    error=error,
                    failure_stage=failure_stage,
                    started_at=started_at,
                )

        candidate_dir_name = safe_path_segment(profile.candidate_name) or safe_path_segment(cv_path.stem)
        candidate_output_dir = unique_path(output_dir / candidate_dir_name)
        candidate_output_dir.mkdir(parents=True, exist_ok=False)
        _write_candidate_profile(candidate_output_dir / "00_candidate_profile.json", profile)

        failure_stage = "interview_prompts"
        await run_interview_prompts(
            client,
            notebook_id=notebook_id,
            candidate_name=profile.candidate_name,
            candidate_role=profile.candidate_role or "候选人",
            output_dir=candidate_output_dir,
            workflow_config_path=workflow_config_path,
            source_ids=active_source_ids,
        )

        failure_stage = "delete_source"
        candidate_source_deleted = await _delete_candidate_source(
            client,
            notebook_id,
            candidate_source_id,
        )
        if not candidate_source_deleted:
            error = "candidate source deletion failed"
            return _record_result(
                manifest_path,
                cv_path=cv_path,
                status=status,
                candidate_name=profile.candidate_name,
                candidate_role=profile.candidate_role or "候选人",
                notebook_id=notebook_id,
                candidate_source_id=candidate_source_id,
                candidate_source_deleted=False,
                output_dir=candidate_output_dir,
                processed_path=None,
                error=error,
                failure_stage=failure_stage,
                started_at=started_at,
            )

        failure_stage = "move_cv"
        processed_path = unique_path(processed_dir / cv_path.name)
        shutil.move(str(cv_path), str(processed_path))
        status = "success"
        failure_stage = None
    except Exception as exc:
        error = str(exc)
        if isinstance(exc, InterviewPromptError):
            failure_stage = exc.stage
        if candidate_source_id and not candidate_source_deleted:
            candidate_source_deleted = await _delete_candidate_source(
                client,
                notebook_id,
                candidate_source_id,
            )
        elif candidate_source_id is None:
            await cleanup_sources_by_title(
                client,
                notebook_id,
                retained_source_ids=default_source_ids,
                titles={cv_path.name},
            )

    return _record_result(
        manifest_path,
        cv_path=cv_path,
        status=status,
        candidate_name=profile.candidate_name if profile and profile.candidate_name else None,
        candidate_role=profile.candidate_role if profile and profile.candidate_role else None,
        notebook_id=notebook_id,
        candidate_source_id=candidate_source_id,
        candidate_source_deleted=candidate_source_deleted,
        output_dir=candidate_output_dir,
        processed_path=processed_path,
        error=error,
        failure_stage=failure_stage,
        started_at=started_at,
    )


async def cleanup_unrecorded_sources(
    client: NotebookLMClient,
    notebook_id: str,
    retained_source_ids: list[str],
) -> int:
    """Delete notebook sources that are not part of the persisted default sources."""
    return await cleanup_sources_by_title(
        client,
        notebook_id,
        retained_source_ids=retained_source_ids,
        titles=None,
    )


async def cleanup_sources_by_title(
    client: NotebookLMClient,
    notebook_id: str,
    *,
    retained_source_ids: list[str],
    titles: set[str] | None,
) -> int:
    sources_api = getattr(client, "sources", None)
    list_sources = getattr(sources_api, "list", None)
    if list_sources is None:
        return 0

    retained = set(retained_source_ids)
    deleted_count = 0
    sources = await list_sources(notebook_id)
    for source in sources:
        source_id = str(getattr(source, "id", "") or "")
        source_title = str(getattr(source, "title", "") or "")
        if not source_id or source_id in retained:
            continue
        if titles is not None and source_title not in titles:
            continue
        if await _delete_candidate_source(client, notebook_id, source_id):
            deleted_count += 1
    return deleted_count


async def _delete_candidate_source(
    client: NotebookLMClient,
    notebook_id: str,
    source_id: str,
) -> bool:
    try:
        return await delete_source(client, notebook_id, source_id)
    except Exception:
        return False


def safe_path_segment(value: str) -> str:
    """Return a conservative filesystem path segment."""
    normalized = re.sub(r"[\s/\\:]+", "_", value.strip())
    normalized = re.sub(r"[\x00-\x1f]+", "", normalized)
    normalized = normalized.strip("._ ")
    return normalized or "candidate"


def candidate_name_from_filename(path: Path) -> str:
    """Infer a Chinese candidate name from the filename as a last resort."""
    first_token = re.split(r"[\s_\\/-]+", path.stem.strip(), maxsplit=1)[0]
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", first_token):
        return first_token
    return ""


def unique_path(path: Path) -> Path:
    """Return a path with a numeric suffix when the original already exists."""
    if not path.exists():
        return path
    parent = path.parent
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _record_result(
    manifest_path: Path,
    *,
    cv_path: Path,
    status: str,
    candidate_name: str | None,
    candidate_role: str | None,
    notebook_id: str,
    candidate_source_id: str | None,
    candidate_source_deleted: bool,
    output_dir: Path | None,
    processed_path: Path | None,
    error: str | None,
    failure_stage: str | None,
    started_at: str,
) -> BatchItemResult:
    finished_at = _now_iso()
    record = {
        "cv_path": str(cv_path),
        "status": status,
        "candidate_name": candidate_name,
        "candidate_role": candidate_role,
        "notebook_id": notebook_id,
        "candidate_source_id": candidate_source_id,
        "candidate_source_deleted": candidate_source_deleted,
        "output_dir": str(output_dir) if output_dir else None,
        "processed_path": str(processed_path) if processed_path else None,
        "error": error,
        "failure_stage": failure_stage,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "a", encoding="utf-8") as manifest_file:
        manifest_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return BatchItemResult(
        cv_path=cv_path,
        status=status,
        candidate_name=candidate_name,
        candidate_role=candidate_role,
        notebook_id=notebook_id,
        candidate_source_id=candidate_source_id,
        candidate_source_deleted=candidate_source_deleted,
        output_dir=output_dir,
        processed_path=processed_path,
        error=error,
        failure_stage=failure_stage,
    )


def _write_candidate_profile(path: Path, profile: CandidateProfile) -> None:
    path.write_text(
        json.dumps(
            {
                "candidate_name": profile.candidate_name,
                "candidate_role": profile.candidate_role,
                "summary": profile.summary,
                "confidence": profile.confidence,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("Candidate profile response did not contain JSON")
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Candidate profile response must be a JSON object")
    return parsed


def _load_notebook_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as config_file:
        return yaml.safe_load(config_file) or {}


def _write_notebook_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as config_file:
        yaml.safe_dump(config, config_file, allow_unicode=True, sort_keys=False)


def _notebook_id_from_config(config: dict[str, Any]) -> str | None:
    notebook = config.get("notebook") or {}
    notebook_id = notebook.get("id")
    return str(notebook_id).strip() if notebook_id else None


def _notebook_title_from_config(config: dict[str, Any]) -> str | None:
    notebook = config.get("notebook") or {}
    title = notebook.get("title")
    return str(title).strip() if title else None


def _recorded_sources(config: dict[str, Any]) -> dict[str, str]:
    sources = config.get("sources") or []
    recorded: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        path = source.get("path")
        source_id = source.get("id")
        if path and source_id:
            recorded[str(Path(path).resolve())] = str(source_id)
    return recorded


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

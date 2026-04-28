import argparse
import asyncio
from pathlib import Path

from src.cv_checker.batch import run_batch_interview_workflow
from src.cv_checker.checker import check_cv
from src.cv_checker.client import create_client
from src.cv_checker.workflow import run_interview_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description="CV Checker - Automated resume review using NotebookLM")
    parser.add_argument("--notebook-id", help="NotebookLM notebook ID")
    parser.add_argument("--cv", type=Path, help="Legacy single-prompt CV path")
    parser.add_argument(
        "--batch-cv-dir",
        type=Path,
        help="Directory containing candidate CV files for batch interview workflow",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("data") / "sources",
        help="Directory containing persistent NotebookLM source files for batch mode",
    )
    parser.add_argument(
        "--default-notebook-config",
        type=Path,
        default=Path("config") / "default_notebook.yaml",
        help="Path to default NotebookLM notebook metadata for batch mode",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=Path("data") / "raw" / "cv" / "processed",
        help="Directory where successfully processed CV files are moved in batch mode",
    )
    parser.add_argument("--candidate-cv", type=Path, help="Candidate CV file for interview workflow")
    parser.add_argument("--candidate-name", help="Candidate name for interview workflow")
    parser.add_argument("--candidate-role", default="候选人", help="Candidate role label used in prompts")
    parser.add_argument(
        "--requirement",
        action="append",
        type=Path,
        default=[],
        help="Requirement/background source file. Required when creating a new notebook.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for workflow Markdown outputs",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to custom prompt YAML config")
    parser.add_argument(
        "--keep-candidate-source",
        action="store_true",
        help="Keep the uploaded candidate CV source after workflow completion",
    )
    args = parser.parse_args()

    if args.batch_cv_dir:
        if not args.batch_cv_dir.exists():
            parser.error(f"Batch CV directory not found: {args.batch_cv_dir}")
        if not args.batch_cv_dir.is_dir():
            parser.error(f"Batch CV path is not a directory: {args.batch_cv_dir}")
        if not args.source_dir.exists() and not args.notebook_id and not args.default_notebook_config.exists():
            parser.error(f"Source directory not found: {args.source_dir}")
    elif args.candidate_cv:
        if not args.candidate_cv.exists():
            parser.error(f"Candidate CV file not found: {args.candidate_cv}")
        if not args.candidate_name:
            parser.error("--candidate-name is required with --candidate-cv")
        if not args.notebook_id and not args.requirement:
            parser.error("--requirement is required when --notebook-id is omitted")
        for requirement in args.requirement:
            if not requirement.exists():
                parser.error(f"Requirement file not found: {requirement}")
    else:
        if not args.notebook_id:
            parser.error("--notebook-id is required unless --candidate-cv is used with --requirement")
        if not args.cv:
            parser.error("--cv is required for legacy single-prompt mode")
        if not args.cv.exists():
            parser.error(f"CV file not found: {args.cv}")

    result = asyncio.run(_run(args))
    print(result)


async def _run(args: argparse.Namespace) -> str:
    raw_client = await create_client()
    async with raw_client as client:
        return await _run_with_client(args, client)


async def _run_with_client(args: argparse.Namespace, client: object) -> str:
    if args.batch_cv_dir:
        result = await run_batch_interview_workflow(
            client,
            cv_dir=args.batch_cv_dir,
            source_dir=args.source_dir,
            default_notebook_config_path=args.default_notebook_config,
            output_dir=args.output_dir or Path("outputs") / "interview_batch",
            processed_dir=args.processed_dir,
            notebook_id=args.notebook_id,
            workflow_config_path=args.config,
        )
        if not result.results:
            return (
                "Batch interview workflow completed.\n"
                f"Notebook ID: {result.notebook_id}\n"
                f"Manifest: {result.manifest_path}\n"
                "No pending CV files found."
            )
        return (
            "Batch interview workflow completed.\n"
            f"Notebook ID: {result.notebook_id}\n"
            f"Manifest: {result.manifest_path}\n"
            f"Processed: {result.processed_count}\n"
            f"Failed: {result.failed_count}"
        )
    if args.candidate_cv:
        result = await run_interview_workflow(
            client,
            notebook_id=args.notebook_id,
            candidate_cv_path=args.candidate_cv,
            candidate_name=args.candidate_name,
            candidate_role=args.candidate_role,
            requirement_paths=args.requirement,
            output_dir=args.output_dir or Path("outputs") / "interview_review",
            workflow_config_path=args.config,
            delete_candidate_source_after_run=not args.keep_candidate_source,
        )
        deletion_status = "deleted" if result.candidate_source_deleted else "kept"
        return (
            f"Interview workflow completed.\n"
            f"Notebook ID: {result.notebook_id}\n"
            f"Output directory: {result.output_dir}\n"
            f"Candidate source: {deletion_status}\n"
            f"- {result.fit_analysis_path}\n"
            f"- {result.information_gaps_path}\n"
            f"- {result.interview_template_path}"
        )
    return await check_cv(client, args.notebook_id, args.cv, args.config)


if __name__ == "__main__":
    main()

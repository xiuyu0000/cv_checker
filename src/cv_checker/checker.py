from pathlib import Path

from notebooklm import NotebookLMClient

from .client import add_source, ask_notebook
from .config import load_prompt_config


async def check_cv(
    client: NotebookLMClient,
    notebook_id: str,
    cv_path: Path,
    config_path: Path | None = None,
) -> str:
    """Check a CV by uploading it to NotebookLM and running a review prompt.

    Args:
        client: Authenticated NotebookLM client.
        notebook_id: The target notebook ID.
        cv_path: Path to the CV file (PDF, etc.).
        config_path: Optional path to a custom prompt YAML config.

    Returns:
        The review result text from NotebookLM.
    """
    config = load_prompt_config(config_path)
    review_prompt = config.get("review_prompt", "")
    if not review_prompt:
        raise ValueError("review_prompt is empty in config file")

    # Upload the CV as a source
    await add_source(client, notebook_id, cv_path)

    # Ask NotebookLM to review the CV
    result, _ = await ask_notebook(client, notebook_id, review_prompt)
    return result

import asyncio
from pathlib import Path

from notebooklm import NotebookLMClient


async def create_client(timeout: float = 180.0) -> NotebookLMClient:
    """Create and return an authenticated NotebookLM client."""
    client = await NotebookLMClient.from_storage(timeout=timeout)
    return client


async def create_notebook(client: NotebookLMClient, title: str) -> str:
    """Create a NotebookLM notebook and return its ID."""
    result = await client.notebooks.create(title)
    return result.id


async def add_source(
    client: NotebookLMClient,
    notebook_id: str,
    file_path: Path,
    *,
    wait: bool = False,
    wait_timeout: float = 300.0,
    attempts: int = 3,
) -> str:
    """Add a file as a source to the specified notebook. Returns the source ID."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = await client.sources.add_file(
                notebook_id,
                file_path,
                wait=wait,
                wait_timeout=wait_timeout,
            )
            return result.id
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                break
            await asyncio.sleep(5 * attempt)
    raise last_error or RuntimeError(f"Failed to add source: {file_path}")


async def delete_source(client: NotebookLMClient, notebook_id: str, source_id: str) -> bool:
    """Delete a source from the specified notebook."""
    return await client.sources.delete(notebook_id, source_id)


async def ask_notebook(
    client: NotebookLMClient,
    notebook_id: str,
    prompt: str,
    *,
    conversation_id: str | None = None,
    source_ids: list[str] | None = None,
    attempts: int = 3,
) -> tuple[str, str]:
    """Ask a question to the notebook and return the response text."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = await client.chat.ask(
                notebook_id,
                prompt,
                source_ids=source_ids,
                conversation_id=conversation_id,
            )
            return result.answer, result.conversation_id
        except Exception as exc:
            last_error = exc
            if attempt == attempts or not _is_retryable_chat_error(exc):
                break
            await asyncio.sleep(10 * attempt)
    raise last_error or RuntimeError("Failed to ask NotebookLM")


def _is_retryable_chat_error(error: Exception) -> bool:
    message = str(error).lower()
    retryable_fragments = (
        "timed out",
        "timeout",
        "server disconnected",
        "connection",
        "rate limit",
        "temporarily",
    )
    return any(fragment in message for fragment in retryable_fragments)

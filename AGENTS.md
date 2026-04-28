# Repository Guidelines

## Project Structure & Module Organization

- `main.py` contains the CLI entry point and argument parsing.
- `src/cv_checker/` contains the package code.
- `src/cv_checker/client.py` wraps NotebookLM client operations.
- `src/cv_checker/checker.py` coordinates upload and review execution.
- `src/cv_checker/config.py` loads YAML prompt configuration.
- `config/prompt.yaml` stores the default review prompt.
- `uv.lock` and `pyproject.toml` define the Python environment and dependencies.

There is no `tests/` directory yet. Add tests under `tests/` for behavior that can be validated without real NotebookLM browser access.

## Build, Test, and Development Commands

- `uv sync` installs dependencies into the project environment from `pyproject.toml` and `uv.lock`.
- `uv run python main.py --notebook-id <id> --cv path/to/cv.pdf` runs the CV checker with the default prompt.
- `uv run python main.py --notebook-id <id> --cv path/to/cv.pdf --config config/prompt.yaml` runs with an explicit prompt config.
- `uv run python -m compileall main.py src` performs a lightweight syntax check.

The project requires Python `>=3.14`. NotebookLM authentication is loaded through `NotebookLMClient.from_storage()`, so local browser/session setup must exist before CLI runs.

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation, type hints for public functions, and concise docstrings for external-service modules. Keep async boundaries explicit: functions that call NotebookLM should remain `async` and be awaited by callers.

Use `snake_case` for modules, functions, and variables. Prefer small wrappers around third-party calls, as in `client.py`, so orchestration code can stay testable.

## Testing Guidelines

No testing framework is configured yet. When adding tests, use `pytest` and name files `tests/test_*.py`. Mock `NotebookLMClient` instead of calling the live service. Focus initial coverage on config loading, CLI validation, and `check_cv` orchestration.

Until tests exist, run `uv run python -m compileall main.py src` before submitting changes.

## Commit & Pull Request Guidelines

This repository has no existing commit history, so no local convention is established. Use short, imperative commit messages such as `Add prompt config validation` or `Mock NotebookLM client in tests`.

Pull requests should include a concise description, tests performed, and any NotebookLM/browser setup required for verification. For prompt changes in `config/prompt.yaml`, summarize the behavioral intent and include sample before/after output if available.

## Security & Configuration Tips

Do not commit CV files, browser session data, credentials, or generated NotebookLM artifacts. Keep prompt files in `config/`, and pass custom prompts with `--config` instead of hard-coding review text in Python modules.

from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "prompt.yaml"
DEFAULT_WORKFLOW_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "interview_workflow.yaml"
)


def load_prompt_config(config_path: Path | None = None) -> dict:
    """Load review prompt configuration from a YAML file."""
    path = config_path or DEFAULT_CONFIG_PATH
    return load_yaml_config(path)


def load_workflow_config(config_path: Path | None = None) -> dict:
    """Load interview workflow configuration from a YAML file."""
    path = config_path or DEFAULT_WORKFLOW_CONFIG_PATH
    config = load_yaml_config(path)
    prompts = config.get("workflow", {}).get("prompts", {})
    required_prompts = ("fit_analysis", "information_gaps", "interview_template")
    missing = [name for name in required_prompts if not prompts.get(name)]
    if missing:
        raise ValueError(f"Missing workflow prompts: {', '.join(missing)}")
    return config


def load_yaml_config(path: Path) -> dict:
    """Load a YAML config file and return a dictionary."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path) as f:
        config = yaml.safe_load(f)
    return config or {}

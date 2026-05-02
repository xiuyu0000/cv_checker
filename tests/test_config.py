import tempfile
import unittest
from pathlib import Path

from src.cv_checker.config import load_prompt_config, load_yaml_config


class ConfigTests(unittest.TestCase):
    def test_load_yaml_config_rejects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "missing.yaml"

            with self.assertRaisesRegex(FileNotFoundError, "Config file not found"):
                load_yaml_config(missing_path)

    def test_load_yaml_config_returns_empty_dict_for_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "empty.yaml"
            config_path.write_text("", encoding="utf-8")

            self.assertEqual(load_yaml_config(config_path), {})

    def test_load_prompt_config_reads_review_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "prompt.yaml"
            config_path.write_text("review_prompt: Check this CV\n", encoding="utf-8")

            self.assertEqual(load_prompt_config(config_path)["review_prompt"], "Check this CV")

import io
import subprocess
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path

from scripts.bootstrap_config import (
    REQUIRED_KEYS,
    missing_required,
    parse_env_text,
    update_env_file,
    shell_exports,
    configure,
    persist_action_token,
)


class StartupBootstrapTests(unittest.TestCase):
    def test_startup_sources_rtk_after_loading_project_env(self):
        root = Path(__file__).resolve().parents[1]
        startup = (root / "start.sh").read_text(encoding="utf-8")
        env_loader = startup.index('done < "$ROOT/.env"')
        rtk_setup = startup.index('. "$ROOT/scripts/setup_rtk.sh"')
        rtk_call = startup.index('kwee_setup_rtk', rtk_setup)
        python_setup = startup.index('PY="${PY:-python3}"')
        self.assertLess(env_loader, rtk_setup)
        self.assertLess(rtk_setup, rtk_call)
        self.assertLess(rtk_call, python_setup)

    def test_blank_and_placeholder_values_are_reported_as_missing(self):
        values = {
            "LLM_BASE_URL": "https://gateway.contoh.com/v1",
            "LLM_API_KEY": "isi-kunci-di-sini",
            "LLM_MODEL": "",
            "WA_OWNER_NUMBERS": "628111111111",
        }
        self.assertEqual(missing_required(values), list(REQUIRED_KEYS))

    def test_env_parser_preserves_equals_and_supports_quoted_values(self):
        values = parse_env_text(
            '# comment\nLLM_API_KEY="abc=def"\nLLM_MODEL=vendor/model\nexport WA_OWNER_NUMBERS=628123\n'
        )
        self.assertEqual(values["LLM_API_KEY"], "abc=def")
        self.assertEqual(values["LLM_MODEL"], "vendor/model")
        self.assertEqual(values["WA_OWNER_NUMBERS"], "628123")

    def test_update_is_atomic_preserves_comments_and_locks_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("# keep me\nLLM_MODEL=old-model\nCUSTOM_FLAG=yes\n", encoding="utf-8")
            update_env_file(path, {"LLM_MODEL": "new/model", "LLM_API_KEY": "secret=token"})
            text = path.read_text(encoding="utf-8")
            self.assertIn("# keep me", text)
            self.assertIn("CUSTOM_FLAG=yes", text)
            self.assertIn("LLM_MODEL=new/model", text)
            self.assertIn("LLM_API_KEY=secret=token", text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_action_token_persistence_replaces_existing_key_without_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("ACTION_TOKEN=\nCUSTOM_FLAG=yes\nACTION_TOKEN=old-token\n", encoding="utf-8")
            persist_action_token(path, "new-token")
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("ACTION_TOKEN=")]
            self.assertEqual(lines, ["ACTION_TOKEN=new-token"])
            self.assertIn("CUSTOM_FLAG=yes", path.read_text(encoding="utf-8"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_shell_exports_are_safe_to_eval_even_with_shell_metacharacters(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "executed"
            secret = f"abc'; touch {marker}; #"
            script = shell_exports({"LLM_API_KEY": secret}) + 'printf "%s" "$LLM_API_KEY"\n'
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, secret)
            self.assertFalse(marker.exists())


    def test_interactive_first_run_saves_api_key_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("LLM_BASE_URL=\nLLM_API_KEY=\nLLM_MODEL=\nWA_OWNER_NUMBERS=\n", encoding="utf-8")
            answers = io.StringIO("\nacme/model\n628123456789\n")
            diagnostics = io.StringIO()
            with patch("sys.stdin", answers), patch("sys.stderr", diagnostics), patch(
                "scripts.bootstrap_config.getpass.getpass", side_effect=["sk-test-secret", ""]
            ):
                exports = configure(path, env={}, interactive=True)
            self.assertEqual(exports["LLM_API_KEY"], "sk-test-secret")
            self.assertIn("LLM_API_KEY=sk-test-secret", path.read_text(encoding="utf-8"))
            self.assertNotIn("sk-test-secret", diagnostics.getvalue())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_first_run_template_has_no_dummy_credentials(self):
        template = Path(__file__).resolve().parents[1] / ".env.example"
        values = parse_env_text(template.read_text(encoding="utf-8"))
        self.assertEqual(missing_required(values), list(REQUIRED_KEYS))
        self.assertEqual(values.get("DEFAULT_MECHANISM"), "native")
        self.assertEqual(values.get("KWEE_CANVAS"), "0")


if __name__ == "__main__":
    unittest.main()

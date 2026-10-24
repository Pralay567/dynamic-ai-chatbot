"""Regression guard: the exposed API key must never come back into the project."""

import re
import tempfile
import unittest
from pathlib import Path

from helpers import FAKE_KEY, ROOT

KEY_RE = re.compile(rb"AIza[0-9A-Za-z_\-]{20,}")
SKIP_DIRS = {".git", ".venv", "venv", "logs", "node_modules", ".idea", ".vscode"}
SKIP_FILES = {".env"}  # the real, git-ignored secrets file is allowed to hold a key


def find_key_leaks(root: Path):
    leaks = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name in SKIP_FILES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.stat().st_size > 5_000_000:
            continue
        if KEY_RE.search(path.read_bytes()):
            leaks.append(str(path.relative_to(root)))
    return leaks


class SecretHygieneTests(unittest.TestCase):
    def test_scanner_detects_a_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "leaky.js").write_text(f"const k = '{FAKE_KEY}';", encoding="utf-8")
            Path(tmp, ".env").write_text(f"GEMINI_API_KEY={FAKE_KEY}", encoding="utf-8")
            self.assertEqual(find_key_leaks(Path(tmp)), ["leaky.js"])

    def test_project_contains_no_google_api_key(self):
        self.assertEqual(find_key_leaks(ROOT), [])

    def test_env_files_are_git_ignored_but_example_is_not(self):
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", lines)
        self.assertIn("!.env.example", lines)
        self.assertIn("logs/", lines)

    def test_env_example_has_no_filled_in_secret(self):
        values = {}
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, _, value = line.partition("=")
                values[name.strip()] = value.strip()
        self.assertEqual(values["GEMINI_API_KEY"], "")
        self.assertEqual(values["GEMINI_MODEL"], "")  # no unverified model name shipped

    def test_frontend_has_no_key_or_hardcoded_model(self):
        for name in ("app.js", "index.html"):
            text = (ROOT / "static" / name).read_text(encoding="utf-8")
            self.assertNotIn("apiKey", text)
            self.assertNotIn("gemini-1.5", text)

    def test_no_url_embedded_key_pattern_in_live_python_sources(self):
        # script*.py are stale generator scripts that only hold OLD code inside strings
        # (they are kept for now; see the stage report). Live modules must not put the
        # key in a URL.
        for path in ROOT.glob("*.py"):
            if path.name.startswith("script"):
                continue
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"\?key=\{", path.name)


if __name__ == "__main__":
    unittest.main()

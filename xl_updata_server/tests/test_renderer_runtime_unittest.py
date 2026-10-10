import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_app.character_card_renderer import render_character_cards_batch
from server_app.character_cards import normalize_character_record


class RendererRuntimeDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.renderer = self.root / "renderer"
        self.renderer.mkdir()
        (self.renderer / "render_batch.js").write_text("// test renderer\n", encoding="utf-8")
        self.record = normalize_character_record("10000224", {"name": "雾铃/Mistbell"})

    def test_empty_stdout_preserves_canvas_module_error(self):
        completed = subprocess.CompletedProcess(
            args=["node"],
            returncode=1,
            stdout="",
            stderr="Error: Cannot find module 'canvas'",
        )
        with patch("server_app.character_card_renderer.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "Cannot find module 'canvas'"):
                render_character_cards_batch(
                    (self.record,), self.root / "out", renderer_dir=self.renderer
                )

    def test_renderer_response_without_character_id_is_not_reported_as_unknown(self):
        completed = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=json.dumps({"results": []}),
            stderr="",
        )
        with patch("server_app.character_card_renderer.subprocess.run", return_value=completed):
            result = render_character_cards_batch(
                (self.record,), self.root / "out", renderer_dir=self.renderer
            )[0]

        self.assertIn("10000224", result.error)
        self.assertIn("未返回", result.error)
        self.assertNotEqual(result.error, "未知错误")

import io
import sys
import unittest
from unittest import mock

from rich.text import Text

from ModuleFolders.UserInterface.TaskUI import TaskUI
from PluginScripts.TranslationCheckPlugin.TranslationCheckPlugin import (
    _safe_console_print,
)


class TaskUICriticalDetectionTests(unittest.TestCase):
    def test_game_text_containing_panic_is_not_a_crash(self):
        log_item = Text(
            "[17:29:45] [01-010] Translating: "
            "Exploits the enemy's panic with a merciless pursuit."
        )
        self.assertFalse(TaskUI._is_explicit_critical_log(log_item))

    def test_game_text_containing_traceback_phrase_is_not_a_crash(self):
        log_item = Text(
            "[17:29:45] [01-011] Translating: "
            "Traceback (most recent call last): is dialogue content."
        )
        self.assertFalse(TaskUI._is_explicit_critical_log(log_item))

    def test_structured_traceback_is_a_crash(self):
        log_item = Text(
            "[17:29:45] Traceback (most recent call last):"
        )
        self.assertTrue(TaskUI._is_explicit_critical_log(log_item))

    def test_structured_panic_is_a_crash(self):
        log_item = Text("[17:29:45] panic: worker failed")
        self.assertTrue(TaskUI._is_explicit_critical_log(log_item))

    def test_plugin_report_is_safe_on_gbk_stdout(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="gbk")
        with mock.patch.object(sys, "stdout", stream):
            _safe_console_print("💻 项目运行报告")
            stream.flush()
        output = raw.getvalue().decode("gbk")
        self.assertIn(r"\U0001f4bb", output)
        self.assertIn("项目运行报告", output)


if __name__ == "__main__":
    unittest.main()

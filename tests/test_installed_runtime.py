"""Exercise the registered GUI command and installed CLI on disposable media.

Run after installation: python tests/test_installed_runtime.py
No registry changes or real user media are involved.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import winreg


@unittest.skipUnless(os.environ.get("SHUFFLES_RUN_INSTALLED_TESTS") == "1", "Explicit installed-runtime opt-in required")
class InstalledRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = r"Software\Classes\Directory\Background\shell\ShufflesOrganize\command"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            cls.command = winreg.QueryValueEx(handle, "")[0]
        cls.runtime = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Shuffles"
        cls.environment = os.environ.copy()
        windows = os.environ["SystemRoot"]
        # Reproduce Explorer inheriting an environment without Python on PATH.
        cls.environment["PATH"] = f"{windows}\\System32;{windows}"

    def test_registered_gui_without_python_on_path(self):
        with tempfile.TemporaryDirectory(prefix="shuffles-gui-test-") as directory:
            for name in ("Bob Photos", "Photos & Friends! (test)", "Fotos %TEMP% café"):
                with self.subTest(name=name):
                    target = Path(directory) / name
                    target.mkdir()
                    (target / "first.JPG").write_bytes(b"dummy jpg")
                    (target / "second.PNG").write_bytes(b"dummy png")
                    command = self.command.replace("%V", str(target))
                    process = subprocess.Popen(command, env=self.environment, creationflags=subprocess.CREATE_NO_WINDOW)
                    try:
                        status = process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        subprocess.run(
                            [str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                             "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                        self.fail("Registered GUI launch stalled, possibly on an error dialog")
                    self.assertEqual(status, 0)
                    base = "-".join(name.lower().split())
                    media = {p.name for p in target.iterdir() if p.suffix.lower() in {".jpg", ".png"}}
                    self.assertEqual(media, {f"{base}-1.jpg", f"{base}-1.png"})
                    self.assertFalse(list(target.glob("_temp_shuffles_*")))
                    self.assertFalse((target / ".shuffles.lock").exists())

    def test_installed_batch_quiet_stdout_without_python_on_path(self):
        with tempfile.TemporaryDirectory(prefix="shuffles-cli-test-") as directory:
            target = Path(directory) / "Quiet Photos & Test!"
            target.mkdir()
            (target / "sample.jpg").write_bytes(b"dummy jpg")
            result = subprocess.run(
                [str(self.runtime / "shuffles.bat"), "--quiet", str(target)],
                env=self.environment, capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["success"])
            self.assertEqual(report["renamed"], 1)
            self.assertTrue((target / "quiet-photos-&-test!-1.jpg").exists())

    def test_installed_legacy_command_when_present(self):
        launcher = Path.home() / "Desktop" / "Coding" / ".scripts" / "Shuffles" / "hide_and_run.vbs"
        if not launcher.is_file():
            self.skipTest("No legacy compatibility launcher installed on this machine")
        with tempfile.TemporaryDirectory(prefix="shuffles-legacy-test-") as directory:
            target = Path(directory) / "Legacy Photos & Test!"
            target.mkdir()
            (target / "sample.JPG").write_bytes(b"dummy jpg")
            process = subprocess.Popen(
                [str(Path(os.environ["SystemRoot"]) / "System32" / "wscript.exe"),
                 str(launcher), str(target)], env=self.environment, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                status = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    [str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                     "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                self.fail("Legacy launcher stalled")
            self.assertEqual(status, 0)
            self.assertTrue((target / "legacy-photos-&-test!-1.jpg").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

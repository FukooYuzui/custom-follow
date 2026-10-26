import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from windows_support import (
    SessionError, acquire_instance, load_session, release_instance,
    save_session, startup_command,
)


@unittest.skipUnless(os.name == "nt", "Windows integration")
class WindowsSupportTests(unittest.TestCase):
    def test_session_round_trip_has_no_plaintext_and_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.bin"
            self.assertEqual(load_session(path), {})
            values = {"SESSDATA": "test-secret-should-never-be-written", "DedeUserID": "123"}
            save_session(path, values)
            self.assertNotIn(b"test-secret", path.read_bytes())
            self.assertEqual(load_session(path), values)
            save_session(path, {"SESSDATA": "replacement"})
            self.assertEqual(load_session(path), {"SESSDATA": "replacement"})
            self.assertEqual([item.name for item in Path(directory).iterdir()], ["session.bin"])

    def test_damaged_session_is_not_silently_treated_as_signed_out(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.bin"
            path.write_bytes(b"wrong header")
            with self.assertRaises(SessionError):
                load_session(path)
            save_session(path, {"SESSDATA": "test"})
            stored = bytearray(path.read_bytes())
            stored[-1] ^= 0x7F
            path.write_bytes(stored)
            with self.assertRaises(SessionError):
                load_session(path)

    def test_second_process_cannot_acquire_mutex_until_release(self):
        # A unique username namespace keeps this test separate from a running app
        # and from concurrent test runs. The child uses the same isolated name.
        test_user = "bili-reminder-test-" + uuid4().hex
        check = (
            "import windows_support as ws; "
            f"ws.getpass.getuser = lambda: {test_user!r}; "
            "print(ws.acquire_instance())"
        )
        with patch("windows_support.getpass.getuser", return_value=test_user):
            try:
                self.assertTrue(acquire_instance())
                duplicate = subprocess.run([sys.executable, "-c", check], capture_output=True, text=True, check=True)
                self.assertEqual(duplicate.stdout.strip(), "False")
            finally:
                release_instance()
            later = subprocess.run([sys.executable, "-c", check], capture_output=True, text=True, check=True)
            self.assertEqual(later.stdout.strip(), "True")

    def test_frozen_startup_path_with_spaces_is_quoted(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", r"D:\My Apps\B站提醒.exe"):
            self.assertEqual(startup_command(), '"D:\\My Apps\\B站提醒.exe" --background')

    def test_source_startup_path_is_absolute_and_quoted(self):
        with tempfile.TemporaryDirectory(prefix="bili reminder ") as directory:
            entry = Path(directory) / "main.py"
            entry.write_text("", encoding="utf-8")
            command = startup_command(entry)
            self.assertIn('"' + str(entry.resolve()) + '"', command)
            self.assertTrue(command.endswith(" --background"))


if __name__ == "__main__":
    unittest.main()

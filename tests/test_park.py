import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("park", ROOT / "bin" / "park.py")
park = importlib.util.module_from_spec(spec)
spec.loader.exec_module(park)

SID = "47a3b031-2ba5-4065-9b51-fdd7fa185de7"


class StripSessionOptions(unittest.TestCase):
    def test_keeps_other_options(self):
        args = ["--allow-all", "--model", "claude-opus-5.5", "--reasoning-effort", "xhigh"]
        self.assertEqual(park.strip_session_options(args), args)

    def test_removes_resume_forms(self):
        for args in (
            [f"--resume={SID}", "--allow-all"],
            ["--resume", SID, "--allow-all"],
            ["-r", SID, "--allow-all"],
            ["-r", "--allow-all"],
            ["--resume", "--allow-all"],
            ["--continue", "--allow-all"],
            ["--session-id", SID, "--allow-all"],
            [f"--session-id={SID}", "--allow-all"],
            ["-n", "my session", "--allow-all"],
            ["--name=my-session", "--allow-all"],
            ["--connect", "--allow-all"],
        ):
            with self.subTest(args=args):
                self.assertEqual(park.strip_session_options(args), ["--allow-all"])


class CopilotCommand(unittest.TestCase):
    PROCESSES = [
        {"pid": 10, "argv": ["node", "/usr/bin/copilot", "--allow-all", f"--resume={SID}"]},
        {"pid": 11, "argv": ["/usr/lib/node_modules/@github/copilot/node_modules/"
                             "@github/copilot-linux-x64/copilot", "--allow-all", f"--resume={SID}"]},
    ]

    def test_reads_wrapper_options_and_both_pids(self):
        options, pids = park.copilot_command(self.PROCESSES)
        self.assertEqual(options, ["--allow-all", f"--resume={SID}"])
        self.assertEqual(pids, [10, 11])

    def test_native_only(self):
        options, pids = park.copilot_command(self.PROCESSES[1:])
        self.assertEqual(options, ["--allow-all", f"--resume={SID}"])
        self.assertEqual(pids, [11])

    def test_no_copilot(self):
        self.assertIsNone(park.copilot_command([{"pid": 1, "argv": ["/bin/zsh"]}]))
        self.assertIsNone(park.copilot_command([{"pid": 2, "argv": ["node", "server.js"]}]))


class CleanTitle(unittest.TestCase):
    def test_strips_suffix(self):
        self.assertEqual(park.clean_title("Fix Grub Rescue Issue - GitHub Copilot"),
                         "Fix Grub Rescue Issue")

    def test_default(self):
        for title in (None, "", "GitHub Copilot", "   "):
            self.assertEqual(park.clean_title(title), park.DEFAULT_TITLE)

    def test_apostrophes_and_control_characters(self):
        self.assertEqual(park.clean_title("Fix user's \x07bug\n now"), "Fix user\u2019s bug now")

    def test_length(self):
        self.assertEqual(len(park.clean_title("x" * 500)), park.MAX_TITLE_LENGTH)


class SessionParsing(unittest.TestCase):
    def test_exit_summary_takes_last(self):
        old = "0d1ac088-2fcb-49da-ae7e-301896542c95"
        text = (f"   Resume     copilot --resume={old}\n$ copilot\n"
                f"   ▔▔▔▔    Resume     copilot --resume={SID}\n")
        self.assertEqual(park.session_from_exit_summary(text), SID)

    def test_exit_summary_ignores_typed_commands(self):
        self.assertIsNone(park.session_from_exit_summary(f"$ copilot --resume={SID}\n"))

    def test_log_takes_last_registration(self):
        old = "0d1ac088-2fcb-49da-ae7e-301896542c95"
        text = (f"x Registering foreground session: {old}\n"
                f"y Registering foreground session: {SID}\n")
        self.assertEqual(park.session_from_log(text), SID)


class ResumeArgv(unittest.TestCase):
    def argv(self, *options, title="Title"):
        return park.placeholder_argv(Path("/state/parked.sh"), SID, title, "2026-10-25 20:00",
                                     list(options))

    def test_valid(self):
        park.check_resume_argv(self.argv("--allow-all", "--model", "claude-opus-5.5"))

    def test_first_word_is_bash(self):
        self.assertEqual(self.argv()[:2], ["bash", "/state/parked.sh"])

    def test_rejects_apostrophe(self):
        with self.assertRaises(park.ParkError):
            park.check_resume_argv(self.argv("--add-dir", "/tmp/it's"))

    def test_rejects_too_many_args(self):
        with self.assertRaises(park.ParkError):
            park.check_resume_argv(self.argv(*(["--allow-all"] * 60)))


if __name__ == "__main__":
    unittest.main()

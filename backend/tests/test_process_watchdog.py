import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.process_watchdog import ProcessInfo, select_stale_project_browser_pids


def _proc(pid, parent_pid, name, command_line="", age_minutes=60):
    return ProcessInfo(
        pid=pid,
        parent_pid=parent_pid,
        name=name.lower(),
        command_line=command_line,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )


class ProcessWatchdogSelectionTests(unittest.TestCase):
    def test_selects_only_stale_project_browser_tree_and_driver_parent(self):
        profiles_root = r"D:\PRO\openai-register\backend\profiles"
        stale_profile = profiles_root + r"\worker_stale"
        active_profile = profiles_root + r"\worker_active"
        external_profile = r"D:\Other\profiles\worker_external"

        selected = select_stale_project_browser_pids(
            [
                _proc(100, 10, "node.exe", r"C:\Users\me\AppData\Local\ms-playwright\driver\node.exe", age_minutes=90),
                _proc(101, 100, "camoufox.exe", f'"C:\\camoufox\\camoufox.exe" -profile "{stale_profile}"', age_minutes=90),
                _proc(102, 101, "firefox.exe", "content process", age_minutes=90),
                _proc(201, 200, "camoufox.exe", f'"C:\\camoufox\\camoufox.exe" -profile "{active_profile}"', age_minutes=90),
                _proc(301, 300, "camoufox.exe", f'"C:\\camoufox\\camoufox.exe" -profile "{external_profile}"', age_minutes=90),
                _proc(401, 400, "camoufox.exe", f'"C:\\camoufox\\camoufox.exe" -profile "{profiles_root}\\worker_fresh"', age_minutes=5),
            ],
            profiles_root=profiles_root,
            active_profiles={active_profile},
            browser_stale_seconds=45 * 60,
            driver_stale_seconds=45 * 60,
        )

        self.assertEqual(selected, {100, 101, 102})

    def test_selects_only_orphan_driver_spawned_by_current_backend(self):
        selected = select_stale_project_browser_pids(
            [
                _proc(501, 12345, "node.exe", r"C:\Users\me\AppData\Local\ms-playwright\driver\node.exe", age_minutes=90),
                _proc(601, 99999, "node.exe", r"C:\Users\me\AppData\Local\ms-playwright\driver\node.exe", age_minutes=90),
                _proc(701, 12345, "node.exe", r"C:\PRO\openai-register\frontend\node_modules\vite\bin\vite.js", age_minutes=90),
            ],
            profiles_root=r"D:\PRO\openai-register\backend\profiles",
            active_profiles=set(),
            browser_stale_seconds=45 * 60,
            driver_stale_seconds=45 * 60,
            current_pid=12345,
        )

        self.assertEqual(selected, {501})


if __name__ == "__main__":
    unittest.main()

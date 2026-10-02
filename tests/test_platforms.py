from __future__ import annotations

import os
import subprocess
import sys

from helpers import Case

from med_lit_mcp.platforms import process_alive, stop_process_tree
from med_lit_mcp.store import file_lock


class PlatformTests(Case):
    def test_process_probe_does_not_terminate_process_and_tree_can_be_stopped(self) -> None:
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        try:
            self.assertTrue(process_alive(process.pid))
            self.assertTrue(process_alive(process.pid))
            self.assertIsNone(process.poll())
            stop_process_tree(process.pid)
            process.wait(timeout=10)
            self.assertFalse(process_alive(process.pid))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_lock_blocks_other_process_and_releases_after_exception(self) -> None:
        path = self.root / "lock"
        probe = 'import sys, portalocker\nf=open(sys.argv[1], "a+")\ntry:\n portalocker.lock(f, portalocker.LOCK_EX | portalocker.LOCK_NB)\nexcept portalocker.exceptions.LockException:\n sys.exit(2)\n'
        with self.assertRaisesRegex(RuntimeError, "release"), file_lock(path):
            result = subprocess.run([sys.executable, "-c", probe, str(path)], timeout=10, capture_output=True, check=False)
            self.assertEqual(result.returncode, 2, result.stderr)
            raise RuntimeError("release")
        result = subprocess.run([sys.executable, "-c", probe, str(path)], timeout=10, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_private_store_has_only_current_user_access(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows DACL test")
        import win32security

        from med_lit_mcp import keys

        path = keys.set_key(keys.BY_NAME["scopus"], "test-only")
        descriptor = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION)
        acl = descriptor.GetSecurityDescriptorDacl()
        self.assertEqual(acl.GetAceCount(), 1)
        self.assertEqual(acl.GetAce(0)[0][0], win32security.ACCESS_ALLOWED_ACE_TYPE)

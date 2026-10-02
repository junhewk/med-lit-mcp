from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from med_lit_mcp import __version__

ROOT = Path(__file__).resolve().parents[1]


def builder():
    spec = importlib.util.spec_from_file_location('build_macos_installer', ROOT / 'scripts/build_macos_installer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wheel(path: Path, *, name='med-lit-mcp', version=__version__) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as bundle:
        bundle.writestr(f'med_lit_mcp-{version}.dist-info/METADATA', f'Name: {name}\nVersion: {version}\n')
    return path


class MacInstallerTests(unittest.TestCase):
    def test_release_cannot_silently_build_unsigned_or_without_notarization(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output'
            build = builder()
            with patch.object(build.sys, 'platform', 'darwin'):
                for options in ({}, {'sign_identity': 'identity'}, {'notary_profile': 'profile'}, {'unsigned': True, 'sign_identity': 'identity'}):
                    with self.subTest(options=options):
                        with self.assertRaises(ValueError):
                            build.build(output, **options)
                        self.assertFalse(output.exists())

    def test_notarization_retains_rejected_pending_and_failed_submissions(self):
        build = builder()
        outcomes = [
            (json.dumps({"id": "rejected", "status": "Invalid"}), 0),
            (json.dumps({"id": "pending", "status": "In Progress"}), 0),
            (json.dumps({"id": "tool-error", "status": "Accepted"}), 69),
            ("incomplete response", 79),
        ]
        for response, exit_code in outcomes:
            with self.subTest(response=response, exit_code=exit_code), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                artifact = output / "test.dmg"
                artifact.write_bytes(b"signed candidate")

                def submit(command, *, stdout, stderr, check):
                    stdout.write(response)
                    stderr.write("submission diagnostic")
                    return subprocess.CompletedProcess(command, exit_code)

                with patch.object(build.subprocess, "run", side_effect=submit), patch.object(build, "run") as assessment:
                    with self.assertRaises(ValueError):
                        build.notarize(artifact, output, "test-profile")
                    assessment.assert_not_called()
                retained = list((output / ".notarization").iterdir())
                self.assertEqual(len(retained), 1)
                self.assertEqual((retained[0] / "test.dmg").read_bytes(), artifact.read_bytes())
                self.assertEqual((retained[0] / "stdout.json").read_text(), response)
                self.assertEqual((retained[0] / "stderr.txt").read_text(), "submission diagnostic")
                self.assertTrue((retained[0] / "result.json").exists())
                self.assertFalse(list(output.glob("*-notarization.json")))

    def test_accepted_submission_requires_stapling_and_assessment_before_release_report(self):
        build = builder()
        for assessment_error in (None, subprocess.CalledProcessError(1, "stapler")):
            with self.subTest(assessment_error=assessment_error), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                artifact = output / "test.dmg"
                artifact.write_bytes(b"signed candidate")

                def submit(command, *, stdout, stderr, check):
                    stdout.write(json.dumps({"id": "accepted", "status": "Accepted"}))
                    return subprocess.CompletedProcess(command, 0)

                with patch.object(build.subprocess, "run", side_effect=submit), patch.object(build, "run", side_effect=assessment_error):
                    if assessment_error:
                        with self.assertRaises(subprocess.CalledProcessError):
                            build.notarize(artifact, output, "test-profile")
                        self.assertFalse(list(output.glob("*-notarization.json")))
                    else:
                        candidate = build.notarize(artifact, output, "test-profile")
                        self.assertEqual(candidate.read_bytes(), artifact.read_bytes())
                        report = json.loads((output / f"med-lit-macos-{__version__}-notarization.json").read_text())
                        self.assertEqual(report["status"], "Accepted")

    @unittest.skipIf(os.name == 'nt', 'native bootstrap uses bash')
    def test_bootstrap_persists_wheel_and_registers_that_exact_copy(self):
        with tempfile.TemporaryDirectory(prefix='med lit 한글 ') as temporary:
            root = Path(temporary)
            tools = root / 'tools'; tools.mkdir()
            uname = tools / 'uname'
            uname.write_text('#!/bin/sh\nif [ "$1" = -s ]; then echo Darwin; else echo arm64; fi\n')
            uname.chmod(0o755)
            capture = root / 'capture.py'
            capture.write_text('import json,os,sys\nfrom pathlib import Path\nPath(os.environ["CAPTURE"]).write_text(json.dumps(sys.argv[1:]))\n')
            uv = tools / 'uv'
            uv.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{capture}" "$@"\n')
            uv.chmod(0o755)
            bundled = wheel(root / 'mounted DMG' / f'med_lit_mcp-{__version__}-py3-none-any.whl')
            digest = hashlib.sha256(bundled.read_bytes()).hexdigest()
            runtime = root / 'persistent runtime'
            log = root / 'args.json'
            env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH'], 'MED_LIT_RUNTIME_DIR': str(runtime), 'CAPTURE': str(log), 'MED_LIT_INSTALLER_FORCE_RUNTIME': '0'}
            result = subprocess.run(['bash', str(ROOT / 'installers/macos/bootstrap.sh'), str(bundled), digest, __version__], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = runtime / 'packages' / __version__ / digest / bundled.name
            self.assertEqual(saved.read_bytes(), bundled.read_bytes())
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertEqual(saved.parent.stat().st_mode & 0o777, 0o700)
            argv = json.loads(log.read_text())
            self.assertEqual(argv[argv.index('--from') + 1], str(saved))
            self.assertEqual(argv[argv.index('--package') + 1], str(saved))
            self.assertNotIn(str(bundled), argv)
            bundled.unlink()
            self.assertTrue(saved.is_file())
            self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(), digest)

    @unittest.skipIf(os.name == 'nt', 'native bootstrap uses bash')
    def test_damaged_bundle_never_runs_package_or_writes_persistent_wheel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uname = root / 'uname'; uname.write_text('#!/bin/sh\nif [ "$1" = -s ]; then echo Darwin; else echo arm64; fi\n'); uname.chmod(0o755)
            uv = root / 'uv'; uv.write_text('#!/bin/sh\ntouch "$CAPTURE"\n'); uv.chmod(0o755)
            bundled = wheel(root / f'med_lit_mcp-{__version__}-py3-none-any.whl')
            runtime = root / 'runtime'
            marker = root / 'ran'
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'], 'MED_LIT_RUNTIME_DIR': str(runtime), 'CAPTURE': str(marker), 'MED_LIT_INSTALLER_FORCE_RUNTIME': '0'}
            result = subprocess.run(['bash', str(ROOT / 'installers/macos/bootstrap.sh'), str(bundled), '0' * 64, __version__], env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('damaged', result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse((runtime / 'packages').exists())

    @unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'native Mac lifecycle test')
    def test_native_close_stops_child_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / 'child.sh'
            heartbeat = root / 'heartbeat'
            pidfile = root / 'pid'
            script.write_text('printf "%s\\n" "$$" > "$TEST_PID"\n/bin/bash -c \'while true; do /bin/date +%s > "$TEST_HEARTBEAT"; /bin/sleep 0.1; done\' &\nwait\n')
            main = root / 'main.swift'
            main.write_text('''import Foundation
import Darwin
let child = ChildGroup()
try child.start(script: CommandLine.arguments[1], arguments: [], environment: ProcessInfo.processInfo.environment, output: { _ in }, completion: { code in exit(code == 137 ? 0 : 1) })
DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { child.stop(immediate: true) }
RunLoop.main.run()
''')
            # Compile the exact production child-group component with a small main.
            # Swift rejects top-level expressions in secondary files even in inactive #if blocks.
            component = root / 'ChildGroup.swift'
            component.write_text((ROOT / 'installers/macos/Launcher.swift').read_text().split('final class Installer:', 1)[0])
            binary = root / 'close-test'
            compile = subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', str(component), str(main), '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compile.returncode, 0, compile.stderr)
            try:
                result = subprocess.run([str(binary), str(script)], env={**os.environ, 'TEST_PID': str(pidfile), 'TEST_HEARTBEAT': str(heartbeat)}, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(heartbeat.exists())
                modified = heartbeat.stat().st_mtime_ns
                time.sleep(0.3)
                self.assertEqual(heartbeat.stat().st_mtime_ns, modified, 'A descendant kept running after closing the installer')
            finally:
                if pidfile.exists():
                    try:
                        os.killpg(int(pidfile.read_text()), 9)
                    except ProcessLookupError:
                        pass

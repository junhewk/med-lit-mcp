from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from med_lit_mcp import __version__

ROOT = Path(__file__).resolve().parents[1]


def builder():
    spec = importlib.util.spec_from_file_location("build_windows_installer", ROOT / "scripts/build_windows_installer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsInstallerTests(unittest.TestCase):
    def test_built_launcher_has_pinned_version_and_inline_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = builder().build(Path(temporary))
            bat = artifacts[0].read_text(encoding='ascii')
            self.assertIn(f"med-lit-mcp=={__version__}", bat)
            self.assertNotIn("@BOOTSTRAP@", bat)
            self.assertNotIn("@VERSION@", bat)
            self.assertLess(max(map(len, bat.splitlines())), 8191)
            for forbidden in ("ExecutionPolicy", "Bypass", "Unblock-File", "Invoke-Expression", " -File "):
                self.assertNotIn(forbidden, bat)
            self.assertIn(b"\r\n", artifacts[0].read_bytes())

    def test_api_and_cli_build_only_windows_assets_with_matching_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for cli in (False, True):
                with self.subTest(cli=cli):
                    output = root / ('cli' if cli else 'api')
                    if cli:
                        result = subprocess.run(
                            [sys.executable, str(ROOT / 'scripts/build_windows_installer.py'), str(output)],
                            capture_output=True, text=True, check=False,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                    else:
                        artifacts = builder().build(output)
                        self.assertEqual([p.name for p in artifacts], ['Install med-lit.bat', 'SHA256SUMS'])
                    self.assertEqual({p.name for p in output.iterdir()}, {'Install med-lit.bat', 'SHA256SUMS'})
                    bat = output / 'Install med-lit.bat'
                    expected = f'{hashlib.sha256(bat.read_bytes()).hexdigest()}  Install med-lit.bat\n'
                    self.assertEqual((output / 'SHA256SUMS').read_text(encoding='ascii'), expected)

    @unittest.skipUnless(os.name == 'nt', 'requires Windows PowerShell')
    def test_windows_batch_executes_inline_bootstrap_with_existing_uv(self) -> None:
        import shutil
        powershell = shutil.which('powershell.exe')
        with tempfile.TemporaryDirectory(prefix='med lit 한글 ') as temporary:
            root = Path(temporary)
            artifact = builder().build(root)[0]
            exe = root / 'uv.exe'
            # A tiny executable captures arguments without installing anything.
            source = 'public class Capture { public static int Main(string[] args) { System.IO.File.WriteAllLines(System.Environment.GetEnvironmentVariable("MED_LIT_CAPTURE"), args); return 0; } }'
            env = {**os.environ, 'MED_LIT_CAPTURE': str(root / 'args.txt'), 'PATH': str(root) + os.pathsep + os.environ['PATH'], 'MED_LIT_TEST_EXE': str(exe), 'MED_LIT_TEST_CS': source}
            compiled = subprocess.run([powershell, '-NoProfile', '-Command', 'Add-Type -TypeDefinition $env:MED_LIT_TEST_CS -OutputAssembly $env:MED_LIT_TEST_EXE -OutputType ConsoleApplication'], env=env, capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(['cmd.exe', '/d', '/c', str(artifact)], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = (root / 'args.txt').read_text(encoding='utf-8').splitlines()
            self.assertEqual(args[:6], ['tool', 'run', '--python', '3.11', '--from', f'med-lit-mcp=={__version__}'])
            self.assertEqual(args[-2:], ['--launcher', str(exe)])


@unittest.skipUnless(os.name == 'nt', 'requires native Windows PowerShell and cmd.exe')
class WindowsBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        cls.temporary = tempfile.TemporaryDirectory(prefix='med lit 한글 ')
        cls.root = Path(cls.temporary.name)
        cls.powershell = shutil.which('powershell.exe')
        cls.fake = cls.root / 'fake-uv.exe'
        source = 'public class Capture { public static int Main(string[] args) { System.IO.File.WriteAllLines(System.Environment.GetEnvironmentVariable("MED_LIT_CAPTURE"), args); string code = System.Environment.GetEnvironmentVariable("MED_LIT_CAPTURE_EXIT"); return code == null ? 0 : int.Parse(code); } }'
        env = {**os.environ, 'MED_LIT_TEST_EXE': str(cls.fake), 'MED_LIT_TEST_CS': source}
        result = subprocess.run([cls.powershell, '-NoProfile', '-Command', 'Add-Type -TypeDefinition $env:MED_LIT_TEST_CS -OutputAssembly $env:MED_LIT_TEST_EXE -OutputType ConsoleApplication'], env=env, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_bootstrap(self, prelude='', *, args=(), **changes):
        root = self.root / self._testMethodName
        root.mkdir()
        artifact = builder().build(root)[0]
        source = artifact.read_text(encoding='ascii').replace(' -Command "', ' -Command "Import-Module Microsoft.PowerShell.Utility; Import-Module Microsoft.PowerShell.Management; ' + prelude)
        artifact.write_bytes(source.replace('\n', '\r\n').encode('ascii'))
        env = {**os.environ, 'MED_LIT_RUNTIME_DIR': str(root / 'runtime 한글'), 'MED_LIT_TEST_FAKE': str(self.fake), 'MED_LIT_CAPTURE': str(root / 'args.txt'), **changes}
        result = subprocess.run([str(artifact), *args], shell=True, input='\n', env=env, capture_output=True, text=True, errors='replace', timeout=30)
        return root, result

    def test_fresh_download_checksum_extract_and_cleanup(self):
        prelude = "function Get-Command { return $null }; function Invoke-WebRequest { param($Uri, $OutFile, [switch]$UseBasicParsing, $TimeoutSec); Set-Content -LiteralPath $OutFile archive }; function Get-FileHash { return @{ Hash = '5d223efa0bf00208c3853246af09420419dfbd352536aa6bb8163d6170e23890' } }; function Expand-Archive { param($LiteralPath, $DestinationPath); Copy-Item -LiteralPath $env:MED_LIT_TEST_FAKE -Destination (Join-Path $DestinationPath 'uv.exe') }; "
        root, result = self.run_bootstrap(prelude)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((root / 'runtime 한글' / 'uv.exe').is_file())
        self.assertEqual(list((root / 'runtime 한글').glob('.download-*')), [])
        args = (root / 'args.txt').read_text(encoding='utf-8').splitlines()
        self.assertEqual(args[-2:], ['--launcher', str(root / 'runtime 한글' / 'uv.exe')])

    def test_checksum_failure_never_extracts_or_launches_and_retains_error(self):
        prelude = "function Get-Command { return $null }; function Invoke-WebRequest { param($Uri, $OutFile, [switch]$UseBasicParsing, $TimeoutSec); Set-Content -LiteralPath $OutFile invalid }; function Get-FileHash { return @{ Hash = 'bad-checksum' } }; function Expand-Archive { throw 'Unexpected extraction' }; "
        root, result = self.run_bootstrap(prelude)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checksum did not match', result.stderr)
        self.assertIn('reopen this file to retry', result.stdout)
        self.assertFalse((root / 'args.txt').exists())
        self.assertEqual(list((root / 'runtime 한글').iterdir()), [])

    def test_download_failure_removes_temporary_files(self):
        root, result = self.run_bootstrap("function Get-Command { return $null }; function Invoke-WebRequest { throw 'Download failed: test network unavailable' }; ")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('network unavailable', result.stderr)
        self.assertEqual(list((root / 'runtime 한글').iterdir()), [])
        self.assertFalse((root / 'args.txt').exists())

    def test_unsupported_architecture_rejected_before_download(self):
        root, result = self.run_bootstrap(PROCESSOR_ARCHITECTURE='ARM64')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires Windows x64', result.stderr)
        self.assertFalse((root / 'runtime 한글').exists())

    def test_developer_checkout_unicode_arguments_and_cancel_exit(self):
        checkout = self.root / 'developer checkout 한글'
        checkout.mkdir()
        (checkout / 'pyproject.toml').touch()
        prelude = "function Get-Command { return @{ Source = $env:MED_LIT_TEST_FAKE } }; "
        root, result = self.run_bootstrap(prelude, args=('--dev', str(checkout)), MED_LIT_CAPTURE_EXIT='1')
        self.assertEqual(result.returncode, 1)
        self.assertIn('setup did not finish', result.stdout)
        self.assertNotIn('connection verified', result.stdout)
        args = (root / 'args.txt').read_text(encoding='utf-8').splitlines()
        self.assertEqual(args, ['run', '--directory', str(checkout), 'med-lit-mcp', 'setup', '--ui', '--client', 'chatgpt', '--dev', str(checkout)])

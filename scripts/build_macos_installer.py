"""Build the universal native Mac installer. Release builds require signing and notarization."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

from med_lit_mcp import __version__
from med_lit_mcp.installation import validate_wheel

ROOT = Path(__file__).resolve().parents[1]


def run(*command: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, **kwargs)


def notarize(artifact: Path, output: Path, profile: str) -> Path:
    """Retain every submission; return only a stapled, assessed Accepted candidate."""
    withheld = output / '.notarization'
    withheld.mkdir(exist_ok=True)
    submission = Path(tempfile.mkdtemp(prefix=f'{__version__}-', dir=withheld))
    candidate = submission / artifact.name
    shutil.copy2(artifact, candidate)
    print(f'Notarization candidate: {submission}', flush=True)
    with (submission / 'stdout.json').open('w', encoding='utf-8') as stdout, (submission / 'stderr.txt').open('w', encoding='utf-8') as stderr:
        response = subprocess.run(
            ['/usr/bin/xcrun', 'notarytool', 'submit', str(candidate), '--keychain-profile', profile,
             '--wait', '--timeout', '15m', '--output-format', 'json'], stdout=stdout, stderr=stderr, check=False)
    try:
        report = json.loads((submission / 'stdout.json').read_text())
    except json.JSONDecodeError:
        report = {'status': 'Unconfirmed', 'tool_exit': response.returncode}
    (submission / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if response.returncode or report.get('status') != 'Accepted':
        raise ValueError(f'Notarization is not accepted. Candidate and response retained in {submission}')
    run('/usr/bin/xcrun', 'stapler', 'staple', str(candidate))
    run('/usr/bin/xcrun', 'stapler', 'validate', str(candidate))
    run('/usr/sbin/spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=2', str(candidate))
    (output / f'med-lit-macos-{__version__}-notarization.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return candidate


def build(output: Path, *, wheel: Path | None = None, sign_identity: str | None = None,
          notary_profile: str | None = None, unsigned: bool = False) -> list[Path]:
    if sys.platform != 'darwin':
        raise ValueError('The native Mac installer must be built on macOS with Xcode')
    if unsigned and (sign_identity or notary_profile):
        raise ValueError('--unsigned cannot be combined with release signing options')
    if not unsigned and (not sign_identity or not notary_profile):
        raise ValueError('Release builds require --sign-identity and --notary-profile; use --unsigned only for local development')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.macos-build-', dir=output) as temporary:
        stage = Path(temporary)
        if wheel is None:
            wheel_dir = stage / 'wheel'
            run(shutil.which('uv') or '/opt/homebrew/bin/uv', 'build', '--wheel', '--out-dir', str(wheel_dir), cwd=ROOT)
            wheels = list(wheel_dir.glob('*.whl'))
            if len(wheels) != 1:
                raise ValueError('Expected one med-lit wheel')
            wheel = wheels[0]
        wheel = validate_wheel(wheel)
        payload = stage / 'payload'
        app = payload / 'med-lit Installer.app'
        resources = app / 'Contents/Resources'
        executable = app / 'Contents/MacOS/med-lit Installer'
        resources.mkdir(parents=True)
        executable.parent.mkdir()
        shutil.copy2(wheel, resources / wheel.name)
        shutil.copy2(ROOT / 'installers/macos/bootstrap.sh', resources / 'bootstrap.sh')
        for name in ('LICENSE', 'NOTICE'):
            shutil.copy2(ROOT / name, resources / name)
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        (resources / 'manifest.json').write_text(json.dumps({'version': __version__, 'wheel': wheel.name, 'wheel_sha256': digest}), encoding='utf-8')
        plist = {
            'CFBundleIdentifier': 'org.medlit.installer',
            'CFBundleName': 'med-lit Installer',
            'CFBundleDisplayName': 'med-lit Installer',
            'CFBundleExecutable': 'med-lit Installer',
            'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': __version__,
            'CFBundleVersion': __version__,
            'LSMinimumSystemVersion': '11.0',
            'NSHighResolutionCapable': True,
            'NSHumanReadableCopyright': 'med-lit contributors. PolyForm Noncommercial License 1.0.0.',
        }
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(plist))
        sdk = run('/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path', capture_output=True, text=True).stdout.strip()
        slices = []
        for architecture in ('arm64', 'x86_64'):
            binary = stage / f'launcher-{architecture}'
            run('/usr/bin/xcrun', 'swiftc', '-swift-version', '5', '-O', '-sdk', sdk,
                '-target', f'{architecture}-apple-macos11.0', str(ROOT / 'installers/macos/Launcher.swift'), '-o', str(binary))
            slices.append(str(binary))
        run('/usr/bin/lipo', '-create', *slices, '-output', str(executable))
        executable.chmod(0o755)
        if sign_identity:
            # The script and wheel live in Resources and are sealed by the bundle signature.
            run('/usr/bin/codesign', '--force', '--sign', sign_identity, '--timestamp', '--options', 'runtime', str(app))
            run('/usr/bin/codesign', '--verify', '--strict', '--deep', '--verbose=2', str(app))
        name = f'med-lit-macos-{__version__}' + ('-DEVELOPER-UNSIGNED' if unsigned else '') + '.dmg'
        dmg = output / name
        # Build in an owned temporary path so a failed build does not overwrite a previous artifact.
        built = stage / name
        run('/usr/bin/hdiutil', 'create', '-volname', 'med-lit Installer', '-srcfolder', str(payload),
            '-format', 'UDZO', '-ov', str(built))
        if sign_identity:
            run('/usr/bin/codesign', '--sign', sign_identity, '--timestamp', str(built))
        if notary_profile:
            built = notarize(built, output, notary_profile)
        shutil.move(str(built), dmg)
        checksum = output / 'SHA256SUMS-macos'
        checksum.write_text(f'{hashlib.sha256(dmg.read_bytes()).hexdigest()}  {dmg.name}\n', encoding='utf-8')
        return [dmg, checksum]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', type=Path, default=Path('dist-macos'))
    parser.add_argument('--wheel', type=Path, help='Use an already built matching portable med-lit wheel')
    parser.add_argument('--sign-identity', help='Developer ID Application identity name or SHA-1')
    parser.add_argument('--notary-profile', help='Existing notarytool Keychain profile name')
    parser.add_argument('--unsigned', action='store_true', help='Explicit local development build; never distribute this artifact')
    args = parser.parse_args()
    try:
        for path in build(args.output, wheel=args.wheel, sign_identity=args.sign_identity,
                          notary_profile=args.notary_profile, unsigned=args.unsigned):
            print(path)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Build failed: {error}\n')

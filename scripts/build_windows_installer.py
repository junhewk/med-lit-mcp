"""Build the standalone Windows launcher. Publish its pinned PyPI version first."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from med_lit_mcp import __version__

ROOT = Path(__file__).resolve().parents[1]


def windows_script() -> str:
    source = (ROOT / 'installers/windows-bootstrap.ps1').read_text()
    code = ' '.join(line.strip() for line in source.splitlines() if line.strip() and not line.lstrip().startswith('#'))
    # cmd.exe expands these even inside quotes. User paths travel via an environment variable.
    if any(char in code for char in ('"', '%', '\n', '\r')):
        raise ValueError('Inline PowerShell must not contain cmd.exe substitutions or quotes')
    return (ROOT / 'installers/Install med-lit.bat').read_text().replace('@BOOTSTRAP@', code).replace('@VERSION@', __version__)


def build(output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    windows = output / 'Install med-lit.bat'
    windows.write_bytes(windows_script().replace('\n', '\r\n').encode('ascii'))
    checksums = output / 'SHA256SUMS'
    checksums.write_text(f'{hashlib.sha256(windows.read_bytes()).hexdigest()}  {windows.name}\n', encoding='ascii')
    return [windows, checksums]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', type=Path, default=Path('dist-installers'))
    for artifact in build(parser.parse_args().output):
        print(artifact)

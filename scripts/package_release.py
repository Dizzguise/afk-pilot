"""Package the tested binary with checksums, dependency notices and build provenance."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import tkinter
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from AFKPilot import APP_VERSION


def digest(data):
    return hashlib.sha256(data).hexdigest()


def package():
    binary = ROOT / 'dist/AFKPilot.exe'
    files = {name: (ROOT / name).read_bytes() for name in (
        'AFK Pilot - Read Me.txt', 'CHANGELOG.md', 'PUBLISHING.md', 'THIRD_PARTY_NOTICES.md')}
    files['AFKPilot.exe'] = binary.read_bytes()
    files['licenses/Python.txt'] = (Path(sys.base_prefix) / 'LICENSE.txt').read_bytes()
    tcl_root = Path(tkinter.Tcl().eval('info library')).parent
    for component in ('tcl8.6', 'tk8.6'):
        installed = tcl_root / component / 'license.terms'
        fallback = ROOT / 'licenses' / ('Tcl.txt' if component.startswith('tcl') else 'Tk.txt')
        files[f'licenses/{component}.txt'] = (installed if installed.exists() else fallback).read_bytes()
    distribution = importlib.metadata.distribution('pyinstaller')
    copying = next(path for path in distribution.files if str(path).endswith('licenses/COPYING.txt'))
    files['licenses/PyInstaller.txt'] = distribution.locate_file(copying).read_bytes()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=ROOT, text=True).strip())
    manifest = {
        'version': APP_VERSION, 'source_commit': revision, 'source_dirty': dirty,
        'source_sha256': digest((ROOT / 'AFKPilot.py').read_bytes()),
        'python': platform.python_version(), 'architecture': platform.machine(),
        'pyinstaller': importlib.metadata.version('pyinstaller'),
        'exe_sha256': digest(files['AFKPilot.exe']),
        'signing': 'Check AFKPilot.exe Authenticode signature with Get-AuthenticodeSignature.',
    }
    files['BUILD-INFO.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    files['SHA256SUMS.txt'] = ''.join(f'{digest(data)}  {name}\n' for name, data in sorted(files.items())).encode()
    destination = ROOT / f'dist/AFK-Pilot-{APP_VERSION}-windows-x64.zip'
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(f'AFK Pilot {APP_VERSION}/{name}', data)
    checksum = destination.with_suffix('.zip.sha256')
    checksum.write_text(f'{digest(destination.read_bytes())}  {destination.name}\n', encoding='utf-8')
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        for name, data in files.items():
            assert archive.read(f'AFK Pilot {APP_VERSION}/{name}') == data
    print(destination)
    print(checksum.read_text().strip())


if __name__ == '__main__':
    package()

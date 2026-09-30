"""Generate Windows version metadata from the application's single version source."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from AFKPilot import APP_VERSION

version = tuple(int(part) for part in APP_VERSION.split('.')) + (0,)
text = f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}, mask=0x3f,
                   flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('FileDescription', 'AFK Pilot window automation utility'),
    StringStruct('FileVersion', '{APP_VERSION}'),
    StringStruct('InternalName', 'AFKPilot'),
    StringStruct('OriginalFilename', 'AFKPilot.exe'),
    StringStruct('ProductName', 'AFK Pilot'),
    StringStruct('ProductVersion', '{APP_VERSION}')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
'''
(ROOT / 'build').mkdir(exist_ok=True)
(ROOT / 'build/version.txt').write_text(text, encoding='utf-8')

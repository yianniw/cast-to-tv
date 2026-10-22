# Tracked release recipe. Keep normal dependency analysis for external libraries;
# remove only their own modules after analysis so their dependencies remain.
import sys
from pathlib import Path

root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from tools.release import prepare_release, external_entry, finish_release

release = prepare_release(root)
a = Analysis(
    [str(root / 'run_cast_to_tv.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / '.tmp/packaging/build_info.json'), 'cast_to_tv'),
                              (str(release / 'NOTICES.txt'), 'third-party')],
    hiddenimports=['pystray._win32', 'zeroconf.asyncio'],
    hookspath=[], runtime_hooks=[], excludes=[], noarchive=False, optimize=0,
)
finish_release(root, release, a)
a.pure = [entry for entry in a.pure if not external_entry(entry)]
a.binaries = [entry for entry in a.binaries if not external_entry(entry)]
a.datas = [entry for entry in a.datas if not external_entry(entry)]
if any(external_entry(entry) for entries in (a.pure, a.binaries, a.datas) for entry in entries):
    raise RuntimeError('An LGPL library is still embedded')
if (root / '.tmp/native-build/cmake/Release/cast_streaming_probe.exe').is_file():
    a.datas += [('native/cast_streaming_probe.exe',
                 str(root / '.tmp/native-build/cmake/Release/cast_streaming_probe.exe'), 'DATA')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='CastToTV',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          console=False, disable_windowed_traceback=False)

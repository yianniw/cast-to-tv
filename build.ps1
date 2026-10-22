$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    $nativeHelperPath = Join-Path $PSScriptRoot '.tmp\native-build\cmake\Release\cast_streaming_probe.exe'
    if (!(Test-Path -LiteralPath $nativeHelperPath -PathType Leaf)) {
        throw "Native helper missing. Run powershell -ExecutionPolicy Bypass -File .\native\build.ps1 -Fetch first, then retry this build."
    }
    python -c "import tkinter; tkinter.Tcl()"
    if ($LASTEXITCODE -ne 0) {
        throw "Tcl/Tk could not initialize. Build from a Windows session where Python's Tcl/Tk files are accessible."
    }
    $buildInfoPath = Join-Path $PSScriptRoot '.tmp\packaging\build_info.json'
    python -m cast_to_tv.build_info $buildInfoPath
    if ($LASTEXITCODE -ne 0) { throw "Could not write application build information" }
    python (Join-Path $PSScriptRoot 'native\collect_notices.py')
    if ($LASTEXITCODE -ne 0) { throw "Could not collect native dependency notices" }
    python -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot 'tools\CastToTV.spec')
    if ($LASTEXITCODE -ne 0) { throw "Executable build failed" }
    python -m tools.release publish
    if ($LASTEXITCODE -ne 0) { throw "Could not publish complete third-party materials; do not distribute this build" }
} finally {
    Pop-Location
}

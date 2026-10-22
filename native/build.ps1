param([switch]$Fetch)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if ($Fetch) {
        python native/fetch_dependencies.py
        if ($LASTEXITCODE -ne 0) { throw "Dependency fetch failed" }
    }
    $cmakeCommand = Get-Command cmake -ErrorAction SilentlyContinue
    if ($cmakeCommand) {
        $cmakePath = $cmakeCommand.Source
    } else {
        $vswherePath = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
        $vsPath = & $vswherePath -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        $cmakePath = Join-Path $vsPath 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
    }
    if (!(Test-Path -LiteralPath $cmakePath)) { throw "Install Visual Studio 2022 Desktop development with C++ and CMake tools" }
    & $cmakePath -S native -B .tmp/native-build/cmake -G 'Visual Studio 17 2022' -A x64
    if ($LASTEXITCODE -ne 0) { throw "Native configuration failed" }
    & $cmakePath --build .tmp/native-build/cmake --config Release --target cast_streaming_probe --parallel 4
    if ($LASTEXITCODE -ne 0) { throw "Native build failed" }
} finally {
    Pop-Location
}

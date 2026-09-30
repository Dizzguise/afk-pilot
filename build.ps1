param(
    [string]$Python = "python",
    [string]$CertificateThumbprint = "",
    [string]$SignTool = "signtool.exe"
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Push-Location $PSScriptRoot
try {
    & $Python -c "import sys, struct; assert sys.platform == 'win32' and struct.calcsize('P') == 8, 'Build on 64-bit Windows'; assert sys.version_info[:2] == (3, 12), 'Release builds require Python 3.12'"
    if ($LASTEXITCODE -ne 0) { throw "Unsupported build environment" }
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Regression tests failed" }
    & $Python AFKPilot.py --gui-self-test
    if ($LASTEXITCODE -ne 0) { throw "Source GUI smoke failed" }
    & $Python scripts/version_resource.py
    if ($LASTEXITCODE -ne 0) { throw "Version metadata generation failed" }
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx `
        --name AFKPilot --version-file (Join-Path $PSScriptRoot 'build/version.txt') --distpath dist `
        --workpath build/pyinstaller --specpath build AFKPilot.py
    if ($LASTEXITCODE -ne 0) { throw "Executable build failed" }
    if ($CertificateThumbprint) {
        & $SignTool sign /sha1 $CertificateThumbprint /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 dist/AFKPilot.exe
        if ($LASTEXITCODE -ne 0) { throw "Code signing failed" }
        & $SignTool verify /pa /all dist/AFKPilot.exe
        if ($LASTEXITCODE -ne 0) { throw "Signature verification failed" }
    }
    foreach ($testFlag in @("--self-test", "--gui-self-test")) {
        $process = Start-Process -FilePath (Join-Path $PSScriptRoot "dist/AFKPilot.exe") -ArgumentList $testFlag -PassThru -WindowStyle Hidden
        if (-not $process.WaitForExit(30000)) {
            $process.Kill()
            throw "Packaged smoke timed out: $testFlag"
        }
        $process.Refresh()
        if ($process.ExitCode -ne 0) { throw "Packaged smoke failed: $testFlag (exit $($process.ExitCode))" }
    }
    & $Python scripts/package_release.py
    if ($LASTEXITCODE -ne 0) { throw "Release packaging failed" }
} finally {
    Pop-Location
}

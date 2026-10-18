param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not $SkipTests) {
        python -m unittest discover -s tests -t . -p 'test_*.py'
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    }
    $releaseVersion = python -c "from link_rules import VERSION; print(VERSION)"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read version' }
    python -m PyInstaller --noconfirm --onefile --windowed --name "EmbyLinkStudio-v$releaseVersion" --paths src --specpath build/specs --distpath dist --workpath build/pyinstaller src/docker_client.py
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
    python package_deploy.py
    if ($LASTEXITCODE -ne 0) { throw 'Deployment packaging failed' }
    python tools/check_release.py dist
    if ($LASTEXITCODE -ne 0) { throw 'Release verification failed' }
} finally {
    Pop-Location
}

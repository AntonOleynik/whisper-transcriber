param(
    [string]$Version = "0.2.0"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Path $dist -Force | Out-Null

# The archive contains only source files and launch scripts. The virtual
# environment, downloaded models, tokens, and user recordings are excluded.
$names = @(
    "README.md",
    ".gitignore",
    "requirements.txt",
    "setup.bat",
    "start.bat",
    "audio_converter.bat",
    "transcribe_gui.py",
    "audio_converter.py",
    "package_release.ps1"
)
$files = foreach ($name in $names) {
    $path = Join-Path $root $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Release file is missing: $name"
    }
    $path
}

$safeVersion = $Version -replace "[^0-9A-Za-z._-]", "-"
$archive = Join-Path $dist "WhisperTranscriber-v$safeVersion-windows.zip"
Compress-Archive -Path $files -DestinationPath $archive -Force
Write-Host "Release archive created: $archive"

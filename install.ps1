#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:INSTAGRAM_REPO_URL) { $env:INSTAGRAM_REPO_URL } else { "https://github.com/josephtandle/instagram-agent" }
$TargetDir = if ($env:INSTAGRAM_TARGET_DIR) { $env:INSTAGRAM_TARGET_DIR } else { Join-Path $env:USERPROFILE "Tools\Instagram" }
$TempDir = Join-Path $env:TEMP ("instagram-agent-" + [System.Guid]::NewGuid().ToString("N"))

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host "Git is required." -ForegroundColor Red
  exit 1
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Write-Host "Node.js is required." -ForegroundColor Red
  exit 1
}

try {
  Write-Host "Downloading Instagram agent..."
  git clone --depth 1 $RepoUrl "$TempDir\instagram-agent" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Git clone failed" }

  Set-Location "$TempDir\instagram-agent"
  node install/install-instagram.js --target $TargetDir @args
}
finally {
  if (Test-Path $TempDir) {
    Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
  }
}

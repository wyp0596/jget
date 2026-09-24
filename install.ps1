# Install jget and its AI agent skill on Windows.
#
#   irm https://raw.githubusercontent.com/wyp0596/jget/main/install.ps1 | iex
#
# Set $env:JGET_INSTALL_ARGS to pass options to `jget install`, e.g. "--cursor".
$ErrorActionPreference = 'Stop'

$Repo = if ($env:JGET_REPO) { $env:JGET_REPO } else { 'wyp0596/jget' }
$Ref = if ($env:JGET_REF) { $env:JGET_REF } else { 'main' }
$BaseUrl = if ($env:JGET_BASE_URL) { $env:JGET_BASE_URL } else { "https://raw.githubusercontent.com/$Repo/$Ref" }

function Find-Python {
    # `py` is the official launcher; `python` may be the Microsoft Store stub, so verify it runs.
    $candidates = @(
        @{ Cmd = 'py'; Args = @('-3') },
        @{ Cmd = 'python'; Args = @() },
        @{ Cmd = 'python3'; Args = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Cmd -ErrorAction SilentlyContinue)) { continue }
        $pyArgs = $c.Args
        $exe = & $c.Cmd @pyArgs -c 'import sys; assert sys.version_info >= (3, 8); print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { return ($exe | Select-Object -First 1).Trim() }
    }
    return $null
}

$Python = Find-Python
if (-not $Python) { throw 'Python 3.8+ is required. Install it from https://www.python.org/downloads/ and try again.' }

$Tmp = Join-Path ([IO.Path]::GetTempPath()) ("jget-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Tmp | Out-Null
try {
    Write-Host "Downloading jget from $BaseUrl ..."
    Invoke-WebRequest -UseBasicParsing "$BaseUrl/jget.py" -OutFile (Join-Path $Tmp 'jget.py')
    Invoke-WebRequest -UseBasicParsing "$BaseUrl/SKILL.md" -OutFile (Join-Path $Tmp 'SKILL.md')

    $InstallArgs = if ($env:JGET_INSTALL_ARGS) { $env:JGET_INSTALL_ARGS -split '\s+' } else { @() }
    & $Python (Join-Path $Tmp 'jget.py') install @InstallArgs
    if ($LASTEXITCODE -ne 0) { throw "jget install failed with exit code $LASTEXITCODE" }
} finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}

Write-Host @'

Next, set your Jira credentials (then open a new terminal):

  setx JIRA_URL "https://your-domain.atlassian.net"
  setx JIRA_USER "you@example.com"
  setx JIRA_TOKEN "<api-token>"

For Jira Server / Data Center Personal Access Tokens, also run
setx JIRA_AUTH "bearer" (JIRA_USER is then not needed).

Then try:  jget PROJ-123
'@

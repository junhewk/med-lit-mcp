<#
.SYNOPSIS
Install or update med-lit-mcp on Windows.

.DESCRIPTION
Saves your contact email, reviews folder and optional database keys (`med-lit-mcp setup`) and
registers the server with the MCP clients on this computer (Hermes, Claude Code, Codex, pi). Use
the same command to install and to update: it runs the newest release from PyPI, so the
`uvx med-lit-mcp` that clients start is that release from then on.

Needs uv (https://docs.astral.sh/uv/). Written for Windows PowerShell 5.1 and PowerShell 7.

.PARAMETER Client
Register with these clients: hermes, claude-code, codex or pi. Default: every one found.

.PARAMETER ChatGPT
For ChatGPT desktop Work/Codex: fill in the settings in a browser form instead of terminal prompts,
then register and test the connection.

.PARAMETER Email
Contact email for PubMed and Unpaywall.

.PARAMETER ProjectsDir
Folder for your reviews (default ~\med-lit).

.PARAMETER Yes
Accept defaults without asking (needs -Email; keys are skipped).

.PARAMETER NoRegister
Only save the settings and keys.

.PARAMETER Dev
Run the server from a source checkout instead of PyPI (development).

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -ChatGPT

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1 -Client codex -Email you@example.org -Yes
#>
[CmdletBinding()]
param(
    [string[]]$Client,
    [switch]$ChatGPT,
    [string]$Email,
    [string]$ProjectsDir,
    [switch]$Yes,
    [switch]$NoRegister,
    [string]$Dev
)

$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "'$File $($Arguments -join ' ')' failed with exit code $LASTEXITCODE"
    }
}

# Windows PowerShell 5.1 turns native stderr into terminating errors under 'Stop'.
function Invoke-Captured {
    param([string]$File, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $File @Arguments 2>&1 | ForEach-Object { "$_" }
        return @{ Code = $LASTEXITCODE; Output = ($output -join "`n") }
    } finally {
        $ErrorActionPreference = $previous
    }
}

if ($ChatGPT -and ($Client -or $Yes -or $NoRegister)) {
    [Console]::Error.WriteLine('-ChatGPT uses the browser form; it cannot be combined with -Client, -Yes or -NoRegister.')
    exit 2
}

# 1. uv, which runs med-lit and its Python without touching the system's.
$uv = Get-Command uv -ErrorAction SilentlyContinue
$uvx = Get-Command uvx -ErrorAction SilentlyContinue
if (-not $uv -or -not $uvx) {
    Write-Output 'uv is not installed. Install it, open a new PowerShell window, and rerun this script:'
    Write-Output '  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    exit 1
}

# 2. The server: the newest release from PyPI, or a source checkout.
if ($Dev) {
    if (-not (Test-Path -LiteralPath (Join-Path $Dev 'pyproject.toml'))) {
        [Console]::Error.WriteLine("-Dev must name a med-lit-mcp checkout (no pyproject.toml in $Dev).")
        exit 2
    }
    $Dev = (Resolve-Path -LiteralPath $Dev).Path
    $runner = $uv.Source
    $runnerArgs = @('run', '--quiet', '--directory', $Dev, 'med-lit-mcp')
} else {
    $runner = $uvx.Source
    $runnerArgs = @('med-lit-mcp@latest')
}

try {
    $version = Invoke-Captured $runner ($runnerArgs + @('--version'))
    if ($version.Code -ne 0) { throw "med-lit-mcp could not start:`n$($version.Output)" }
    Write-Output "==> $(($version.Output -split "`n")[-1])"

    # 3. Settings, keys and client registration.
    $setupArgs = @('setup')
    if ($ChatGPT) {
        $setupArgs += @('--ui', '--client', 'chatgpt')
    } else {
        foreach ($name in $Client) { $setupArgs += @('--client', $name) }
        if ($Email) { $setupArgs += @('--email', $Email) }
        if ($ProjectsDir) { $setupArgs += @('--projects-dir', $ProjectsDir) }
        if ($Yes) { $setupArgs += '--yes' }
        if ($NoRegister) { $setupArgs += '--no-register' }
    }
    if ($Dev) { $setupArgs += @('--dev', $Dev) }
    Write-Output '==> Setting up med-lit'
    Invoke-Checked $runner ($runnerArgs + $setupArgs)

    # 4. Check: the saved configuration as the server reads it.
    Write-Output '==> Checking the configuration'
    Invoke-Checked $runner ($runnerArgs + @('--check'))
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}

Write-Output ''
Write-Output 'Done. Restart your MCP client (in Hermes /reload-mcp, in pi a new session or /reload), then ask:'
Write-Output '  "Start a new review on ..."'
Write-Output 'To update later, run this script again.'

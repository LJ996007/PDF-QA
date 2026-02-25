$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[DEV-DOWN] $Message" -ForegroundColor Cyan
}

function Write-WarnMessage {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[DEV-DOWN] $Message" -ForegroundColor Yellow
}

function Stop-ProcessTree {
    param([object]$PidValue)

    [int]$pidInt = 0
    if (-not [int]::TryParse([string]$PidValue, [ref]$pidInt)) {
        return $false
    }

    if (-not (Get-Process -Id $pidInt -ErrorAction SilentlyContinue)) {
        return $false
    }

    & taskkill /PID $pidInt /T /F *> $null
    Start-Sleep -Milliseconds 300
    return $null -eq (Get-Process -Id $pidInt -ErrorAction SilentlyContinue)
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$stateFile = Join-Path $repoRoot '.runtime\dev-processes.json'

if (-not (Test-Path $stateFile)) {
    Write-Step "No runtime state found. Services may already be stopped."
    exit 0
}

$state = $null
try {
    $state = Get-Content -Path $stateFile -Raw | ConvertFrom-Json
} catch {
    Write-WarnMessage "State file is invalid. Removing stale state file."
    Remove-Item -Path $stateFile -Force -ErrorAction SilentlyContinue
    exit 0
}

$frontendStopped = Stop-ProcessTree -PidValue $state.frontendPid
if ($frontendStopped) {
    Write-Step "Frontend process stopped."
} else {
    Write-WarnMessage "Frontend process was not running."
}

$backendStopped = Stop-ProcessTree -PidValue $state.backendPid
if ($backendStopped) {
    Write-Step "Backend process stopped."
} else {
    Write-WarnMessage "Backend process was not running."
}

Remove-Item -Path $stateFile -Force -ErrorAction SilentlyContinue
Write-Step "Runtime state cleaned."
exit 0

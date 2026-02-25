$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[DEV-UP] $Message" -ForegroundColor Cyan
}

function Write-WarnMessage {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[DEV-UP] $Message" -ForegroundColor Yellow
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory = ''
    )

    if ($WorkingDirectory) {
        Push-Location $WorkingDirectory
    }

    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
        }
    } finally {
        if ($WorkingDirectory) {
            Pop-Location
        }
    }
}

function Resolve-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            & py -3 --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return @{
                    Exe = 'py'
                    Prefix = @('-3')
                }
            }
        } catch {
            # Fall through to next option.
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        try {
            & python --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return @{
                    Exe = 'python'
                    Prefix = @()
                }
            }
        } catch {
            # Fall through to failure.
        }
    }

    throw "Python 3 not found. Install Python and ensure either 'py -3' or 'python' is available in PATH."
}

function Test-ProcessAlive {
    param([object]$PidValue)
    [int]$pidInt = 0
    if (-not [int]::TryParse([string]$PidValue, [ref]$pidInt)) {
        return $false
    }

    return $null -ne (Get-Process -Id $pidInt -ErrorAction SilentlyContinue)
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

function Get-FreePort {
    param([Parameter(Mandatory = $true)][int]$StartPort)

    for ($port = $StartPort; $port -le 65535; $port++) {
        $listener = $null
        try {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
            $listener.Start()
            $listener.Stop()
            return $port
        } catch {
            if ($null -ne $listener) {
                try {
                    $listener.Stop()
                } catch {
                    # no-op
                }
            }
        }
    }

    throw "No free port available from $StartPort."
}

function Wait-ForHttpReady {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [object]$ProcessId
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($ProcessId -and -not (Test-ProcessAlive -PidValue $ProcessId)) {
            return $false
        }

        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        } catch {
            # Keep polling.
        }

        Start-Sleep -Seconds 1
    }

    return $false
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeDir = Join-Path $repoRoot '.runtime'
$stateFile = Join-Path $runtimeDir 'dev-processes.json'
$backendReqHashFile = Join-Path $runtimeDir 'backend-requirements.sha256'
$logDir = Join-Path $repoRoot 'logs'

$backendOutLog = Join-Path $logDir 'backend.dev.out.log'
$backendErrLog = Join-Path $logDir 'backend.dev.err.log'
$frontendOutLog = Join-Path $logDir 'frontend.dev.out.log'
$frontendErrLog = Join-Path $logDir 'frontend.dev.err.log'

$venvDir = Join-Path $repoRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$backendDir = Join-Path $repoRoot 'backend'
$backendRequirementsFile = Join-Path $repoRoot 'backend\requirements.txt'
$frontendDir = Join-Path $repoRoot 'frontend'
$frontendNodeModules = Join-Path $frontendDir 'node_modules'
$envFile = Join-Path $repoRoot '.env'
$envExampleFile = Join-Path $repoRoot '.env.example'

$backendProcessId = $null
$frontendProcessId = $null
$envCreated = $false

try {
    Write-Step "Checking required commands..."
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm not found. Install Node.js and ensure npm is in PATH."
    }

    $pythonCommand = Resolve-PythonCommand
    $pythonExe = [string]$pythonCommand.Exe
    $pythonPrefix = @($pythonCommand.Prefix)

    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null

    if (-not (Test-Path $envFile)) {
        if (Test-Path $envExampleFile) {
            Copy-Item -Path $envExampleFile -Destination $envFile -Force
            $envCreated = $true
            Write-WarnMessage "Created .env from .env.example. Update API keys if needed."
        } else {
            Write-WarnMessage ".env is missing and .env.example was not found. Continuing without auto-create."
        }
    }

    if (Test-Path $stateFile) {
        $existingState = $null
        try {
            $existingState = Get-Content -Path $stateFile -Raw | ConvertFrom-Json
        } catch {
            Write-WarnMessage "State file is invalid. It will be cleaned."
        }

        if ($null -ne $existingState) {
            $backendAlive = Test-ProcessAlive -PidValue $existingState.backendPid
            $frontendAlive = Test-ProcessAlive -PidValue $existingState.frontendPid
            if ($backendAlive -and $frontendAlive) {
                $existingBackendPort = [string]$existingState.backendPort
                $existingFrontendPort = [string]$existingState.frontendPort
                Write-Step "Services are already running."
                Write-Host "Frontend: http://127.0.0.1:$existingFrontendPort"
                Write-Host "Backend : http://127.0.0.1:$existingBackendPort"
                Write-Host "Use stop-dev.bat before starting a new instance."
                exit 0
            }
        }

        Write-WarnMessage "Detected stale runtime state. Cleaning it before startup."
        Remove-Item -Path $stateFile -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path $backendRequirementsFile)) {
        throw "Missing backend requirements file: $backendRequirementsFile"
    }

    $venvExists = Test-Path $venvPython
    if (-not $venvExists) {
        Write-Step "Creating backend virtual environment (.venv)..."
        $venvArgs = @() + $pythonPrefix + @('-m', 'venv', $venvDir)
        Invoke-CheckedCommand -FilePath $pythonExe -Arguments $venvArgs
    }

    $requirementsHash = (Get-FileHash -Path $backendRequirementsFile -Algorithm SHA256).Hash
    $installBackendDeps = $true
    if ((Test-Path $venvPython) -and (Test-Path $backendReqHashFile)) {
        $savedHash = (Get-Content -Path $backendReqHashFile -Raw).Trim()
        if ($savedHash -eq $requirementsHash) {
            $installBackendDeps = $false
        }
    }

    if ($installBackendDeps) {
        Write-Step "Installing backend dependencies..."
        Invoke-CheckedCommand -FilePath $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
        Invoke-CheckedCommand -FilePath $venvPython -Arguments @('-m', 'pip', 'install', '-r', $backendRequirementsFile)
        Set-Content -Path $backendReqHashFile -Value $requirementsHash -Encoding ASCII
    } else {
        Write-Step "Backend dependencies already up to date."
    }

    if (-not (Test-Path $frontendNodeModules)) {
        Write-Step "Installing frontend dependencies with npm ci..."
        Invoke-CheckedCommand -FilePath 'npm' -Arguments @('ci') -WorkingDirectory $frontendDir
    } else {
        Write-Step "Frontend dependencies already present."
    }

    $backendPort = Get-FreePort -StartPort 8000
    $backendApiUrl = "http://127.0.0.1:$backendPort"
    Write-Step "Starting backend on $backendApiUrl ..."
    $backendArgs = @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', "$backendPort", '--reload')
    $backendProcess = Start-Process -FilePath $venvPython `
        -ArgumentList $backendArgs `
        -WorkingDirectory $backendDir `
        -RedirectStandardOutput $backendOutLog `
        -RedirectStandardError $backendErrLog `
        -PassThru
    $backendProcessId = $backendProcess.Id

    $backendHealthUrl = "$backendApiUrl/api/health"
    if (-not (Wait-ForHttpReady -Url $backendHealthUrl -TimeoutSeconds 90 -ProcessId $backendProcessId)) {
        throw "Backend failed to become healthy at $backendHealthUrl. Check $backendErrLog for details."
    }

    $frontendPort = Get-FreePort -StartPort 3000
    $frontendUrl = "http://127.0.0.1:$frontendPort"
    Write-Step "Starting frontend on $frontendUrl ..."
    $frontendCommand = "set ""VITE_API_BASE_URL=$backendApiUrl"" && npm run dev -- --host 127.0.0.1 --port $frontendPort"
    $frontendProcess = Start-Process -FilePath 'cmd.exe' `
        -ArgumentList @('/d', '/c', $frontendCommand) `
        -WorkingDirectory $frontendDir `
        -RedirectStandardOutput $frontendOutLog `
        -RedirectStandardError $frontendErrLog `
        -PassThru
    $frontendProcessId = $frontendProcess.Id

    if (-not (Wait-ForHttpReady -Url $frontendUrl -TimeoutSeconds 90 -ProcessId $frontendProcessId)) {
        throw "Frontend failed to become ready at $frontendUrl. Check $frontendErrLog for details."
    }

    $state = [ordered]@{
        backendPid = $backendProcessId
        frontendPid = $frontendProcessId
        backendPort = $backendPort
        frontendPort = $frontendPort
        startedAt = (Get-Date).ToString('o')
        logPaths = [ordered]@{
            backendOut = $backendOutLog
            backendErr = $backendErrLog
            frontendOut = $frontendOutLog
            frontendErr = $frontendErrLog
        }
    }
    $state | ConvertTo-Json -Depth 4 | Set-Content -Path $stateFile -Encoding UTF8

    Write-Step "Services started successfully."
    Write-Host "Frontend: $frontendUrl"
    Write-Host "Backend : $backendApiUrl"
    Write-Host "State   : $stateFile"
    Write-Host "Logs    :"
    Write-Host "  $backendOutLog"
    Write-Host "  $backendErrLog"
    Write-Host "  $frontendOutLog"
    Write-Host "  $frontendErrLog"

    if ($envCreated) {
        Write-WarnMessage ".env was auto-created. Add valid API keys before production use."
    }

    try {
        Start-Process $frontendUrl | Out-Null
    } catch {
        Write-WarnMessage "Could not auto-open browser. Open this URL manually: $frontendUrl"
    }
    exit 0
} catch {
    Write-Host "[DEV-UP] ERROR: $($_.Exception.Message)" -ForegroundColor Red

    if ($null -ne $frontendProcessId) {
        Stop-ProcessTree -PidValue $frontendProcessId | Out-Null
    }
    if ($null -ne $backendProcessId) {
        Stop-ProcessTree -PidValue $backendProcessId | Out-Null
    }
    if (Test-Path $stateFile) {
        Remove-Item -Path $stateFile -Force -ErrorAction SilentlyContinue
    }

    exit 1
}

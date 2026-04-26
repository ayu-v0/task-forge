param(
    [int]$ApiPort = 8000,
    [switch]$WithWorker,
    [switch]$Migrate,
    [switch]$InstallDeps,
    [switch]$BuildWeb,
    [switch]$NoPortFallback
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$RunDir = Join-Path $ProjectRoot ".run"

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

function Resolve-PythonCommand {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    return "python"
}

function Test-PortInUse {
    param([int]$Port)
    $connection = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq "Listen" }
    return $null -ne $connection
}

function Resolve-ApiPort {
    param([int]$PreferredPort)
    if (-not (Test-PortInUse -Port $PreferredPort)) {
        return $PreferredPort
    }
    if ($NoPortFallback) {
        throw "Port $PreferredPort is already in use."
    }

    for ($candidate = $PreferredPort + 1; $candidate -le ($PreferredPort + 20); $candidate++) {
        if (-not (Test-PortInUse -Port $candidate)) {
            Write-Host "Port $PreferredPort is in use; using $candidate instead."
            return $candidate
        }
    }
    throw "No free API port found near $PreferredPort."
}

function Start-BackgroundProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$StdOutPath,
        [string]$StdErrPath,
        [string]$PidPath
    )

    $escapedRoot = $ProjectRoot.ToString().Replace('"', '\"')
    $escapedFilePath = $FilePath.Replace('"', '\"')
    $escapedArguments = ($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join " "
    $escapedStdOutPath = $StdOutPath.Replace('"', '\"')
    $escapedStdErrPath = $StdErrPath.Replace('"', '\"')
    $command = "cd /d `"$escapedRoot`" && `"$escapedFilePath`" $escapedArguments > `"$escapedStdOutPath`" 2> `"$escapedStdErrPath`""

    $process = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList @("/k", $command) `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -PassThru

    Set-Content -Path $PidPath -Value $process.Id -Encoding ascii
    Write-Host "$Name started. PID=$($process.Id)"
}

$Python = Resolve-PythonCommand

if ($InstallDeps) {
    Write-Host "Installing Python dependencies..."
    & $Python -m pip install -r requirements.txt
}

if ($BuildWeb) {
    Write-Host "Building web assets..."
    npm run build
}

if ($Migrate) {
    Write-Host "Running database migrations..."
    & $Python -m alembic upgrade head
}

$ResolvedApiPort = Resolve-ApiPort -PreferredPort $ApiPort
$ApiOutLog = Join-Path $RunDir "api.out.log"
$ApiErrLog = Join-Path $RunDir "api.err.log"
$ApiPid = Join-Path $RunDir "api.pid"

Start-BackgroundProcess `
    -Name "TaskForge API" `
    -FilePath $Python `
    -Arguments @("-m", "uvicorn", "src.apps.api.app:app", "--host", "127.0.0.1", "--port", "$ResolvedApiPort") `
    -StdOutPath $ApiOutLog `
    -StdErrPath $ApiErrLog `
    -PidPath $ApiPid

if ($WithWorker) {
    $WorkerOutLog = Join-Path $RunDir "worker.out.log"
    $WorkerErrLog = Join-Path $RunDir "worker.err.log"
    $WorkerPid = Join-Path $RunDir "worker.pid"

    Start-BackgroundProcess `
        -Name "TaskForge Worker" `
        -FilePath $Python `
        -Arguments @("-m", "src.apps.worker.main") `
        -StdOutPath $WorkerOutLog `
        -StdErrPath $WorkerErrLog `
        -PidPath $WorkerPid
}

Start-Sleep -Seconds 2

try {
    $health = Invoke-WebRequest -Uri "http://127.0.0.1:$ResolvedApiPort/health" -UseBasicParsing -TimeoutSec 5
    Write-Host "Health check: $($health.Content)"
}
catch {
    Write-Warning "API health check failed. Check logs in $RunDir."
}

Write-Host ""
Write-Host "TaskForge URLs:"
Write-Host "  API health:      http://127.0.0.1:$ResolvedApiPort/health"
Write-Host "  Agent console:   http://127.0.0.1:$ResolvedApiPort/console/agents"
Write-Host "  Batch console:   http://127.0.0.1:$ResolvedApiPort/console/batches"
Write-Host ""
Write-Host "Logs:"
Write-Host "  API stdout:      $ApiOutLog"
Write-Host "  API stderr:      $ApiErrLog"
if ($WithWorker) {
    Write-Host "  Worker stdout:   $WorkerOutLog"
    Write-Host "  Worker stderr:   $WorkerErrLog"
}

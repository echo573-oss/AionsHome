param([switch]$CheckOnly, [switch]$StartOnly)

# User-run reload helper. Include the old QQ port for upgrades.
$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $workspacePath '.venv\Scripts\python.exe'
$serverPath = Join-Path $workspacePath 'aion-chat'
$baseUrl = 'http://127.0.0.1:8080'
$outputPath = Join-Path $serverPath 'data\home-reload.stdout.log'
$errorPath = Join-Path $serverPath 'data\home-reload.stderr.log'
$siwcStarter = Join-Path $workspacePath 'scripts\start-siwc-sidecar.ps1'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "AionHome Python environment missing: $pythonPath"
}

function Test-HomePages {
    foreach ($page in @(
        @{Path='/chat'; Marker='id="chatTitle"'},
        @{Path='/kitchen'; Marker='id="kitchenTitle"'},
        @{Path='/music-box'; Marker='music-box.js'}
    )) {
        try {
            $result = Invoke-WebRequest -UseBasicParsing ($baseUrl + $page.Path) -TimeoutSec 2
            if ($result.StatusCode -ne 200 -or $result.Content -notmatch [regex]::Escape($page.Marker)) {
                throw 'Unexpected page'
            }
        } catch {
            throw "Page $($page.Path) is unavailable or served the wrong content."
        }
    }
}

function Show-SiwcStatus {
    try {
        $health = Invoke-RestMethod "$baseUrl/api/siwc/health" -TimeoutSec 3
        if (-not $health.ok) { throw 'Unhealthy sidecar' }
        Write-Host 'SIWC health: OK.'
    } catch {
        Write-Warning 'SIWC health check failed. Other model routes can still be used.'
    }
}

$targets = @()
$ports = if ($StartOnly -or $CheckOnly) { @(8080) } else { @(8080, 18788, 8788) }
foreach ($servicePort in $ports) {
    $listener = Get-NetTCPConnection -LocalPort $servicePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { continue }
    $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    # A Windows venv launcher may own the child interpreter that holds the port.
    $parentProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($serviceProcess.ParentProcessId)"
    $ownInterpreter = ($serviceProcess.ExecutablePath -eq $pythonPath -or $parentProcess.ExecutablePath -eq $pythonPath)
    $ownCommand = $serviceProcess.CommandLine -match [regex]::Escape($workspacePath + '\')
    if ((-not $ownInterpreter -and -not $ownCommand) -or
        ($servicePort -eq 8080 -and $serviceProcess.CommandLine -notmatch '(?:main\.py|uvicorn\s+main:app)') -or
        ($servicePort -ne 8080 -and $serviceProcess.CommandLine -notmatch 'qq_social\.worker')) {
        # The legacy port may have been taken by an unrelated app since upgrading.
        if ($servicePort -eq 8788) { continue }
        throw "Port $servicePort belongs to an unexpected process; nothing was stopped."
    }
    $targets += $serviceProcess
}
if ($CheckOnly) {
    Test-HomePages
    Write-Host 'Chat, kitchen and music box: OK.'
    Show-SiwcStatus
    exit 0
}

if (Test-Path -LiteralPath $siwcStarter -PathType Leaf) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $siwcStarter
} else {
    Write-Warning 'SIWC launcher is missing.'
}
if (-not $StartOnly) {
    foreach ($serviceProcess in $targets) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($serviceProcess.ProcessId)"
        if ($current -and $current.CreationDate -eq $serviceProcess.CreationDate) {
            Stop-Process -Id $serviceProcess.ProcessId -ErrorAction Stop
        }
    }
}
if (-not $StartOnly -or -not $targets) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $errorPath) | Out-Null
    $oldAgyProxy = $env:AION_AGY_PROXY
    $oldGeminiProxy = $env:AION_GEMINI_PROXY
    $oldPythonUtf8 = $env:PYTHONUTF8
    $oldPythonIoEncoding = $env:PYTHONIOENCODING
    try {
        $env:PYTHONUTF8 = '1'
        $env:PYTHONIOENCODING = 'utf-8:backslashreplace'
        if ((-not $env:AION_AGY_PROXY -or -not $env:AION_GEMINI_PROXY) -and
            (Get-NetTCPConnection -LocalPort 7897 -State Listen -ErrorAction SilentlyContinue)) {
            if (-not $env:AION_AGY_PROXY) { $env:AION_AGY_PROXY = 'http://127.0.0.1:7897' }
            if (-not $env:AION_GEMINI_PROXY) { $env:AION_GEMINI_PROXY = 'http://127.0.0.1:7897' }
        }
        $started = Start-Process -FilePath $pythonPath -ArgumentList '-u','main.py' -WorkingDirectory $serverPath -WindowStyle Hidden -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath -PassThru
    } finally {
        $env:AION_AGY_PROXY = $oldAgyProxy
        $env:AION_GEMINI_PROXY = $oldGeminiProxy
        $env:PYTHONUTF8 = $oldPythonUtf8
        $env:PYTHONIOENCODING = $oldPythonIoEncoding
    }
}

$deadline = (Get-Date).AddSeconds(55)
do {
    if ($started -and $started.HasExited) { throw "AionHome exited. See $errorPath" }
    try {
        Test-HomePages
        Write-Host "Chat, kitchen and music box: OK. $baseUrl"
        Show-SiwcStatus
        if (-not $StartOnly) {
            try {
                $status = Invoke-RestMethod "$baseUrl/api/qq-social/status" -TimeoutSec 3
                if ($null -eq $status.settings.bring_home) { throw 'Unexpected QQ status' }
                Write-Host 'QQ status endpoint: OK. Check account connection on the QQ page.'
            } catch {
                Write-Warning 'QQ status endpoint did not respond. Check the QQ page.'
            }
        }
        exit 0
    } catch {
        $lastFailure = $_.Exception.Message
    }
    if ($StartOnly -and $targets) { break }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)
throw "$lastFailure Check $errorPath or use the restart launcher if an older server is already running."

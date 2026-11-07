<#
.SYNOPSIS
    Re-runnable test entry point for AionsHome.

.DESCRIPTION
    Runs the Python suite (stdlib unittest) and/or the JavaScript suite
    (node:test) with the repository's local virtual environment.

    Tests are run from the aion-chat directory, which is the import root.
    Before importing the application, this script selects an empty temporary
    data directory via AIONSHOME_DATA_DIR (and config.py also falls back to a
    temporary data directory whenever it detects a unittest/pytest process),
    so test runs never touch aion-chat/data/.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-tests.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-tests.ps1 -Suite all

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-tests.ps1 -Name test_reply_timing

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-tests.ps1 -Filter "test_theater*"
#>
param(
    [ValidateSet('python', 'pytest', 'js', 'all')]
    [string]$Suite = 'python',

    # Python only: a single test module (fast inner loop).
    [string]$Name = '',

    # File-name glob without extension. Default runs every test_* file.
    [string]$Filter = 'test_*',

    # Stop at the first failing test file.
    [switch]$FailFast,

    # Only print the pass/fail summary.
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $workspace '.venv\Scripts\python.exe'
$serverDir = Join-Path $workspace 'aion-chat'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python environment missing: $python`nRun '一键安装环境.bat' first."
}
if (-not (Test-Path -LiteralPath $serverDir -PathType Container)) {
    throw "Server directory missing: $serverDir"
}

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'

$previousDataDir = $env:AIONSHOME_DATA_DIR
$testDataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("aionshome-tests-{0}-{1}" -f $PID, [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $testDataDir | Out-Null
$env:AIONSHOME_DATA_DIR = $testDataDir

function Invoke-Native {
    param([string]$Label, [string]$Exe, [string[]]$Arguments)

    Write-Host ""
    Write-Host "== $Label ==" -ForegroundColor Cyan
    Write-Host ("   {0} {1}" -f $Exe, ($Arguments -join ' ')) -ForegroundColor DarkGray

    # Native commands write progress/diagnostics to stderr. With
    # ErrorActionPreference=Stop, PowerShell 5.1 turns that into a
    # NativeCommandError record, so relax it for the duration of the call.
    # Streams are left alone: merging them makes stderr render as an
    # ErrorRecord type name instead of the original text.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Out-Host keeps the child process stdout visible instead of letting it
        # become part of this function's return value (which would swallow the
        # whole test report and leave only the exit code).
        & $Exe @Arguments | Out-Host
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function Invoke-PythonSuite {
    # unittest requires every discover option to come AFTER the 'discover'
    # keyword; '-q discover -s .' is rejected as unrecognized arguments.
    $verbosity = if ($Quiet) { '-q' } else { '-v' }
    $arguments = @('-m', 'unittest')

    if ($Name) {
        $arguments += $verbosity
        if ($FailFast) { $arguments += '-f' }
        $arguments += $Name
    }
    else {
        $arguments += @('discover', $verbosity, '-s', '.', '-p', "$Filter.py")
    }

    return Invoke-Native -Label 'Python / unittest' -Exe $python -Arguments $arguments
}

function Invoke-PytestSuite {
    # A handful of modules use @pytest.fixture / parametrize. unittest only
    # reports them as import errors, so their cases never run. Detect them
    # rather than hard-coding a list that will drift.
    $files = @(Get-ChildItem -LiteralPath $serverDir -Filter "$Filter.py" -File |
        Where-Object {
            (Get-Content -LiteralPath $_.FullName -Raw) -match '(?m)^\s*(import pytest|from pytest)'
        } |
        Sort-Object Name | ForEach-Object { $_.Name })

    if ($files.Count -eq 0) {
        Write-Host "No pytest-dependent module matched '$Filter.py'."
        return 0
    }

    $arguments = @('-m', 'pytest', '-q', '-p', 'no:cacheprovider')
    $arguments += $files

    return Invoke-Native -Label 'Python / pytest' -Exe $python -Arguments $arguments
}

function Invoke-JsSuite {
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) {
        Write-Warning 'node not found on PATH; skipping the JavaScript suite.'
        return 0
    }

    $files = @(Get-ChildItem -LiteralPath $serverDir -Filter "$Filter.js" -File |
        Sort-Object Name | ForEach-Object { $_.Name })
    if ($files.Count -eq 0) {
        Write-Warning "No JavaScript test file matched '$Filter.js'; skipping."
        return 0
    }

    $arguments = @('--test')
    $arguments += $files

    return Invoke-Native -Label 'JavaScript / node:test' -Exe $node.Source -Arguments $arguments
}

Push-Location -LiteralPath $serverDir
try {
    $started = Get-Date
    $failures = 0

    if ($Suite -eq 'python' -or $Suite -eq 'all') {
        if ((Invoke-PythonSuite) -ne 0) { $failures++ }
    }
    if ($Suite -eq 'pytest' -or $Suite -eq 'all') {
        if (-not ($FailFast -and $failures -gt 0)) {
            if ((Invoke-PytestSuite) -ne 0) { $failures++ }
        }
    }
    if ($Suite -eq 'js' -or $Suite -eq 'all') {
        # -FailFast means: do not spend time on the next suite once one failed.
        if (-not ($FailFast -and $failures -gt 0)) {
            if ((Invoke-JsSuite) -ne 0) { $failures++ }
        }
    }
}
finally {
    Pop-Location
    $env:AIONSHOME_DATA_DIR = $previousDataDir
    if ($testDataDir -and (Test-Path -LiteralPath $testDataDir)) {
        Remove-Item -LiteralPath $testDataDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$elapsed = [int]((Get-Date) - $started).TotalSeconds
Write-Host ""
if ($failures -eq 0) {
    Write-Host "RESULT: PASS (${elapsed}s)" -ForegroundColor Green
    exit 0
}
Write-Host "RESULT: FAIL - $failures suite(s) reported errors (${elapsed}s)" -ForegroundColor Red
exit 1

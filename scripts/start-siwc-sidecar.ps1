param(
    [int]$Port = 18765,
    [string]$SidecarRoot = "D:\AionsHome-siwc-sidecar",
    [string]$NodeDir = "D:\nodejs",
    [string]$Proxy = ""
)

$ErrorActionPreference = "Continue"

if (-not $Proxy) {
    if ($env:SIWC_PROXY) { $Proxy = $env:SIWC_PROXY }
    elseif ($env:HTTPS_PROXY) { $Proxy = $env:HTTPS_PROXY }
    elseif ($env:https_proxy) { $Proxy = $env:https_proxy }
    elseif ($env:HTTP_PROXY) { $Proxy = $env:HTTP_PROXY }
    elseif ($env:http_proxy) { $Proxy = $env:http_proxy }
    else { $Proxy = "http://127.0.0.1:7897" }
}

function Test-PortListening {
    param([int]$LocalPort)
    $conn = @(Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue)
    return ($conn.Count -gt 0)
}

if (Test-PortListening -LocalPort $Port) {
    Write-Host ("[siwc] Already listening on 127.0.0.1:{0} — skip start." -f $Port)
    exit 0
}

if (-not (Test-Path -LiteralPath (Join-Path $SidecarRoot "package.json"))) {
    Write-Host ("[siwc] Sidecar not found at {0} — skip." -f $SidecarRoot)
    exit 0
}

$nodeExe = Join-Path $NodeDir "node.exe"
if (-not (Test-Path -LiteralPath $nodeExe)) {
    Write-Host ("[siwc] Node not found at {0} — cannot start sidecar." -f $nodeExe)
    exit 1
}

Write-Host ("[siwc] Starting sidecar on port {0} (proxy {1})..." -f $Port, $Proxy)

# Minimized dedicated window so logs remain visible if something fails.
$inner = @"
`$Host.UI.RawUI.WindowTitle = 'SIWC sidecar'
`$env:Path = '$NodeDir;' + `$env:Path
`$env:HTTP_PROXY = '$Proxy'
`$env:HTTPS_PROXY = '$Proxy'
`$env:http_proxy = '$Proxy'
`$env:https_proxy = '$Proxy'
if (-not `$env:SIWC_PROXY) { `$env:SIWC_PROXY = '$Proxy' }
Set-Location -LiteralPath '$SidecarRoot'
npm run serve
"@

Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-NoExit",
    "-Command", $inner
) -WindowStyle Minimized | Out-Null

for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-PortListening -LocalPort $Port) {
        Write-Host ("[siwc] HTTP listening on http://127.0.0.1:{0}" -f $Port)
        exit 0
    }
}

Write-Host ("[siwc] Warning: port {0} not up yet; check the minimized SIWC window (Clash proxy / npm)." -f $Port)
exit 0

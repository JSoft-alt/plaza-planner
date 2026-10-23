# Full local app, including automatic OSRM, on a Linux engine on this Windows PC.
# Compatible with Windows PowerShell 5.1 and PowerShell 7. No global policy changes.
[CmdletBinding()]
param(
    [ValidateSet('start', 'stop', 'status', 'logs')][string]$Action = 'start',
    [ValidateSet('Auto', 'Docker', 'WSL')][string]$Backend = 'Auto',
    [string]$Distro = '',
    [switch]$NoBrowser,
    [ValidateRange(1, 600)][int]$StartupTimeoutSeconds = 120
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Engine = $null
$EnginePrefix = @()

function Invoke-Engine {
    param([string[]]$Arguments)
    & $script:Engine @script:EnginePrefix @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Container command failed (exit $LASTEXITCODE). Check the output above." }
}

function Select-LocalEngine {
    param([string]$Command, [string[]]$Prefix)
    $script:Engine = $Command
    $script:EnginePrefix = $Prefix
    # Do not silently send local calculations to a remote Docker context.
    $endpoint = (Invoke-Engine -Arguments @('context', 'inspect', '--format', '{{.Endpoints.docker.Host}}') | Out-String).Trim()
    if ($Command -ne 'wsl.exe' -and $env:DOCKER_HOST) { $endpoint = $env:DOCKER_HOST }
    if ($endpoint -notmatch '^(npipe:|unix:|tcp://(127\.0\.0\.1|localhost|\[::1\]):)') {
        throw 'Select a local Docker context. This launcher keeps route calculations on this PC.'
    }
    $os = (Invoke-Engine -Arguments @('info', '--format', '{{.OSType}}') | Out-String).Trim()
    if ($os -ne 'linux') { throw 'OSRM requires Linux containers. Switch the local engine to Linux containers.' }
    Invoke-Engine -Arguments @('compose', 'version') | Out-Host
}

Push-Location -LiteralPath $ProjectRoot
try {
    $failures = @()
    if ($Backend -ne 'WSL' -and (Get-Command docker -ErrorAction SilentlyContinue)) {
        try { Select-LocalEngine -Command 'docker' -Prefix @() }
        catch { $failures += $_.Exception.Message; $Engine = $null }
    }
    if (-not $Engine -and $Backend -ne 'Docker' -and (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        $prefix = @()
        if ($Distro) { $prefix += @('--distribution', $Distro) }
        $prefix += @('--exec', 'docker')
        try { Select-LocalEngine -Command 'wsl.exe' -Prefix $prefix }
        catch { $failures += $_.Exception.Message; $Engine = $null }
    }
    if (-not $Engine) {
        throw ('No running local Linux Docker engine with Compose was found. ' + ($failures -join ' ') +
            ' See deploy/WINDOWS.md: WSL2 + Docker Engine is supported; no paid API is required.')
    }
    $composeRoot = $ProjectRoot
    if ($Engine -eq 'wsl.exe') {
        $wslArgs = @()
        if ($Distro) { $wslArgs += @('--distribution', $Distro) }
        $wslArgs += @('--exec', 'wslpath', '-a', '-u', $ProjectRoot)
        $composeRoot = (& wsl.exe @wslArgs | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $composeRoot.StartsWith('/')) { throw 'Could not translate this folder into a WSL path.' }
    }
    # Fixed project identity preserves history if the extracted source folder is renamed.
    $compose = @('compose', '--project-name', 'plaza-planner', '--project-directory', $composeRoot,
        '--file', ($composeRoot.TrimEnd('/', '\') + '/compose.yaml'))
    switch ($Action) {
        'stop' { Invoke-Engine -Arguments ($compose + @('stop', 'planner')) }
        'status' { Invoke-Engine -Arguments ($compose + @('ps')) }
        'logs' { Invoke-Engine -Arguments ($compose + @('logs', '--tail', '100', 'planner')) }
        'start' {
            Invoke-Engine -Arguments ($compose + @('config', '--quiet'))
            Write-Host 'Starting the local planner. First build downloads dependencies; road data is prepared when needed.'
            Invoke-Engine -Arguments ($compose + @('up', '-d', '--build', 'planner'))
            $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
            $ready = $false
            do {
                try {
                    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8011/api/bootstrap' -TimeoutSec 3
                    $ready = [bool]($health.csrf -and $health.max_stops)
                } catch { $ready = $false }
                if (-not $ready) { Start-Sleep -Seconds 1 }
            } while (-not $ready -and (Get-Date) -lt $deadline)
            if (-not $ready) {
                Invoke-Engine -Arguments ($compose + @('logs', '--tail', '40', 'planner'))
                throw 'The app did not become ready. History is retained. See the logs above and deploy/WINDOWS.md.'
            }
            Write-Host 'Ready: http://127.0.0.1:8011/  (close this window safely; the container keeps running)'
            Write-Host 'Stop without deleting history: run-windows.cmd -Action stop'
            if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8011/' }
        }
    }
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally { Pop-Location }

# Run under Windows PowerShell 5.1 or pwsh. All container/network/browser calls
# here are stubs; the Python portability test exercises actual app processes.
[CmdletBinding()]
param([string]$Scenario = 'all')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $root 'scripts/windows.ps1'
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($launcher, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }

if ($Scenario -eq 'all') {
    $executable = (Get-Process -Id $PID).Path
    foreach ($case in @('docker', 'wsl', 'stop', 'missing', 'remote', 'engine-failure')) {
        & $executable -NoLogo -NoProfile -File $PSCommandPath -Scenario $case
        if ($LASTEXITCODE -ne 0) { throw "Windows launcher check failed: $case" }
    }
    Write-Host 'Windows launcher parser and six behavior scenarios passed.'
    exit 0
}

$global:PlannerTestCalls = @()
$global:PlannerTestBrowserOpened = $false
function Get-Command {
    param([string]$Name, $ErrorAction)
    if ($Scenario -eq 'missing') { return $null }
    if ($Name -eq 'docker' -and $Scenario -eq 'wsl') { return $null }
    return [pscustomobject]@{Name=$Name}
}
function docker {
    $global:PlannerTestCalls += ,@($args)
    $global:LASTEXITCODE = 0
    $joined = $args -join ' '
    if ($joined -like 'context inspect*') {
        if ($Scenario -eq 'remote') { return 'ssh://remote.example' }
        return 'unix:///var/run/docker.sock'
    }
    if ($joined -like 'info*') { return 'linux' }
    if ($joined -like 'compose version*') { return 'Docker Compose test stub' }
    if ($joined -like '*up -d --build planner*' -and $Scenario -eq 'engine-failure') { $global:LASTEXITCODE=17 }
}
function wsl.exe {
    $global:PlannerTestCalls += ,@('WSL' + @($args))
    $global:LASTEXITCODE = 0
    if (($args -join ' ') -like '*wslpath*') { return '/mnt/c/QA folder/office-and-company' }
    $dockerIndex = [array]::IndexOf($args, 'docker')
    $forward = $args[($dockerIndex+1)..($args.Count-1)]
    docker @forward
}
function Invoke-RestMethod { return [pscustomobject]@{csrf='test';max_stops=15} }
function Start-Process { $global:PlannerTestBrowserOpened=$true }

$env:DOCKER_HOST = ''
$env:DOCKER_CONTEXT = ''
$original = (Get-Location).Path
$parameters = @{NoBrowser=$true}
if ($Scenario -eq 'wsl') { $parameters.Backend='WSL';$parameters.Distro='Test Ubuntu' }
if ($Scenario -eq 'stop') { $parameters.Action='stop' }
if ($Scenario -eq 'missing' -or $Scenario -eq 'remote' -or $Scenario -eq 'engine-failure') { $parameters.Backend='Docker' }
& $launcher @parameters
$result = $LASTEXITCODE
if ((Get-Location).Path -ne $original) { throw 'Launcher changed the calling directory.' }
if ($global:PlannerTestBrowserOpened) { throw 'NoBrowser was ignored.' }
$commands = ($global:PlannerTestCalls | ForEach-Object { $_ -join ' ' }) -join "`n"
if ($commands -match 'down.*-v') { throw 'History volume must not be deleted.' }
if ($Scenario -in @('missing','remote','engine-failure')) {
    if ($result -eq 0) { throw 'Expected a nonzero error exit.' }
} elseif ($Scenario -eq 'stop') {
    if ($commands -notmatch 'stop planner' -or $commands -match 'up -d') { throw 'Stop did not preserve the existing stack.' }
} else {
    if ($commands -notmatch '--project-name plaza-planner' -or $commands -notmatch 'up -d --build planner') { throw 'Planner startup command is incomplete.' }
    if ($Scenario -eq 'wsl' -and $commands -notmatch '--project-directory /mnt/c/QA folder/office-and-company') { throw 'WSL path conversion was not used.' }
}
Write-Host "Launcher scenario passed: $Scenario"
exit 0

param([ValidateSet('Database', 'Interface')][string]$Mode = 'Database')

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$local = Join-Path $root '.local'
$envFile = Join-Path $root '.env'
$composeFile = Join-Path $root 'compose.yaml'
$logging = $false

function First-File([string[]]$Paths) {
    foreach ($path in $Paths) {
        if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) { return $path }
    }
}

function Get-EngineType([string]$Docker) {
    $ErrorActionPreference = 'SilentlyContinue'
    $probe = & $Docker info --format '{{.OSType}}' 2>$null
    if ($LASTEXITCODE -eq 0) { return ($probe -join '').Trim() }
}

try {
    New-Item -ItemType Directory -Path $local -Force | Out-Null
    Start-Transcript -LiteralPath (Join-Path $local 'start.log') -Append | Out-Null
    $logging = $true
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        throw 'Create .env from .env.example and fill in the password. See README.md.'
    }
    $dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
    $docker = First-File @($dockerCommand.Source, 'D:\Docker\resources\bin\docker.exe', "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe")
    if (-not $docker) { throw 'Docker CLI not found. Install Docker Desktop first.' }

    Write-Host 'Waiting for Docker Desktop...'
    $engine = Get-EngineType $docker
    if (-not $engine) {
        if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
            $desktop = First-File @($env:BANKIRU_DOCKER_DESKTOP, 'D:\Docker\Docker Desktop.exe', "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe")
            if (-not $desktop) { throw 'Open Docker Desktop manually, wait for the engine and retry.' }
            Start-Process -FilePath $desktop -WindowStyle Hidden
        }
        $deadline = (Get-Date).AddMinutes(3)
        do {
            Start-Sleep -Seconds 2
            $engine = Get-EngineType $docker
        } until ($engine -or (Get-Date) -ge $deadline)
    }
    if (-not $engine) { throw 'Docker Desktop did not become ready. Check it and try again.' }
    if ($engine -ne 'linux') { throw 'Switch Docker Desktop to Linux containers, then retry.' }

    $compose = @('compose', '--env-file', $envFile, '-f', $composeFile)
    if ($Mode -eq 'Interface') {
        $compose += @('-f', (Join-Path $root 'infra\compose.web.yaml'))
        Write-Host 'Starting PostgreSQL and building the web interface...'
        & $docker @compose up -d --build --wait --wait-timeout 120 web
        if ($LASTEXITCODE -ne 0) { throw 'The web interface did not start. See the Compose output above.' }
        $mapping = & $docker @compose port web 5000
        if ($LASTEXITCODE -ne 0 -or ($mapping -join '') -notmatch ':(\d+)$') {
            throw 'The web interface started but its port could not be determined.'
        }
        $url = 'http://localhost:' + $Matches[1] + '/'
        Write-Host "Open $url"
        Start-Process $url
    } else {
        Write-Host 'Starting PostgreSQL...'
        & $docker @compose up -d --wait --wait-timeout 120 postgres
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL did not start. See the Compose output above.' }
        $dbeaver = First-File @($env:BANKIRU_DBEAVER, 'D:\DBeaver\dbeaver.exe', "$env:ProgramFiles\DBeaver\dbeaver.exe", "$env:LOCALAPPDATA\DBeaver\dbeaver.exe")
        if ($dbeaver) {
            if (-not (Get-Process -Name 'dbeaver' -ErrorAction SilentlyContinue)) {
                Start-Process -FilePath $dbeaver -WindowStyle Normal
            }
        } else {
            Write-Host 'DBeaver was not found. Open it manually.'
        }
        Write-Host 'PostgreSQL is ready. Open your saved connection in DBeaver.'
    }
} catch {
    Write-Host ('Startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('Log: ' + (Join-Path $local 'start.log'))
    exit 1
} finally {
    if ($logging) { Stop-Transcript | Out-Null }
}

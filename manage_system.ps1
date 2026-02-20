param(
    [string]$Action = "help"
)

$LogDir = "logs"

function Clean-Logs {
    Write-Host "Cleaning logs..."
    if (Test-Path $LogDir) {
        Remove-Item "$LogDir\*" -Force -Recurse -ErrorAction SilentlyContinue
    } else {
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    }
    Write-Host "Logs cleaned."
}

function Dump-Logs {
    Write-Host "Dumping container logs to $LogDir..."
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
    
    $services = @("rcs-postgres", "rcs-rabbitmq", "rcs-redis", "rcs-jaeger", "rcs-prometheus")
    foreach ($svc in $services) {
        Write-Host "  Processing $svc..."
        # Using a slightly complex redirection to ensure UTF8 and handle stderr
        # PowerShell redirection is tricky with encodings, using cmd /c for raw pipe
        cmd /c "docker logs $svc > ""$LogDir\$svc.log"" 2>&1"
    }
    Write-Host "Logs dumped successfully to $LogDir"
}

function Run-Test {
    Write-Host "Running End-to-End Mock Test..."
    # Ensure venv is active or python is available
    python test_e2e_mock.py
}

if ($Action -eq "clean") {
    Clean-Logs
}
elseif ($Action -eq "start") {
    Write-Host "Starting Docker Compose services..."
    docker-compose up -d
    Write-Host "Services started."
}
elseif ($Action -eq "stop") {
    Write-Host "Stopping Docker Compose services..."
    docker-compose down
}
elseif ($Action -eq "logs") {
    Dump-Logs
}
elseif ($Action -eq "test") {
    Clean-Logs
    Run-Test
    Dump-Logs
    Write-Host "Test complete. Logs are available in the '$LogDir' directory."
}
elseif ($Action -eq "fix-prometheus") {
    Write-Host "Prometheus fix (directory removal) should have been handled manually or by agent."
    docker-compose restart prometheus
}
else {
    Write-Host "Usage: .\manage_system.ps1 [clean|start|stop|logs|test]"
    Write-Host "  clean : Remove old logs"
    Write-Host "  start : Start docker services"
    Write-Host "  stop  : Stop docker services"
    Write-Host "  logs  : Dump current container logs to files"
    Write-Host "  test  : Clean logs, Run test file, and Dump new logs"
}

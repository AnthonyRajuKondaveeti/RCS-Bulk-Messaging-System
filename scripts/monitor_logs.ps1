# scripts/monitor_logs.ps1
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "   RCS SYSTEM LOG MONITOR" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

# 1. Start Docker Logs in a new window
Write-Host "Starting Docker logs in a new window..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "docker-compose logs -f"

# 2. Tail the local test log in THIS window
$LogFile = "logs/test_local.log"

# Create/Clear the log file
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
Set-Content -Path $LogFile -Value "" 

Write-Host "Streaming local test logs ($LogFile)..." -ForegroundColor Green
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray

Get-Content -Path $LogFile -Wait

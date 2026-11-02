# Start the isolated React + Python TikTok photo-carousel studio on Windows.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw 'Python не найден в PATH.'
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw 'Нужен Node.js с npm (Node.js 22 или новее).'
}
Write-Host 'Собираем React/TypeScript интерфейс...'
Push-Location (Join-Path $root 'carousel-studio')
try {
  npm install --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'Ошибка установки React-зависимостей.' }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw 'Сборка TypeScript завершилась ошибкой.' }
} finally { Pop-Location }

Write-Host 'Проверяем gallery-dl и Pillow...'
python -c "import PIL, gallery_dl"
if ($LASTEXITCODE -ne 0) {
  python -m pip install Pillow gallery-dl
  if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости TikTok-каруселей.' }
}

$up = $false
try {
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/studio/readiness' -TimeoutSec 3 -UseBasicParsing
  $up = $r.StatusCode -eq 200
} catch { $up = $false }

if (-not $up) {
  Write-Host 'Запускаем локальный backend Content Machine...'
  $env:CM_NO_BROWSER = '1'
  Start-Process -FilePath 'python' -ArgumentList 'app.py' -WorkingDirectory $root
  Start-Sleep -Seconds 3
}
Start-Process 'http://127.0.0.1:8765/carousel-studio/'
Write-Host 'TikTok Studio: http://127.0.0.1:8765/carousel-studio/'
Write-Host 'Результаты: marketing-qa\carousel-studio\'

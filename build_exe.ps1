<#
    build_exe.ps1 - compila randomcutt a un .exe autocontenido (Windows).

    Uso:
        powershell -ExecutionPolicy Bypass -File build_exe.ps1
        powershell -ExecutionPolicy Bypass -File build_exe.ps1 -Clean

    Crea un venv aislado en .venv-build (no toca tu Python base), instala
    PyInstaller ahí y deja el ejecutable en dist\randomcutt.exe.
    ffmpeg NO se empaqueta: la app lo busca en el PATH al iniciar.

    Este archivo va en UTF-8 con BOM: sin BOM, Windows PowerShell 5.1 lo lee
    como ANSI y las tildes de los mensajes salen corruptas.
#>
param(
    [switch]$Clean,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venv = Join-Path $root ".venv-build"
$vpy  = Join-Path $venv "Scripts\python.exe"

if ($Clean) {
    foreach ($d in @("build", "dist", $venv)) {
        if (Test-Path $d) { Write-Host "limpiando $d"; Remove-Item $d -Recurse -Force }
    }
    Get-ChildItem -Filter "*.spec" | Remove-Item -Force -ErrorAction SilentlyContinue
}

# --- Python base -----------------------------------------------------------
if (-not $Python) {
    foreach ($c in @("python", "py")) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike "*WindowsApps*") { $Python = $cmd.Source; break }
    }
}
if (-not $Python) {
    $guess = "$env:USERPROFILE\miniconda3\python.exe"
    if (Test-Path $guess) { $Python = $guess }
}
if (-not $Python) { throw "No encontré un Python real. Indica uno con -Python C:\ruta\python.exe" }
Write-Host "Python base: $Python"

# --- venv de build ---------------------------------------------------------
if (-not (Test-Path $vpy)) {
    Write-Host "creando venv de build ..."
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "falló la creación del venv" }
}
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install --upgrade pyinstaller --quiet
if ($LASTEXITCODE -ne 0) { throw "falló la instalación de PyInstaller" }
& $vpy -c "import tkinter"
if ($LASTEXITCODE -ne 0) { throw "ese Python no incluye tkinter; usa otro con -Python" }

# Anaconda/Miniconda guardan tcl86t.dll y tk86t.dll en Library\bin, no en DLLs.
# Sin esto PyInstaller no las empaqueta y el .exe falla al iniciar con
# "ImportError: DLL load failed while importing _tkinter".
$condaBin = Join-Path (Split-Path -Parent $Python) "Library\bin"
if (Test-Path (Join-Path $condaBin "tk86t.dll")) {
    Write-Host "layout conda detectado -> agregando $condaBin al PATH"
    $env:PATH = "$condaBin;$env:PATH"
}

# La caché de Analysis no detecta los cambios de PATH: empezar siempre desde cero.
if (Test-Path "build") { Remove-Item "build" -Recurse -Force }

# Un randomcutt.exe abierto bloquea el archivo de salida.
Get-Process randomcutt -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "cerrando randomcutt.exe en uso (pid $($_.Id))"
    Stop-Process -Id $_.Id -Force
}

# --- build -----------------------------------------------------------------
# $args es una variable automática de PowerShell: no reutilizar ese nombre.
$pyiArgs = @(
    "--noconfirm", "--onefile", "--windowed",
    "--name", "randomcutt",
    "--icon", "randomcutt.ico",
    "--add-data", "randomcutt.ico;.",
    "--exclude-module", "numpy",
    "--exclude-module", "PIL",
    "--exclude-module", "pytest",
    "--exclude-module", "setuptools",
    "randomcutt_v007.py"
)
Write-Host "compilando ..."
& $vpy -m PyInstaller @pyiArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló" }

$exe = Join-Path $root "dist\randomcutt.exe"
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "OK -> $exe  ($mb MB)" -ForegroundColor Green

<#
 KupkaProd Cinema Pipeline - one-shot setup for a Windows PC (Comfy Desktop + RTX 3090).

   powershell -ExecutionPolicy Bypass -File .\setup.ps1
 Options:
   -SkipModels      don't pull/verify Ollama models
   -CreativeModel   Ollama model for scene planning/writing (default: bonsai-2)
   -FastModel       Ollama model for image/take evaluation, needs vision (default: bonsai-2)
   -ForceSettings   overwrite an existing video_director_agent\user_settings.json
#>
param([switch]$SkipModels, [switch]$ForceSettings,
      [string]$CreativeModel = 'bonsai-2', [string]$FastModel = 'bonsai-2')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$ComfyRoot = 'D:\ComfyUI\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI'
$ComfyHost = '127.0.0.1:8188'
$Venv      = Join-Path $PSScriptRoot '.venv'
$Py        = Join-Path $Venv 'Scripts\python.exe'

function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

# 1. Python venv ----------------------------------------------------------------
Step 'Python environment (.venv, Python 3.12)'
if (-not (Test-Path $Py)) {
  if (Have 'uv') {
    uv venv $Venv --python 3.12
  } elseif (Have 'py') {
    py -3.12 -m venv $Venv
  } else {
    throw 'Neither uv nor the py launcher was found. Install uv: winget install astral-sh.uv'
  }
}
if (Have 'uv') {
  uv pip install --python $Py -r requirements.txt
} else {
  & $Py -m pip install --upgrade pip
  & $Py -m pip install -r requirements.txt
}
& $Py -c "import tkinter, cv2, websocket, ollama, fastapi; print('python deps OK')"

# 2. FFmpeg (ffmpeg + ffprobe are both required for assembly) -------------------
Step 'FFmpeg'
$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
$ffprobe = (Get-Command ffprobe -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg -or -not $ffprobe) {
  Write-Host 'Installing FFmpeg via winget (Gyan.FFmpeg)...'
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements | Out-Host
  $links = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'
  if (Test-Path "$links\ffmpeg.exe")  { $ffmpeg  = "$links\ffmpeg.exe" }
  if (Test-Path "$links\ffprobe.exe") { $ffprobe = "$links\ffprobe.exe" }
  if (-not $ffprobe) {
    $found = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffprobe.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { $ffprobe = $found.FullName; $ffmpeg = Join-Path $found.DirectoryName 'ffmpeg.exe' }
  }
}
if (-not $ffmpeg -or -not $ffprobe) { throw 'FFmpeg/ffprobe not found after install. Open a new terminal and re-run.' }
Write-Host "ffmpeg : $ffmpeg`nffprobe: $ffprobe"

# 3. Ollama models ---------------------------------------------------------------
Step 'Ollama'
$creative = $CreativeModel
$fast = $FastModel
if (-not (Have 'ollama')) { throw 'Ollama CLI not on PATH. Install from https://ollama.com/download and re-run.' }
if (-not $SkipModels) {
  foreach ($m in @($fast, $creative) | Select-Object -Unique) { ollama pull $m }
}

# 4. Comfy Desktop launcher (used only if ComfyUI is not already running) -------
Step 'Comfy Desktop'
$launcher = ''
$exe = Get-ChildItem "$env:LOCALAPPDATA\Programs" -Recurse -Depth 2 -Filter '*.exe' -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match 'comfy' -and $_.Name -notmatch 'uninstall' } | Select-Object -First 1
if ($exe) { $launcher = $exe.FullName; Write-Host "launcher: $launcher" }
else { Write-Host 'Comfy Desktop exe not found - start it manually before a production run.' -ForegroundColor Yellow }

# 5. user_settings.json ------------------------------------------------------------
Step 'Settings'
$settingsPath = 'video_director_agent\user_settings.json'
if ((Test-Path $settingsPath) -and -not $ForceSettings) {
  Write-Host "$settingsPath exists - kept (use -ForceSettings to regenerate)."
} else {
  $settings = [ordered]@{
    comfyui_root            = $ComfyRoot
    comfyui_output_dir      = 'output'   # files are fetched via ComfyUI /view if not found here
    comfyui_host            = $ComfyHost
    comfyui_hosts           = @($ComfyHost)
    comfyui_launcher        = $launcher
    comfyui_startup_timeout = 240
    ffmpeg_path             = $ffmpeg
    ffprobe_path            = $ffprobe
    llm_provider            = 'ollama'
    llm_base_url            = 'http://localhost:11434'
    ollama_host             = 'http://localhost:11434'
    ollama_model_creative   = $creative
    ollama_model_fast       = $fast
    takes_per_scene         = 2
    scene_min_sec           = 2
    scene_max_sec           = 12
    kf_width                = 1536
    kf_height               = 832
    video_width             = 1024
    video_height            = 576
  }
  $json = $settings | ConvertTo-Json -Depth 4
  [IO.File]::WriteAllText((Join-Path $PSScriptRoot $settingsPath), $json, (New-Object Text.UTF8Encoding $false))
  Write-Host "wrote $settingsPath"
}

# 6. Validate templates against the running ComfyUI ------------------------------
Step 'ComfyUI template check'
& $Py tools\check_comfy.py
if ($LASTEXITCODE -eq 2) { Write-Host 'ComfyUI not running - start Comfy Desktop and run: .venv\Scripts\python tools\check_comfy.py' -ForegroundColor Yellow }

Write-Host "`nDone. Launch with start.bat (desktop UI) or start-web.bat (browser UI on http://localhost:8010)." -ForegroundColor Green

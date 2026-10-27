# GPU-only PP-OCRv6 small runtime for the standalone Windows EXE.
# Run from the repository checkout on Windows with Python 3.12 available.
param(
    [ValidateSet('cu118','cu126')][string]$CudaIndex='cu126'
)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$base=Join-Path $env:LOCALAPPDATA 'PokemonChampionsAssistant\gpu-ocr-venv'
& py -3.12 -m venv $base
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 required' }
$python=Join-Path $base 'Scripts\python.exe'
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }
& $python -m pip install paddlepaddle-gpu==3.2.0 -i "https://www.paddlepaddle.org.cn/packages/stable/$CudaIndex/"
if ($LASTEXITCODE -ne 0) { throw 'Paddle GPU wheel installation failed' }
& $python -m pip install "$repo[gpu-ocr]"
if ($LASTEXITCODE -ne 0) { throw 'PaddleOCR installation failed' }
& $python -c "from champions_assist.ocr_gpu import PaddleV6SmallGpuOcr; PaddleV6SmallGpuOcr().warmup(); print('GPU_OCR_READY')"
if ($LASTEXITCODE -ne 0) { throw 'GPU unavailable or PP-OCRv6 small model initialization failed' }
[Environment]::SetEnvironmentVariable('CHAMPIONS_OCR_PYTHON',$python,'User')
Write-Host "GPU OCR installed. Restart the GUI to load CHAMPIONS_OCR_PYTHON."

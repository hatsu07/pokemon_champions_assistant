# Trusted Windows CI only: stage Japanese OCR for portable DEVELOPMENT GUI.
$ErrorActionPreference = 'Stop'
& choco install tesseract --yes --no-progress --limit-output
if ($LASTEXITCODE -ne 0) { throw "Tesseract install failed" }

$roots = @(
  (Join-Path $env:ProgramFiles 'Tesseract-OCR'),
  (Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Tesseract-OCR')
)
$root = $roots | Where-Object { Test-Path (Join-Path $_ 'tesseract.exe') } | Select-Object -First 1
if (-not $root) { throw "Tesseract binary missing" }

$out = Join-Path (Resolve-Path .).Path 'build\ocr-runtime'
New-Item -Force -ItemType Directory -Path (Join-Path $out 'tessdata') | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'tesseract.exe') -Destination $out -Force
Get-ChildItem -LiteralPath $root -File -Filter '*.dll' | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $out -Force
}
$english = Join-Path $root 'tessdata\eng.traineddata'
if (-not (Test-Path $english)) { throw "English tessdata missing" }
Copy-Item -LiteralPath $english -Destination (Join-Path $out 'tessdata\eng.traineddata') -Force

$jp = Join-Path $out 'tessdata\jpn.traineddata'
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/jpn.traineddata' -OutFile $jp
if ((Get-Item -LiteralPath $jp).Length -lt 1000000) {
  throw "Japanese tessdata download incomplete"
}
$env:TESSDATA_PREFIX = Join-Path $out 'tessdata'
$check = & (Join-Path $out 'tesseract.exe') --list-langs 2>&1 | Out-String
Write-Host $check
if ($check -notmatch '(?m)^jpn\s*$' -or $check -notmatch '(?m)^eng\s*$') {
  throw "Packaged OCR lacks jpn or eng"
}
$notice = @'
Tesseract OCR: https://github.com/tesseract-ocr/tesseract
tessdata_fast/jpn.traineddata: https://github.com/tesseract-ocr/tessdata_fast
Tesseract and tessdata_fast use Apache-2.0 licensing.
'@
Set-Content -LiteralPath (Join-Path $out 'OCR_SOURCES.txt') -Value $notice -Encoding UTF8
Write-Host "Staged Japanese OCR in $out"

# Trusted Windows CI only: stage Japanese OCR for portable DEVELOPMENT GUI.
$ErrorActionPreference = 'Stop'
$roots = @(
  (Join-Path $env:ProgramFiles 'Tesseract-OCR'),
  (Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Tesseract-OCR')
)
$root = $roots | Where-Object { Test-Path (Join-Path $_ 'tesseract.exe') } | Select-Object -First 1
if (-not $root) {
  # Chocolatey's community NuGet feed can return transient HTTP 503. Never
  # treat the choco exit code alone as success; verify the binary on disk.
  for ($attempt=1; $attempt -le 4; $attempt++) {
    Write-Host "Tesseract package install attempt $attempt of 4"
    & choco install tesseract --yes --no-progress --limit-output
    $root = $roots | Where-Object { Test-Path (Join-Path $_ 'tesseract.exe') } | Select-Object -First 1
    if ($root) { break }
    if ($attempt -lt 4) { Start-Sleep -Seconds (5 * $attempt) }
  }
}
if (-not $root) { throw "Tesseract binary missing after retry" }

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
# --list-langs alone does not prove that OCR can actually load both models
# and process an image with bundled DLLs. Exercise the native binary offline.
$smoke = Join-Path $env:RUNNER_TEMP 'champions-ocr-smoke.pgm'
$rows = @('P2','32 16','255')
for ($y=0; $y -lt 16; $y++) {
  $rows += ((@(0..31 | ForEach-Object { if ($y -ge 4 -and $y -le 11 -and $_ -ge 8 -and $_ -le 23) { '0' } else { '255' } })) -join ' ')
}
Set-Content -LiteralPath $smoke -Value $rows -Encoding Ascii
# Use the SAME config-free TSV mode as the Python adapter. No packaged
# configs/tsv is required. Prevent a zero-exit plain-text result from being
# accepted as if the OCR parser could consume it.
$smokeText = & (Join-Path $out 'tesseract.exe') $smoke stdout -l jpn+eng --tessdata-dir (Join-Path $out 'tessdata') --psm 6 -c tessedit_create_tsv=1 -c tessedit_create_txt=0 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
  throw "Bundled OCR native TSV invocation failed"
}
if ($smokeText -notmatch 'level\s+page_num\s+block_num' -or
    $smokeText -notmatch '\bconf\s+text\b') {
  throw "Bundled OCR did not emit a TSV header"
}
Remove-Item -LiteralPath $smoke -Force

$notice = @'
Tesseract OCR: https://github.com/tesseract-ocr/tesseract
tessdata_fast/jpn.traineddata: https://github.com/tesseract-ocr/tessdata_fast
Tesseract and tessdata_fast use Apache-2.0 licensing.
'@
Set-Content -LiteralPath (Join-Path $out 'OCR_SOURCES.txt') -Value $notice -Encoding UTF8
Write-Host "Staged Japanese OCR in $out"

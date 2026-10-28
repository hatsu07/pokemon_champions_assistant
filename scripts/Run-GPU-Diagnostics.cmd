@echo off
setlocal
echo Pokemon Champions - offline GPU stability diagnostics
echo Running 4 cases (OCR recognizer / classifier / serial / parallel).
echo Each case runs in an isolated process using DirectML GPU 0.
echo No game capture or ChatGPT API traffic is used.
echo WARNING: Native NVIDIA/DirectML crashes are expected during diagnosis.
echo Save other work before proceeding.
echo.
echo This launcher runs 3 independent trials per case, 20 inferences each.
echo Extra options can be passed to this CMD, e.g. --gpu-id 1 --iterations 100.
echo.
start "" /wait "%~dp0PokemonChampionsAssistant.exe" --gpu-diagnostics --gpu-id 0 --iterations 20 --trials 3 %*
set "RESULT=%ERRORLEVEL%"
echo.
echo Diagnostic launcher completed with exit code %RESULT%.
echo Reports are stored under:
echo %LOCALAPPDATA%\PokemonChampionsAssistant\diagnostics\gpu-stress-*
echo.
if exist "%LOCALAPPDATA%\PokemonChampionsAssistant\diagnostics" start "" "%LOCALAPPDATA%\PokemonChampionsAssistant\diagnostics"
pause
exit /b %RESULT%

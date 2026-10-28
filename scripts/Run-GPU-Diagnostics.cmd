@echo off
setlocal
echo Pokemon Champions - offline GPU stability diagnostics
echo Phase 1: E only, two independent processes running GPU inference together.
echo E runs TWO synchronized child EXEs on DirectML GPU 0.
echo No game capture or ChatGPT API traffic is used.
echo WARNING: Native NVIDIA/DirectML crashes are expected during diagnosis.
echo Save other work before proceeding.
echo.
echo This launcher runs 3 independent E trials, 20 inferences each.
echo To compare all five modes, pass --gpu-diagnostic-modes rec,classifier,serial,parallel,multiprocess
echo.
start "" /wait "%~dp0PokemonChampionsAssistant.exe" --gpu-diagnostics --gpu-id 0 --gpu-diagnostic-modes multiprocess --iterations 20 --trials 3 %*
set "RESULT=%ERRORLEVEL%"
echo.
echo Diagnostic launcher completed with exit code %RESULT%.
echo Reports are stored under:
echo %LOCALAPPDATA%\PokemonChampionsAssistant\diagnostics\gpu-stress-*
echo.
if exist "%LOCALAPPDATA%\PokemonChampionsAssistant\diagnostics" start "" "%LOCALAPPDATA%\PokemonChampionsAssistant\diagnostics"
pause
exit /b %RESULT%

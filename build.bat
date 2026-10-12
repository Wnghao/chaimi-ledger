@echo off
chcp 65001 >nul
rem build.bat - lulu-ledger pack (source of truth: lulu-ledger-fn\app\src)
rem
rem Usage:
rem   build.bat    - keep manifest version (no auto bump), package .fpk
rem
rem Workflow: edit files under lulu-ledger-fn\app\src\ , then run build.bat .
rem NOTE: keep ALL lines pure ASCII - cmd pre-reads the file head under the OEM
rem       codepage and mis-splits non-ASCII lines into bogus commands.
setlocal EnableExtensions

rem === [1/3] read manifest version ===
set "MANIFEST=%~dp0lulu-ledger-fn\manifest"
set "CURVER="
for /f "tokens=2 delims==" %%V in ('findstr /b /i "version=" "%MANIFEST%"') do set "CURVER=%%V"
if not defined CURVER goto verfail
echo version: %CURVER%
goto ver_ok
:verfail
echo [error] version field not found in manifest. & pause & exit /b 1
:ver_ok

rem === [1.5/3] bake version into static (device tree has no manifest; server reads this) ===
echo version=%CURVER%> "%~dp0lulu-ledger-fn\app\static\version.txt"

rem === [2/3] self-check: syntax compile + required files ===
set "SRC=%~dp0lulu-ledger-fn\app\src"
set "STATIC=%~dp0lulu-ledger-fn\app\static"
python -c "import glob,py_compile,sys;[py_compile.compile(f,doraise=True) for f in glob.glob(r'%SRC%\*.py')]" || (echo [error] py_compile failed. & pause & exit /b 1)
for %%F in ("%SRC%\server.py" "%SRC%\requirements.txt" "%STATIC%\app.html" "%~dp0lulu-ledger-fn\app\server\config") do (
    if not exist %%F (echo [error] missing %%F & pause & exit /b 1)
)
rd /s /q "%SRC%\__pycache__" >nul 2>nul

rem === [2.5/3] clean dev runtime data (don't leak into .fpk) ===
if exist "%~dp0lulu-ledger-fn\app\data\lulu.db*" del /q "%~dp0lulu-ledger-fn\app\data\lulu.db*" >nul 2>nul
rem local tests write receipts into app\data\receipts (config.receipts_dir dev fallback)
if exist "%~dp0lulu-ledger-fn\app\data\receipts\*" del /q "%~dp0lulu-ledger-fn\app\data\receipts\*" >nul 2>nul

rem === [2.6/3] unit tests (packing is blocked on failure) ===
echo [2.6/3] running unit tests ...
pushd "%~dp0"
python -m unittest discover -s tests -q
set "TEST_ERR=%errorlevel%"
popd
if not "%TEST_ERR%"=="0" ( echo [error] unit tests failed. & pause & exit /b 1 )

rem === [3/3] fnpack ===
echo [3/3] fnpack packing ...
pushd "%~dp0"
if not exist fnpack.exe ( popd & echo [error] fnpack.exe not found. & pause & exit /b 1 )
.\fnpack.exe build lulu-ledger-fn
set "PACK_ERR=%errorlevel%"
popd
if not "%PACK_ERR%"=="0" ( echo [error] fnpack failed. & pause & exit /b 1 )

if exist "%~dp0lulu-ledger.fpk" (
    move /y "%~dp0lulu-ledger.fpk" "%~dp0lulu-ledger.%CURVER%.fpk" >nul
)
for %%F in ("%~dp0lulu-ledger.*.fpk") do (
    if /i not "%%~nxF"=="lulu-ledger.%CURVER%.fpk" del /f /q "%%F" >nul 2>nul
)
if exist "%~dp0lulu-ledger.%CURVER%.fpk" (
    echo done: lulu-ledger.%CURVER%.fpk
) else (
    echo [warn] lulu-ledger.fpk not found, check fnpack output.
)
echo.
echo all done.
ping -n 3 127.0.0.1 >nul
endlocal

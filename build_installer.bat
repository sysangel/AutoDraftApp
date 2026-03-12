@echo off
setlocal
echo ============================================
echo  draft.ai - Build Windows Installer
echo ============================================
echo.

cd /d %~dp0
call venv\Scripts\activate

echo Installing/updating build tools...
pip install pyinstaller pywebview pywin32 -q

echo.
echo Building draft.ai with PyInstaller...

pyinstaller --onedir ^
  --name "DraftAI" ^
  --icon NONE ^
  --noconsole ^
  --collect-all webview ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --hidden-import "uvicorn.logging" ^
  --hidden-import "uvicorn.loops" ^
  --hidden-import "uvicorn.loops.auto" ^
  --hidden-import "uvicorn.protocols" ^
  --hidden-import "uvicorn.protocols.http" ^
  --hidden-import "uvicorn.protocols.http.auto" ^
  --hidden-import "uvicorn.protocols.websockets.auto" ^
  --hidden-import "uvicorn.lifespan" ^
  --hidden-import "uvicorn.lifespan.on" ^
  --hidden-import "apscheduler.schedulers.background" ^
  --hidden-import "apscheduler.executors.pool" ^
  --hidden-import "sqlalchemy.dialects.sqlite" ^
  --hidden-import "setup_app" ^
  main_app.py

if errorlevel 1 (
  echo.
  echo ERROR: PyInstaller build failed.
  pause
  exit /b 1
)

echo.
echo PyInstaller build complete: dist\DraftAI\DraftAI.exe
echo.

REM --- Compile Inno Setup installer if available ---
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if exist "%ISCC%" (
  echo Compiling installer with Inno Setup...
  mkdir installer_output 2>nul
  "%ISCC%" draft_ai_installer.iss
  if errorlevel 1 (
    echo WARNING: Inno Setup compilation failed.
  ) else (
    echo.
    echo ============================================
    echo  INSTALLER READY:
    echo  installer_output\DraftAI_Setup.exe
    echo ============================================
  )
) else (
  echo.
  echo Inno Setup not found.
  echo Download from: https://jrsoftware.org/isinfo.php
  echo Then re-run this script to produce DraftAI_Setup.exe
  echo.
  echo Portable build is at: dist\DraftAI\DraftAI.exe
)

echo.
pause

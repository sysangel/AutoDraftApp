@echo off
echo ============================================
echo  draft.ai - Build Installer
echo ============================================
echo.

cd /d %~dp0

call venv\Scripts\activate

pip install pyinstaller -q

echo Building draft.ai executable...
pyinstaller --onedir ^
  --name "DraftAI" ^
  --icon NONE ^
  --noconsole ^
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

REM --- Optional: compile Inno Setup installer ---
REM Looks for ISCC.exe in the default Inno Setup install location.
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"

if exist %ISCC% (
  echo Building installer with Inno Setup...
  mkdir installer_output 2>nul
  %ISCC% draft_ai_installer.iss
  if errorlevel 1 (
    echo WARNING: Inno Setup compilation failed.
  ) else (
    echo Installer created: installer_output\DraftAI_Setup.exe
  )
) else (
  echo Inno Setup not found - skipping installer packaging.
  echo To create a single-file installer, install Inno Setup 6 from:
  echo   https://jrsoftware.org/isinfo.php
  echo Then re-run this script.
)

echo.
echo ============================================
echo  Done!
echo  - dist\DraftAI\DraftAI.exe    portable app
echo  - installer_output\DraftAI_Setup.exe  (if Inno Setup was found)
echo ============================================
pause

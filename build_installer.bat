@echo off
echo ============================================
echo  AutoDraft App - Building Installer
echo ============================================
echo.

REM Make sure we're in the right directory
cd /d %~dp0

REM Activate venv
call venv\Scripts\activate

REM Install PyInstaller if needed
pip install pyinstaller -q

echo Building setup wizard exe...
pyinstaller --onefile ^
  --name "AutoDraftSetup" ^
  --icon NONE ^
  --console ^
  setup_wizard.py

echo.
echo Building main app exe...
pyinstaller --onedir ^
  --name "AutoDraftApp" ^
  --icon NONE ^
  --console ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --add-data "requirements.txt;." ^
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
  app.py

echo.
echo ============================================
echo  Done! Check the dist\ folder.
echo  - dist\AutoDraftSetup.exe  = run first
echo  - dist\AutoDraftApp\       = main app
echo ============================================
pause

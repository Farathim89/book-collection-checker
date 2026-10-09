@echo off
rem Builds dist\BookCollectionChecker.exe (one file, no console). Needs the .venv with requirements + pyinstaller.
cd /d "%~dp0"
.venv\Scripts\python.exe -m pip install -q pyinstaller || goto :error
.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --name BookCollectionChecker ^
    --icon assets\icon.ico --add-data "assets\icon.png;assets" ^
    --exclude-module tkinter --exclude-module pytest ^
    checker.pyw || goto :error
if not exist dist\CollectionChecker-data mkdir dist\CollectionChecker-data
echo.
echo Built: "%~dp0dist\BookCollectionChecker.exe"
exit /b 0
:error
echo Build failed.
exit /b 1

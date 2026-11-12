@echo off
rem Kompiluje DevKit.exe i kopiuje go do roota repo (folder wyzej).
cd /d "%~dp0"
if not exist ui\index.html (echo Brak ui\index.html & pause & exit /b 1)
py -m pip install --quiet pyinstaller
py -m PyInstaller --onefile --noconsole --name DevKit --icon icon.ico --add-data "ui;ui" devkit.py
copy /Y dist\DevKit.exe ..\DevKit.exe
rmdir /S /Q build dist
del DevKit.spec
echo Gotowe: DevKit.exe w roocie repo.
pause

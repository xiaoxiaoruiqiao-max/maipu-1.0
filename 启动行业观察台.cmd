@echo off
setlocal
pushd "%~dp0"
python.exe -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul
if not errorlevel 1 goto use_python
py.exe -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul
if not errorlevel 1 goto use_py
echo Please install Python 3.9 or later, or open dist\index.html to view the saved snapshot.
pause
exit /b 1
:use_python
python.exe app.py open
goto end
:use_py
py.exe -3 app.py open
:end
if errorlevel 1 pause
popd

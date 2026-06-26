@echo off
pythonw "%~dp0main.py" %*
if errorlevel 1 (
    python "%~dp0main.py" %*
)

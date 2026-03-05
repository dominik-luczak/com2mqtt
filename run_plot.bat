@echo off
setlocal

set SCRIPT_DIR=%~dp0
python "%SCRIPT_DIR%serial_json_plot.py" %*

endlocal

@echo off
echo Installing dependencies...
python -m pip install PySide6 pyqtgraph akshare pandas numpy requests beautifulsoup4 lxml > D:\finance\install_log.txt 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Install failed, check install_log.txt
) else (
    echo Done > D:\finance\install_done.txt
    echo Install OK
)

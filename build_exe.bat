@echo off
cd /d D:\finance
echo %date% %time% Start > build_log.txt
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
"D:\Program Files\Tencent\Marvis\MarvisAgent\1.0.1100.151\runtime\python311\python.exe" -m PyInstaller --onedir --console --icon=icon.ico --name StockLens --collect-all numpy --collect-all PySide6 --collect-all pyqtgraph --collect-all akshare --distpath D:\finance\dist --workpath D:\finance\build --specpath D:\finance main.py >> build_log.txt 2>&1
echo %date% %time% Done, code=%ERRORLEVEL% >> build_log.txt
echo OK > build_done.txt
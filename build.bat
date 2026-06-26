@echo off
REM 打包 WinPreview 為單一 exe
REM --collect-all tkinterdnd2 是關鍵：確保拖放用的 tkdnd 原生檔被打包進去
REM PyMuPDF 自帶 PyInstaller hook，不需 --collect-all（會把整包塞進去）
REM --exclude-module 排除共用環境中不相干的大型套件，避免 exe 爆肥
echo 開始打包 WinPreview...
pyinstaller --noconfirm --windowed --onefile --name WinPreview ^
    --collect-all tkinterdnd2 ^
    --exclude-module pygame ^
    --exclude-module numpy ^
    --exclude-module scipy ^
    --exclude-module pandas ^
    --exclude-module matplotlib ^
    --exclude-module cv2 ^
    --exclude-module torch ^
    --exclude-module tensorflow ^
    --exclude-module PyQt5 ^
    --exclude-module PyQt6 ^
    --exclude-module PySide2 ^
    --exclude-module PySide6 ^
    --exclude-module IPython ^
    --exclude-module notebook ^
    --exclude-module sympy ^
    --exclude-module sphinx ^
    main.py
echo.
echo 完成！執行檔在 dist\WinPreview.exe
pause

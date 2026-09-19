@echo off
chcp 65001 >nul
echo ========================================
echo   FIT Viewer - Build Windows EXE
echo ========================================

:: 安装依赖
pip install pyinstaller fitparse

:: 打包（单文件模式，包含 tkinter）
pyinstaller --onefile --windowed --name "FIT-Viewer" ^
    --icon=NONE ^
    --hidden-import tkinter ^
    --clean ^
    fit_viewer.py

echo.
echo ========================================
if exist "dist\FIT-Viewer.exe" (
    echo BUILD SUCCESS!
    echo Output: dist\FIT-Viewer.exe
) else (
    echo BUILD FAILED - check output above
)
echo ========================================
pause

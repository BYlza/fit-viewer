@echo off
chcp 65001 >nul
echo ========================================
echo   FIT Viewer - Build Windows EXE
echo ========================================

:: 安装依赖
pip install pyinstaller fitparse

:: 打包（单文件模式，包含 tkinter）
pyinstaller --onefile --windowed --name "FIT查看器" ^
    --icon=NONE ^
    --add-data "Zepp20260916211459.fit;." ^
    --hidden-import tkinter ^
    --clean ^
    fit_viewer.py

echo.
echo ========================================
if exist "dist\FIT查看器.exe" (
    echo BUILD SUCCESS!
    echo Output: dist\FIT查看器.exe
) else (
    echo BUILD FAILED - check output above
)
echo ========================================
pause

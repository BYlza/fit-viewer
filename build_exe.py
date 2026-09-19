# -*- coding: utf-8 -*-
"""一键打包为 Windows EXE"""
import subprocess, sys, os

def run(cmd):
    print(f">>> {cmd}")
    r = subprocess.run(cmd, shell=True)
    if r.returncode != 0:
        print(f"ERROR: command failed with code {r.returncode}")
        sys.exit(1)

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print("Installing dependencies...")
    run(f"{sys.executable} -m pip install pyinstaller fitparse")

    print("\nBuilding EXE...")
    run(
        f'{sys.executable} -m PyInstaller '
        f'--onefile --windowed '
        f'--name "FIT查看器" '
        f'--hidden-import tkinter '
        f'--clean '
        f'fit_viewer.py'
    )

    exe = os.path.join("dist", "FIT查看器.exe")
    if os.path.isfile(exe):
        print(f"\nBUILD SUCCESS: {exe}")
        print("Double-click to run.")
    else:
        print("\nBUILD FAILED")

if __name__ == "__main__":
    main()

# FIT 运动轨迹查看器 - 打包指南

## 打包为 Windows EXE

### 方法 1：命令行（推荐）
```bash
cd D:\运动数据
python build_exe.py
```

### 方法 2：双击批处理
```
双击 build_exe.bat
```

### 方法 3：手动 PyInstaller
```bash
pip install pyinstaller fitparse
pyinstaller --onefile --windowed --name "FIT查看器" --hidden-import tkinter --clean fit_viewer.py
```

生成文件：`dist\FIT查看器.exe`

## 直接使用（无需打包）

```bash
cd D:\运动数据
python fit_viewer.py                  # 弹出文件夹选择框
python fit_viewer.py D:\运动数据\数据   # 直接指定文件夹
```

完整使用说明见 [README.md](README.md)。

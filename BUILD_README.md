# FIT 运动轨迹查看器 - 打包指南

## 文件说明
| 文件 | 说明 |
|------|------|
| `fit_viewer.py` | 主程序（Python 源码） |
| `build_exe.py` | Windows EXE 打包脚本 |
| `build_exe.bat` | Windows EXE 打包批处理 |
| `buildozer.spec` | Android APK 打包配置 |

---

## 一、打包为 Windows EXE

### 方法 1：双击批处理
```
双击 build_exe.bat
```

### 方法 2：命令行
```bash
cd D:\运动数据
python build_exe.py
```

### 方法 3：手动 PyInstaller
```bash
pip install pyinstaller fitparse
pyinstaller --onefile --windowed --name "FIT查看器" --hidden-import tkinter --clean fit_viewer.py
```

生成文件：`dist\FIT查看器.exe`（约 15-25MB）

---

## 二、打包为 Android APK

### 前置条件
1. **WSL2** 或 **Linux** 系统（Buildozer 不支持原生 Windows）
2. Python 3.8+
3. Buildozer

### 安装 Buildozer（在 WSL/Linux 中）
```bash
sudo apt update
sudo apt install -y buildozer git python3-dev
pip install buildozer
```

### 构建 APK
```bash
# 将项目复制到 WSL 中
cp -r /mnt/d/运动数据 ~/fit-viewer
cd ~/fit-viewer

# 构建 debug APK
buildozer android debug
```

生成文件：`bin/fitviewer-1.0-debug.apk`

### 注意事项
- 首次构建会下载 Android SDK/NDK（约 2-3GB）
- 构建时间约 10-20 分钟
- 需要 Android 手机开启"USB 调试"安装 APK

---

## 三、直接使用（无需打包）

```bash
cd D:\运动数据
python fit_viewer.py
```

会弹出文件选择对话框，选择 .fit 文件后自动在浏览器中打开轨迹地图。

命令行模式：
```bash
python fit_viewer.py Zepp20260916211459.fit
```

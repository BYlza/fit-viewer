# FIT 运动轨迹查看器

> 当前版本：**v1.0.2**

一个解析运动手表 `.fit` 文件（Garmin / Amazfit / Zepp 等）并在网页地图上可视化轨迹的桌面工具。支持自动识别绕圈（圈数）与公里分段，逐点查看配速、心率、海拔、累计距离等。

## 功能

- **选择文件夹批量加载**：打开后选择包含 `.fit` 文件的文件夹，自动加载全部文件，右上角下拉框切换。
- **实景地图**：街道 / 卫星 / 地形 / 混合四种底图一键切换，支持放大到 22 级。
- **自动圈数检测**：纯 GPS 识别「绕圈」路线，标记每圈闭合 / 未闭合；非绕圈路线自动显示公里分段。
- **逐点查看**：点击或悬停轨迹点，右侧显示该点所属圈数、时间、心率、速度、配速、海拔、累计距离、踏频。
- **曲线图联动**：底部配速 / 心率 / 海拔曲线图，悬停时地图自动高亮对应点并显示信息；提示框配速精确到秒。
- **底部统计栏**：距离、用时、平均/最佳配速、心率区间、步频、步幅、海拔、卡路里等。

## 使用

### 直接运行（需 Python 3）

```bash
pip install fitparse
python fit_viewer.py                  # 弹出文件夹选择框
python fit_viewer.py D:\运动数据\数据   # 直接指定文件夹
python fit_viewer.py xx.fit           # 直接指定单个文件
```

运行后自动在浏览器或内置窗口打开轨迹地图，并生成 `fit_route.html`（可单独用浏览器打开）。

### Windows EXE（无需安装 Python，推荐）

直接到 [Releases](https://github.com/BYlza/fit-viewer/releases) 页面下载最新的 `FIT-Viewer.exe`，双击运行，选择包含 `.fit` 文件的文件夹即可，无需安装任何依赖。

## 打包为 EXE

```bash
pip install pyinstaller fitparse
python build_exe.py        # 或双击 build_exe.bat
```

生成：`dist\FIT查看器.exe`

## 项目结构

| 文件 | 说明 |
|------|------|
| `fit_viewer.py` | 主程序（解析 FIT + 生成 HTML + 内置地图/图表） |
| `analyze_fit.py` | 命令行 FIT 文件分析工具 |
| `build_exe.py` / `build_exe.bat` | Windows EXE 打包脚本 |
| `FIT查看器.spec` | PyInstaller 配置 |
| `数据/` | FIT 数据文件目录（可自行放置） |

## 说明

- 轨迹坐标使用 WGS-84（GPS），底图为 Web Mercator。
- 卫星图默认来自 Esri，国内访问偶尔不稳定，可自行替换图源。
- `fit_route.html` 为运行生成的中间产物，已加入 `.gitignore`，不含到版本库。

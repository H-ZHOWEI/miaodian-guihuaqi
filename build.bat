@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ===== 开始打包 秒点规划器 =====
"E:\Program Files (x86)\python\python.exe" -m PyInstaller --noconfirm --onefile --windowed ^
  --name "秒点规划器" ^
  "秒点规划器.py"
echo.
echo ===== 打包完成，exe 在 dist 目录 =====
pause

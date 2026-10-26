@echo off
REM Builds dist\POD_Photo_Renamer.exe from pod_qr_app.py
cd /d "%~dp0"
python -m pip install pyinstaller -r requirements.txt
python -m PyInstaller --noconfirm --onefile --windowed --name POD_Photo_Renamer --collect-all zxingcpp pod_qr_app.py
pause

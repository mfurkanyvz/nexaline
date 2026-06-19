@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ASİSTAN Mobil Web: http://127.0.0.1:8765
start "" http://127.0.0.1:8765
py -3.12 -m http.server 8765 --bind 127.0.0.1

@echo off
cd /d %~dp0\..
python web_wallet_server.py --host 127.0.0.1 --port 8000
pause

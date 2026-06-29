@echo off
cd /d %~dp0\..
python miner.py --node http://192.168.31.210:8111 --reward-address RLC_PASTE_YOUR_ADDRESS_HERE --once
pause

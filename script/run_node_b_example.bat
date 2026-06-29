@echo off
cd /d %~dp0\..
python node_server.py --node B --host 0.0.0.0 --port 8111 --db node_b_v2.db --difficulty 2 --external-url http://192.168.31.211:8111 --peers http://192.168.31.210:8111 --sync-interval 5
pause

@echo off
echo Replace RLC_REWARD_ADDRESS_HERE with your wallet address.
python miner.py --node http://127.0.0.1:8111 --reward-address RLC_REWARD_ADDRESS_HERE --once
pause

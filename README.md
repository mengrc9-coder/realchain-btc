# RealChain-BTC V1

这是 RealChain-BTC 的第一个正式版本，目标是尽量向真实比特币架构靠拢。

## V1 的角色拆分

```text
node_server.py        全节点：账本、UTXO、mempool、交易验证、区块验证
web_wallet_server.py  网页钱包服务器：只提供页面文件
miner.py              独立矿工：从节点拿 block template，本地 PoW，提交新区块
```

核心原则：

- 节点不保存任何用户私钥。
- 网页服务器不保存任何用户私钥。
- 用户私钥只保存在浏览器本地。
- 刷新页面后钱包回到锁定状态，需要密码解锁才能签名。
- 钱包页面不挖矿。
- 矿工奖励地址由 `miner.py` 指定。
- 节点只接收 signed transaction。
- 节点验证签名、UTXO、双花、手续费。
- 矿工独立执行 PoW。

## 安装依赖

```bash
pip install -r requirements.txt
```

## 1. 启动全节点

```bash
python node_server.py --node A --host 127.0.0.1 --port 8111 --db node_v1.db --difficulty 2
```

打开节点接口：

```text
http://127.0.0.1:8111
```

如果返回 JSON，说明节点运行成功。

## 2. 启动网页钱包

```bash
python web_wallet_server.py --host 127.0.0.1 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

页面里的 Node API 填：

```text
http://127.0.0.1:8111
```

## 3. 创建钱包

在网页钱包中输入密码，点击“创建新钱包”。

浏览器本地会生成：

- 私钥
- 公钥
- 地址
- 加密钱包 JSON

密码只用于加密本地私钥，不会发送给节点。

建议立刻点击“导出加密钱包 JSON”，保存备份。

## 4. 运行独立矿工

复制钱包页面里的地址，例如：

```text
RLC_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

然后在新终端运行：

```bash
python miner.py --node http://127.0.0.1:8111 --reward-address RLC_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx --once
```

如果想让矿工一直挖：

```bash
python miner.py --node http://127.0.0.1:8111 --reward-address RLC_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

停止矿工按：

```text
Ctrl + C
```

## 5. 转账实验

建议用两个浏览器模拟两个用户：

```text
Chrome：张三钱包
Edge：李四钱包
```

流程：

```text
1. 张三创建钱包，复制张三地址
2. 运行 miner.py，把奖励地址设为张三地址
3. 张三刷新余额，看到 50 RLC
4. 李四创建钱包，复制李四地址
5. 张三输入李四地址、金额、手续费
6. 张三浏览器本地签名并提交交易
7. 交易进入 mempool
8. 运行 miner.py 挖新区块
9. 李四刷新余额，看到收到的 UTXO
```

## V1 和真实比特币的差异

V1 仍然是实验室版本，不是 Bitcoin Core。

主要差异：

- 地址是 RLC_ 实验地址，不是真实 BTC 地址。
- 浏览器 WebCrypto 使用 P-256，真实比特币使用 secp256k1。
- 只有单节点，V2 才做多节点 P2P。
- 没有 Bitcoin Script、SegWit、Taproot。
- 没有累计工作量主链、分叉重组。
- PoW 难度很低，只适合实验。
- 不要用于真实资金。

## 下一版 V2

V2 目标：

- 多台电脑运行多个 full node。
- 节点之间添加 peers。
- 交易自动广播。
- 区块自动广播。
- 节点掉线后重新同步。

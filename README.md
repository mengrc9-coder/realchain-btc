# RealChain-BTC V2 Final

V2 Final 是 V1 的累计版本，不是平行版本。

## V2 = V1 Stable + LAN P2P

V2 保留 V1 的核心功能：

- 创建钱包
- 导入钱包
- 私钥本地保管
- 节点不保存私钥
- 钱包本地签名交易
- 节点验证签名
- UTXO 余额模型
- 交易进入 mempool
- 独立 `miner.py` 挖矿
- coinbase 奖励
- 区块确认交易
- 查询余额、区块、mempool、链高度

V2 新增：

- 两台电脑局域网双节点：Node A / Node B
- 节点启动时通过 `--peers` 自动连接
- 交易自动广播
- 区块自动广播
- 节点定时自动同步更长合法链
- `wallet.html` 钱包页面和 `network.html` 网络监控页面分离
- 创建钱包后下载 keystore JSON，刷新页面后需要重新导入

---

## 1. 安装依赖

两台电脑都进入项目目录：

```powershell
python -m pip install -r requirements.txt
```

验证：

```powershell
python -c "import flask; import requests; import cryptography; print('ok')"
```

---

## 2. 查询两台电脑的局域网 IP

两台电脑都运行：

```powershell
ipconfig
```

找到 IPv4 地址。

示例：

```text
电脑 A：192.168.31.210
电脑 B：192.168.31.211
```

下面命令里的 IP 要换成你自己的。

---

## 3. 电脑 A 启动 Node A

```powershell
python node_server.py --node A --host 0.0.0.0 --port 8111 --db node_a_v2.db --difficulty 2 --external-url http://192.168.31.210:8111 --peers http://192.168.31.211:8111 --sync-interval 5
```

浏览器打开：

```text
http://192.168.31.210:8111
```

看到 JSON 说明 Node A 正常。

---

## 4. 电脑 B 启动 Node B

```powershell
python node_server.py --node B --host 0.0.0.0 --port 8111 --db node_b_v2.db --difficulty 2 --external-url http://192.168.31.211:8111 --peers http://192.168.31.210:8111 --sync-interval 5
```

浏览器打开：

```text
http://192.168.31.211:8111
```

看到 JSON 说明 Node B 正常。

---

## 5. 两台电脑都启动本地钱包页面

两台电脑都运行：

```powershell
python web_wallet_server.py --host 127.0.0.1 --port 8000
```

两台电脑都打开：

```text
http://127.0.0.1:8000
```

进入：

```text
wallet.html     钱包页面
network.html    网络监控页面
```

注意：钱包页面建议用 `127.0.0.1` 打开，这样浏览器 WebCrypto 能正常创建钱包。

---

## 6. network.html 怎么用

打开：

```text
http://127.0.0.1:8000/network.html
```

填写：

```text
Node A API: http://192.168.31.210:8111
Node B API: http://192.168.31.211:8111
```

点刷新状态。

正常情况下应该看到：

- Node A peers 里有 Node B
- Node B peers 里有 Node A
- 两个节点高度一致
- 两个节点 tip_hash 一致

---

## 7. wallet.html 怎么用

打开：

```text
http://127.0.0.1:8000/wallet.html
```

Node API 可以填任意一个节点：

```text
http://192.168.31.210:8111
```

或：

```text
http://192.168.31.211:8111
```

创建钱包后会下载：

```text
realchain_keystore_RLC_xxx.json
```

这个文件要自己保存，不要上传 GitHub。

刷新页面后钱包不会自动恢复，需要重新导入 keystore 并输入密码解锁。

---

## 8. 挖矿

复制钱包地址后，可以让矿工连接任意节点挖矿。

连接 Node A 挖一次：

```powershell
python miner.py --node http://192.168.31.210:8111 --reward-address 你的RLC地址 --once
```

连接 Node B 挖一次：

```powershell
python miner.py --node http://192.168.31.211:8111 --reward-address 你的RLC地址 --once
```

挖完后去 `network.html` 看两个节点高度是否自动一致。

---

## 9. 验收测试

### 测试 1：节点互联

Node A 和 Node B 启动后，`network.html` 应显示双方互为 peer。

### 测试 2：区块同步

矿工连接 Node A 挖一个区块，Node B 应在几秒后自动同步到相同高度和 tip_hash。

### 测试 3：反向区块同步

矿工连接 Node B 挖一个区块，Node A 应在几秒后自动同步到相同高度和 tip_hash。

### 测试 4：跨电脑钱包互通

电脑 A 创建张三钱包，电脑 B 创建李四钱包。张三给李四地址转账，交易提交给 Node A 后，应广播到 Node B。矿工打包后，李四在电脑 B 刷新余额能看到收款。

### 测试 5：私钥不共享

电脑 A 创建的钱包不会自动出现在电脑 B。跨设备使用同一个钱包，需要导入 keystore 文件并输入密码。这是真实钱包逻辑。

---

## 10. 常见问题

### 两台电脑互相打不开节点地址

大概率是 Windows 防火墙。允许 Python 通过专用网络。

### 创建钱包失败

请用：

```text
http://127.0.0.1:8000/wallet.html
```

不要用局域网 IP 打开钱包页面。浏览器通常只允许 `localhost/127.0.0.1` 或 HTTPS 使用 WebCrypto。

### V1 和 V2 会不会混乱

不会。V2 使用独立文件夹和独立数据库：

```text
node_a_v2.db
node_b_v2.db
```

运行 V2 前建议关闭 V1 的 8111 和 8000 端口程序。

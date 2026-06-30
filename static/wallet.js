const enc = new TextEncoder();
const dec = new TextDecoder();

let currentKeystore = null;
let currentPrivateKey = null;
let currentAddress = null;

function $(id) { return document.getElementById(id); }
function log(id, obj) { $(id).textContent = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2); }
function getNodeApi() { return ($('nodeApi').value || localStorage.getItem('realchain_node_api') || '').trim().replace(/\/$/, ''); }
function setStatus() {
  $('walletAddress').textContent = currentKeystore?.address || currentAddress || '未导入';
  $('walletLockState').textContent = currentPrivateKey ? '已解锁，仅本页面内存持有私钥' : (currentKeystore ? '已导入 keystore，未解锁' : '未导入');
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') {
    const out = {};
    Object.keys(value).sort().forEach(k => {
      if (value[k] !== undefined) out[k] = canonicalize(value[k]);
    });
    return out;
  }
  return value;
}
function canonicalStringify(value) { return JSON.stringify(canonicalize(value)); }

function abToB64(buf) {
  const bytes = new Uint8Array(buf);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}
function b64ToAb(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
function abToHex(buf) {
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}
async function sha256Hex(data) {
  const buf = typeof data === 'string' ? enc.encode(data) : data;
  return abToHex(await crypto.subtle.digest('SHA-256', buf));
}
async function deriveAddress(publicKeySpki) {
  const h = await sha256Hex(publicKeySpki);
  return 'RLC_' + h.slice(0, 40);
}
async function passwordKey(password, salt) {
  const material = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: 150000, hash: 'SHA-256' },
    material,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt']
  );
}
function downloadJson(filename, obj) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
async function createWallet() {
  const p1 = $('newPassword').value;
  const p2 = $('newPassword2').value;
  if (!p1 || p1.length < 6) throw new Error('密码至少 6 位');
  if (p1 !== p2) throw new Error('两次密码不一致');
  const keys = await crypto.subtle.generateKey(
    { name: 'ECDSA', namedCurve: 'P-256' },
    true,
    ['sign', 'verify']
  );
  const pkcs8 = await crypto.subtle.exportKey('pkcs8', keys.privateKey);
  const spki = await crypto.subtle.exportKey('spki', keys.publicKey);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await passwordKey(p1, salt);
  const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, pkcs8);
  const address = await deriveAddress(spki);
  currentKeystore = {
    version: 1,
    project: 'RealChain-BTC',
    curve: 'P-256',
    kdf: 'PBKDF2-SHA256',
    kdf_iterations: 150000,
    cipher: 'AES-256-GCM',
    address,
    public_key: abToB64(spki),
    crypto: { salt: abToB64(salt), iv: abToB64(iv), ciphertext: abToB64(ciphertext) },
    warning: 'Keep this keystore file by yourself. Nodes and servers do not store your private key.'
  };
  currentPrivateKey = keys.privateKey;
  currentAddress = address;
  setStatus();
  downloadJson(`realchain_keystore_${address}.json`, currentKeystore);
  log('walletLog', `钱包创建成功，keystore 已下载。地址：${address}`);
}
async function unlockKeystore() {
  if (!currentKeystore) throw new Error('请先导入 keystore');
  const password = $('unlockPassword').value;
  if (!password) throw new Error('请输入密码');
  const salt = new Uint8Array(b64ToAb(currentKeystore.crypto.salt));
  const iv = new Uint8Array(b64ToAb(currentKeystore.crypto.iv));
  const ciphertext = b64ToAb(currentKeystore.crypto.ciphertext);
  const key = await passwordKey(password, salt);
  const pkcs8 = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, ciphertext);
  currentPrivateKey = await crypto.subtle.importKey('pkcs8', pkcs8, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
  currentAddress = currentKeystore.address;
  setStatus();
  log('walletLog', `解锁成功。私钥只在当前页面内存中，刷新或锁定后会消失。`);
}
function importKeystoreObject(obj) {
  if (!obj.address || !obj.public_key || !obj.crypto) throw new Error('不是有效 keystore');
  currentKeystore = obj;
  currentPrivateKey = null;
  currentAddress = obj.address;
  setStatus();
  log('walletLog', `keystore 已导入：${obj.address}`);
}
function signPayload(tx) {
  return {
    version: tx.version,
    type: 'transfer',
    inputs: tx.inputs,
    outputs: tx.outputs,
    fee: tx.fee,
    timestamp: tx.timestamp,
    pubkey: tx.pubkey
  };
}
function txidPayload(tx) {
  const x = { ...tx };
  delete x.txid;
  return x;
}
async function refreshBalance() {
  if (!currentKeystore?.address) throw new Error('请先创建或导入钱包');
  const node = getNodeApi();
  if (!node) throw new Error('请先填写 Node API');
  const res = await fetch(`${node}/balance/${currentKeystore.address}`);
  const data = await res.json();
  $('balanceText').textContent = data.balance;
  log('walletLog', data);
}
async function sendTx() {
  if (!currentKeystore || !currentPrivateKey) throw new Error('请先导入并解锁钱包');
  const node = getNodeApi();
  if (!node) throw new Error('请先填写 Node API');
  const to = $('toAddress').value.trim();
  const amount = parseInt($('amount').value, 10);
  const fee = parseInt($('fee').value || '0', 10);
  if (!to.startsWith('RLC_')) throw new Error('收款地址格式不对');
  if (!Number.isInteger(amount) || amount <= 0) throw new Error('金额必须是正整数');
  if (!Number.isInteger(fee) || fee < 0) throw new Error('手续费不能为负数');
  const utxoRes = await fetch(`${node}/utxos/${currentKeystore.address}`);
  const utxoData = await utxoRes.json();
  const need = amount + fee;
  let selected = [], total = 0;
  for (const u of utxoData.utxos || []) {
    selected.push(u); total += parseInt(u.amount, 10);
    if (total >= need) break;
  }
  if (total < need) throw new Error(`余额不足，需要 ${need}，当前可用 ${total}`);
  const outputs = [{ address: to, amount }];
  const change = total - need;
  if (change > 0) outputs.push({ address: currentKeystore.address, amount: change });
  const tx = {
    version: 1,
    type: 'transfer',
    inputs: selected.map(u => ({ txid: u.txid, index: u.index, address: u.address, amount: u.amount })),
    outputs,
    fee,
    timestamp: Math.floor(Date.now() / 1000),
    pubkey: currentKeystore.public_key
  };
  const msg = canonicalStringify(signPayload(tx));
  const sig = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, currentPrivateKey, enc.encode(msg));
  tx.signatures = selected.map(() => abToB64(sig));
  tx.txid = await sha256Hex(canonicalStringify(txidPayload(tx)));
  const res = await fetch(`${node}/transactions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tx })
  });
  const data = await res.json();
  log('txLog', data);
  if (!data.ok) throw new Error(data.error || '交易提交失败');
  await refreshBalance();
}
async function testNode() {
  const node = getNodeApi();
  if (!node) throw new Error('请填写 Node API');
  const res = await fetch(`${node}/status`);
  const data = await res.json();
  log('nodeStatus', data);
}

window.addEventListener('DOMContentLoaded', () => {
  $('nodeApi').value = localStorage.getItem('realchain_node_api') || '';
  setStatus();
  $('saveNodeBtn').onclick = () => { localStorage.setItem('realchain_node_api', getNodeApi()); log('nodeStatus', 'Node API 已保存'); };
  $('testNodeBtn').onclick = () => testNode().catch(e => log('nodeStatus', '连接失败：' + e.message));
  $('createWalletBtn').onclick = () => createWallet().catch(e => log('walletLog', '创建失败：' + e.message));
  $('keystoreFile').onchange = async (ev) => {
    try {
      const file = ev.target.files[0]; if (!file) return;
      importKeystoreObject(JSON.parse(await file.text()));
    } catch (e) { log('walletLog', '导入失败：' + e.message); }
  };
  $('importTextBtn').onclick = () => {
    try { importKeystoreObject(JSON.parse($('keystoreText').value)); } catch(e) { log('walletLog', '导入失败：' + e.message); }
  };
  $('unlockBtn').onclick = () => unlockKeystore().catch(e => log('walletLog', '解锁失败：' + e.message));
  $('lockBtn').onclick = () => { currentPrivateKey = null; setStatus(); log('walletLog', '已锁定。'); };
  $('downloadKeystoreBtn').onclick = () => {
    if (!currentKeystore) return log('walletLog', '没有可下载的 keystore');
    downloadJson(`realchain_keystore_${currentKeystore.address}.json`, currentKeystore);
  };
  $('clearWalletBtn').onclick = () => { currentKeystore = null; currentPrivateKey = null; currentAddress = null; setStatus(); log('walletLog', '当前页面钱包已清除。'); };
  $('refreshBalanceBtn').onclick = () => refreshBalance().catch(e => log('walletLog', '刷新失败：' + e.message));
  $('copyAddressBtn').onclick = () => {
    if (currentKeystore?.address) navigator.clipboard.writeText(currentKeystore.address);
  };
  $('sendTxBtn').onclick = () => sendTx().catch(e => log('txLog', '发送失败：' + e.message));
});

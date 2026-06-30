function $(id) { return document.getElementById(id); }
function clean(url) { return (url || '').trim().replace(/\/$/, ''); }
function print(id, obj) { $(id).textContent = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2); }
async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}
async function postJson(url) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}
async function readNode(base) {
  base = clean(base);
  const [status, mempool, latest] = await Promise.all([
    getJson(`${base}/status`),
    getJson(`${base}/mempool`),
    getJson(`${base}/blocks/latest`)
  ]);
  return { status, mempool, latest: latest.block };
}
function summarize(a, b) {
  if (!a || !b) return '等待两个节点状态';
  const sameHeight = a.status.height === b.status.height;
  const sameTip = a.status.tip_hash === b.status.tip_hash;
  const aHasB = (a.status.peers || []).includes(clean($('nodeB').value));
  const bHasA = (b.status.peers || []).includes(clean($('nodeA').value));
  const lines = [];
  lines.push(sameHeight && sameTip ? '✅ 两个节点高度和 tip_hash 一致' : '⚠️ 两个节点暂时不同步');
  lines.push(`Node A height=${a.status.height}, mempool=${a.status.mempool_size}`);
  lines.push(`Node B height=${b.status.height}, mempool=${b.status.mempool_size}`);
  lines.push(aHasB ? '✅ Node A peers 包含 Node B' : '⚠️ Node A peers 未显示 Node B');
  lines.push(bHasA ? '✅ Node B peers 包含 Node A' : '⚠️ Node B peers 未显示 Node A');
  return lines.join('\n');
}
async function refresh() {
  const aUrl = clean($('nodeA').value), bUrl = clean($('nodeB').value);
  localStorage.setItem('realchain_node_a', aUrl);
  localStorage.setItem('realchain_node_b', bUrl);
  let a = null, b = null;
  try { a = await readNode(aUrl); print('nodeAStatus', a); print('nodeAMempool', a.mempool); }
  catch (e) { print('nodeAStatus', 'Node A 连接失败：' + e.message); print('nodeAMempool', ''); }
  try { b = await readNode(bUrl); print('nodeBStatus', b); print('nodeBMempool', b.mempool); }
  catch (e) { print('nodeBStatus', 'Node B 连接失败：' + e.message); print('nodeBMempool', ''); }
  $('syncSummary').textContent = summarize(a, b);
}
async function syncNode(which) {
  const url = clean(which === 'A' ? $('nodeA').value : $('nodeB').value);
  const data = await postJson(`${url}/sync/now`);
  alert(`Node ${which} 同步结果：\n` + JSON.stringify(data, null, 2));
  await refresh();
}
window.addEventListener('DOMContentLoaded', () => {
  $('nodeA').value = localStorage.getItem('realchain_node_a') || '';
  $('nodeB').value = localStorage.getItem('realchain_node_b') || '';
  $('saveBtn').onclick = () => { localStorage.setItem('realchain_node_a', clean($('nodeA').value)); localStorage.setItem('realchain_node_b', clean($('nodeB').value)); alert('已保存'); };
  $('refreshBtn').onclick = refresh;
  $('syncABtn').onclick = () => syncNode('A').catch(e => alert(e.message));
  $('syncBBtn').onclick = () => syncNode('B').catch(e => alert(e.message));
  refresh().catch(() => {});
  setInterval(() => { if ($('autoRefresh').checked) refresh().catch(() => {}); }, 3000);
});

#!/usr/bin/env python3
import argparse, hashlib, json, time, urllib.parse, urllib.request
from typing import Any

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

def sha256_hex(data):
    if isinstance(data, str): data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()

def block_header(b):
    return {'height':int(b['height']),'previous_hash':b['previous_hash'],'merkle_root':b['merkle_root'],'timestamp':int(b['timestamp']),'difficulty':int(b['difficulty']),'nonce':int(b['nonce'])}

def block_hash(h): return sha256_hex(canonical_json(h))

def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))

def post_json(url, data):
    req = urllib.request.Request(url, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers={'Content-Type':'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))

def mine_once(node_url, reward_address, max_nonce=20000000):
    node_url = node_url.rstrip('/')
    q = urllib.parse.urlencode({'miner_address': reward_address})
    resp = get_json(f'{node_url}/api/mining/template?{q}')
    if not resp.get('ok'):
        raise RuntimeError(resp.get('error', 'failed to get block template'))
    block = resp['template']
    prefix = '0' * int(block['difficulty'])
    print(f"[miner] height={block['height']} txs={len(block['txs'])} fees={block['fees']} difficulty={block['difficulty']}")
    print(f"[miner] reward address={reward_address}")
    start = time.time()
    for nonce in range(max_nonce):
        block['nonce'] = nonce
        if nonce % 50000 == 0:
            block['timestamp'] = int(time.time())
        h = block_hash(block_header(block))
        if h.startswith(prefix):
            block['hash'] = h
            print(f"[miner] solved nonce={nonce} hash={h} elapsed={time.time()-start:.2f}s")
            return post_json(f'{node_url}/api/block/submit', block)
    raise RuntimeError('max_nonce reached')

def main():
    p = argparse.ArgumentParser(description='RealChain-BTC V1 independent miner')
    p.add_argument('--node', default='http://127.0.0.1:8111')
    p.add_argument('--reward-address', required=True)
    p.add_argument('--once', action='store_true')
    p.add_argument('--interval', type=int, default=3)
    a = p.parse_args()
    if a.once:
        print(mine_once(a.node, a.reward_address))
        return
    print('[miner] loop mode, press Ctrl+C to stop')
    while True:
        try:
            print('[miner] submitted:', mine_once(a.node, a.reward_address))
        except Exception as e:
            print('[miner] error:', e)
        time.sleep(a.interval)
if __name__ == '__main__': main()

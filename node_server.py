#!/usr/bin/env python3
import argparse, base64, hashlib, json, sqlite3, time
from pathlib import Path
from typing import Any
from flask import Flask, jsonify, request
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

GENESIS_PREVIOUS_HASH = '0' * 64
BLOCK_REWARD = 50

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

def sha256_hex(data):
    if isinstance(data, str): data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()

def now_ts(): return int(time.time())

def b64url_decode(s: str) -> bytes:
    s = s + '=' * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s.encode())

def public_jwk_canonical(j): return {'crv':j['crv'], 'kty':j['kty'], 'x':j['x'], 'y':j['y']}

def address_from_public_jwk(j):
    return 'RLC_' + sha256_hex(canonical_json(public_jwk_canonical(j)))[:40]

def public_key_from_jwk(j):
    if j.get('kty') != 'EC' or j.get('crv') != 'P-256':
        raise ValueError('Only EC P-256 public keys are supported in V1.')
    x = int.from_bytes(b64url_decode(j['x']), 'big')
    y = int.from_bytes(b64url_decode(j['y']), 'big')
    return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()

def webcrypto_signature_to_der(sig_b64):
    raw = base64.b64decode(sig_b64)
    if len(raw) == 64:
        r = int.from_bytes(raw[:32], 'big')
        s = int.from_bytes(raw[32:], 'big')
        return utils.encode_dss_signature(r, s)
    return raw

def tx_signing_payload(tx):
    return {
        'version': tx.get('version', 1),
        'timestamp': tx.get('timestamp'),
        'inputs': [{'txid': i['txid'], 'vout': int(i['vout'])} for i in tx.get('inputs', [])],
        'outputs': [{'address': o['address'], 'amount': int(o['amount'])} for o in tx.get('outputs', [])],
    }

def signing_message(tx): return canonical_json(tx_signing_payload(tx)).encode()

def txid(tx):
    clean = json.loads(canonical_json(tx))
    for k in ['txid','fee','received_at']:
        clean.pop(k, None)
    return sha256_hex(canonical_json(clean))

def merkle_root(txids):
    if not txids: return sha256_hex('')
    layer = txids[:]
    while len(layer) > 1:
        if len(layer) % 2: layer.append(layer[-1])
        layer = [sha256_hex(layer[i] + layer[i+1]) for i in range(0, len(layer), 2)]
    return layer[0]

def block_header(b):
    return {'height': int(b['height']), 'previous_hash': b['previous_hash'], 'merkle_root': b['merkle_root'], 'timestamp': int(b['timestamp']), 'difficulty': int(b['difficulty']), 'nonce': int(b['nonce'])}

def block_hash_from_header(h): return sha256_hex(canonical_json(h))

class NodeDB:
    def __init__(self, db_path, difficulty):
        self.db_path, self.difficulty = db_path, difficulty
        self.init_db()
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    def init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS blocks(height INTEGER PRIMARY KEY, hash TEXT UNIQUE NOT NULL, previous_hash TEXT NOT NULL, merkle_root TEXT NOT NULL, timestamp INTEGER NOT NULL, difficulty INTEGER NOT NULL, nonce INTEGER NOT NULL, miner TEXT, txs_json TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS utxos(txid TEXT NOT NULL, vout INTEGER NOT NULL, address TEXT NOT NULL, amount INTEGER NOT NULL, spent INTEGER NOT NULL DEFAULT 0, spent_by TEXT, created_height INTEGER NOT NULL, PRIMARY KEY(txid,vout))')
            c.execute('CREATE TABLE IF NOT EXISTS mempool(txid TEXT PRIMARY KEY, tx_json TEXT NOT NULL, fee INTEGER NOT NULL, received_at INTEGER NOT NULL)')
            if c.execute('SELECT COUNT(*) c FROM blocks').fetchone()['c'] == 0:
                g = {'height':0,'previous_hash':GENESIS_PREVIOUS_HASH,'timestamp':now_ts(),'difficulty':0,'nonce':0,'miner':'genesis','txs':[]}
                g['merkle_root'] = merkle_root([])
                g['hash'] = block_hash_from_header(block_header(g))
                c.execute('INSERT INTO blocks VALUES(?,?,?,?,?,?,?,?,?)', (g['height'],g['hash'],g['previous_hash'],g['merkle_root'],g['timestamp'],g['difficulty'],g['nonce'],g['miner'],json.dumps(g['txs'],ensure_ascii=False)))
            c.commit()
    def tip(self):
        with self.connect() as c:
            return dict(c.execute('SELECT * FROM blocks ORDER BY height DESC LIMIT 1').fetchone())
    def height(self): return int(self.tip()['height'])
    def get_utxo(self, t, vout):
        with self.connect() as c:
            r = c.execute('SELECT * FROM utxos WHERE txid=? AND vout=?',(t,int(vout))).fetchone()
            return dict(r) if r else None
    def address_utxos(self, addr):
        with self.connect() as c:
            rows = c.execute('SELECT txid,vout,address,amount,created_height FROM utxos WHERE address=? AND spent=0 ORDER BY created_height,txid,vout',(addr,)).fetchall()
            return [dict(r) for r in rows]
    def mempool_inputs(self):
        with self.connect() as c: rows = c.execute('SELECT tx_json FROM mempool').fetchall()
        used=set()
        for r in rows:
            tx=json.loads(r['tx_json'])
            for i in tx.get('inputs',[]): used.add((i['txid'], int(i['vout'])))
        return used
    def validate_tx(self, tx, check_mempool=True):
        if tx.get('type') == 'coinbase': raise ValueError('Coinbase cannot be submitted to mempool.')
        if not tx.get('inputs') or not tx.get('outputs'): raise ValueError('Transaction must have inputs and outputs.')
        seen, total_in, msg = set(), 0, signing_message(tx)
        mem_used = self.mempool_inputs() if check_mempool else set()
        for n,i in enumerate(tx['inputs']):
            key=(i['txid'], int(i['vout']))
            if key in seen: raise ValueError('Transaction spends same input twice.')
            seen.add(key)
            if check_mempool and key in mem_used: raise ValueError('Input already used by mempool transaction.')
            u=self.get_utxo(i['txid'], int(i['vout']))
            if not u: raise ValueError(f'Input {n} does not exist.')
            if int(u['spent']): raise ValueError(f'Input {n} is already spent.')
            pub, sig = i.get('public_key'), i.get('signature')
            if not pub or not sig: raise ValueError('Each input needs public_key and signature.')
            if address_from_public_jwk(pub) != u['address']: raise ValueError('Public key does not match UTXO address.')
            try:
                public_key_from_jwk(pub).verify(webcrypto_signature_to_der(sig), msg, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                raise ValueError('Invalid signature.')
            total_in += int(u['amount'])
        total_out=0
        for o in tx['outputs']:
            amt=int(o.get('amount',0)); addr=o.get('address','')
            if amt <= 0: raise ValueError('Output amount must be positive.')
            if not addr.startswith('RLC_'): raise ValueError('Output address must start with RLC_.')
            total_out += amt
        if total_out > total_in: raise ValueError('Output total exceeds input total.')
        return txid(tx), total_in-total_out
    def add_mempool(self, tx):
        tid, fee = self.validate_tx(tx, True); tx['txid']=tid
        with self.connect() as c:
            if c.execute('SELECT txid FROM mempool WHERE txid=?',(tid,)).fetchone(): raise ValueError('Transaction already in mempool.')
            c.execute('INSERT INTO mempool VALUES(?,?,?,?)',(tid,json.dumps(tx,ensure_ascii=False),fee,now_ts()))
            c.commit()
        return {'txid':tid,'fee':fee}
    def get_mempool(self):
        with self.connect() as c: rows=c.execute('SELECT * FROM mempool ORDER BY fee DESC, received_at ASC').fetchall()
        out=[]
        for r in rows:
            tx=json.loads(r['tx_json']); tx['txid']=r['txid']; tx['fee']=int(r['fee']); tx['received_at']=int(r['received_at']); out.append(tx)
        return out
    def block_template(self, miner_address):
        if not miner_address.startswith('RLC_'): raise ValueError('Reward address must start with RLC_.')
        txs=[]; fees=0; spent=set()
        for tx in self.get_mempool():
            try:
                tid, fee = self.validate_tx(tx, False)
                if any((i['txid'],int(i['vout'])) in spent for i in tx['inputs']): continue
                for i in tx['inputs']: spent.add((i['txid'], int(i['vout'])))
                tx['txid']=tid; tx['fee']=fee; txs.append(tx); fees += fee
            except Exception: pass
        coinbase={'type':'coinbase','timestamp':now_ts(),'inputs':[],'outputs':[{'address':miner_address,'amount':BLOCK_REWARD+fees}],'message':f'coinbase reward {BLOCK_REWARD} + fees {fees}'}
        coinbase['txid']=txid(coinbase)
        alltx=[coinbase]+txs; tip=self.tip()
        return {'height':int(tip['height'])+1,'previous_hash':tip['hash'],'merkle_root':merkle_root([t['txid'] for t in alltx]),'timestamp':now_ts(),'difficulty':self.difficulty,'nonce':0,'miner':miner_address,'txs':alltx,'fees':fees,'reward':BLOCK_REWARD,'coinbase_amount':BLOCK_REWARD+fees}
    def submit_block(self, b):
        tip=self.tip(); expected_height=int(tip['height'])+1
        if int(b.get('height',-1)) != expected_height: raise ValueError('Block height does not extend current tip.')
        if b.get('previous_hash') != tip['hash']: raise ValueError('previous_hash mismatch.')
        if int(b.get('difficulty',-1)) != self.difficulty: raise ValueError('difficulty mismatch.')
        txs=b.get('txs',[])
        if not txs or txs[0].get('type') != 'coinbase': raise ValueError('First transaction must be coinbase.')
        if any(t.get('type')=='coinbase' for t in txs[1:]): raise ValueError('Only first transaction can be coinbase.')
        for t in txs: t['txid']=txid(t)
        if b.get('merkle_root') != merkle_root([t['txid'] for t in txs]): raise ValueError('Invalid Merkle root.')
        h=block_hash_from_header(block_header(b))
        if not h.startswith('0'*self.difficulty): raise ValueError('Block hash does not satisfy difficulty.')
        used=set(); fees=0
        for t in txs[1:]:
            tid, fee=self.validate_tx(t, False)
            if tid != t['txid']: raise ValueError('Invalid transaction id.')
            for i in t['inputs']:
                key=(i['txid'],int(i['vout']))
                if key in used: raise ValueError('Block contains double spend.')
                used.add(key)
            fees += fee
        if len(txs[0].get('outputs',[])) != 1: raise ValueError('Coinbase must have one output.')
        if int(txs[0]['outputs'][0]['amount']) != BLOCK_REWARD + fees: raise ValueError('Invalid coinbase amount.')
        with self.connect() as c:
            c.execute('INSERT INTO blocks VALUES(?,?,?,?,?,?,?,?,?)',(expected_height,h,b['previous_hash'],b['merkle_root'],int(b['timestamp']),int(b['difficulty']),int(b['nonce']),b.get('miner',''),json.dumps(txs,ensure_ascii=False)))
            for t in txs:
                if t.get('type') != 'coinbase':
                    for i in t['inputs']: c.execute('UPDATE utxos SET spent=1, spent_by=? WHERE txid=? AND vout=?',(t['txid'],i['txid'],int(i['vout'])))
                for vout,o in enumerate(t['outputs']):
                    c.execute('INSERT INTO utxos VALUES(?,?,?,?,0,NULL,?)',(t['txid'],vout,o['address'],int(o['amount']),expected_height))
            for t in txs[1:]: c.execute('DELETE FROM mempool WHERE txid=?',(t['txid'],))
            c.commit()
        return {'height':expected_height,'hash':h,'tx_count':len(txs),'fees':fees,'reward':BLOCK_REWARD,'miner':b.get('miner','')}
    def list_blocks(self, limit=20):
        with self.connect() as c: rows=c.execute('SELECT * FROM blocks ORDER BY height DESC LIMIT ?',(int(limit),)).fetchall()
        th=self.height(); out=[]
        for r in rows:
            b=dict(r); b['txs']=json.loads(b.pop('txs_json')); b['confirmations']=th-int(b['height'])+1; out.append(b)
        return out

def create_app(db, node_name):
    app=Flask(__name__)
    @app.after_request
    def cors(resp):
        resp.headers['Access-Control-Allow-Origin']='*'; resp.headers['Access-Control-Allow-Headers']='Content-Type'; resp.headers['Access-Control-Allow-Methods']='GET,POST,OPTIONS'; return resp
    @app.route('/')
    def index():
        t=db.tip(); return jsonify({'name':'RealChain-BTC V1 Full Node','node':node_name,'height':int(t['height']),'tip_hash':t['hash'],'difficulty':db.difficulty,'note':'Node stores ledger only. User private keys stay in browser wallets.'})
    @app.route('/api/status')
    def status():
        t=db.tip()
        with db.connect() as c:
            m=c.execute('SELECT COUNT(*) c FROM mempool').fetchone()['c']; u=c.execute('SELECT COUNT(*) c FROM utxos WHERE spent=0').fetchone()['c']
        return jsonify({'node':node_name,'height':int(t['height']),'tip_hash':t['hash'],'difficulty':db.difficulty,'mempool_count':int(m),'unspent_utxo_count':int(u),'block_reward':BLOCK_REWARD})
    @app.route('/api/utxos/<address>')
    def utxos(address):
        us=db.address_utxos(address); return jsonify({'address':address,'balance':sum(int(u['amount']) for u in us),'utxos':us})
    @app.route('/api/mempool')
    def mempool(): return jsonify({'transactions':db.get_mempool()})
    @app.route('/api/blocks')
    def blocks(): return jsonify({'blocks':db.list_blocks(int(request.args.get('limit','20')))})
    @app.route('/api/tx/submit', methods=['POST','OPTIONS'])
    def tx_submit():
        if request.method == 'OPTIONS': return ('',204)
        try: return jsonify({'ok':True, **db.add_mempool(request.get_json(force=True))})
        except Exception as e: return jsonify({'ok':False,'error':str(e)}),400
    @app.route('/api/mining/template')
    def mining_template():
        try: return jsonify({'ok':True,'template':db.block_template(request.args.get('miner_address',''))})
        except Exception as e: return jsonify({'ok':False,'error':str(e)}),400
    @app.route('/api/block/submit', methods=['POST','OPTIONS'])
    def block_submit():
        if request.method == 'OPTIONS': return ('',204)
        try: return jsonify({'ok':True,'block':db.submit_block(request.get_json(force=True))})
        except Exception as e: return jsonify({'ok':False,'error':str(e)}),400
    return app

def main():
    p=argparse.ArgumentParser(description='RealChain-BTC V1 full node')
    p.add_argument('--node',default='A'); p.add_argument('--host',default='127.0.0.1'); p.add_argument('--port',type=int,default=8111); p.add_argument('--db',default='node_v1.db'); p.add_argument('--difficulty',type=int,default=2)
    a=p.parse_args(); app=create_app(NodeDB(a.db,a.difficulty), a.node)
    print(f'RealChain-BTC V1 full node {a.node} running at http://{a.host}:{a.port}')
    print('This node never stores user private keys and never acts as a wallet.')
    app.run(host=a.host, port=a.port, debug=False)
if __name__ == '__main__': main()

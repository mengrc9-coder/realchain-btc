#!/usr/bin/env python3
"""
RealChain-BTC V2 Final LAN Node
V2 = V1 stable wallet/UTXO/miner flow + LAN peer broadcast + auto sync + network monitor APIs.
"""
from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import threading
import time
import hashlib
from typing import Any, Dict, List, Tuple, Optional
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.exceptions import InvalidSignature

VERSION = "2.0.4-final"
BASE_REWARD = 50
DEFAULT_DIFFICULTY = 2
REQUEST_TIMEOUT = 3


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def now_ts() -> int:
    return int(time.time())


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    return url.rstrip("/")


def valid_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def public_key_to_address(pubkey_b64: str) -> str:
    der = base64.b64decode(pubkey_b64)
    return "RLC_" + hashlib.sha256(der).hexdigest()[:40]


def sign_payload(tx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version": tx.get("version", 1),
        "type": "transfer",
        "inputs": tx.get("inputs", []),
        "outputs": tx.get("outputs", []),
        "fee": tx.get("fee", 0),
        "timestamp": tx.get("timestamp", 0),
        "pubkey": tx.get("pubkey", ""),
    }


def txid_payload(tx: Dict[str, Any]) -> Dict[str, Any]:
    x = dict(tx)
    x.pop("txid", None)
    return x


def calc_txid(tx: Dict[str, Any]) -> str:
    return sha256_hex(canonical(txid_payload(tx)))


def block_hash_payload(block: Dict[str, Any]) -> Dict[str, Any]:
    b = dict(block)
    b.pop("hash", None)
    return b


def calc_block_hash(block: Dict[str, Any]) -> str:
    return sha256_hex(canonical(block_hash_payload(block)))


def make_coinbase(height: int, address: str, amount: int) -> Dict[str, Any]:
    tx = {
        "version": 1,
        "type": "coinbase",
        "height": height,
        "inputs": [],
        "outputs": [{"address": address, "amount": int(amount)}],
        "timestamp": now_ts(),
    }
    tx["txid"] = calc_txid(tx)
    return tx


def make_genesis_block() -> Dict[str, Any]:
    block = {
        "version": 1,
        "height": 0,
        "prev_hash": "0" * 64,
        "timestamp": 1700000000,
        "difficulty": 0,
        "nonce": 0,
        "miner": "GENESIS",
        "txs": [],
    }
    block["hash"] = calc_block_hash(block)
    return block


GENESIS_BLOCK = make_genesis_block()
GENESIS_HASH = GENESIS_BLOCK["hash"]


class ValidationError(Exception):
    pass


class NodeState:
    def __init__(self, db_path: str, node_name: str, difficulty: int, external_url: str):
        self.db_path = db_path
        self.node_name = node_name
        self.difficulty = int(difficulty)
        self.external_url = normalize_url(external_url)
        self.lock = threading.RLock()
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.lock, self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS blocks (height INTEGER PRIMARY KEY, hash TEXT UNIQUE NOT NULL, data TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS mempool (txid TEXT PRIMARY KEY, data TEXT NOT NULL, created_at INTEGER NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS peers (url TEXT PRIMARY KEY, created_at INTEGER NOT NULL)")
            cur = conn.execute("SELECT COUNT(*) AS c FROM blocks")
            if int(cur.fetchone()["c"]) == 0:
                conn.execute("INSERT INTO blocks(height, hash, data) VALUES (?, ?, ?)", (0, GENESIS_HASH, canonical(GENESIS_BLOCK)))
            conn.commit()

    def get_chain(self) -> List[Dict[str, Any]]:
        with self.lock, self.connect() as conn:
            rows = conn.execute("SELECT data FROM blocks ORDER BY height ASC").fetchall()
            return [json.loads(r["data"]) for r in rows]

    def get_tip(self) -> Dict[str, Any]:
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT data FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
            return json.loads(row["data"])

    def get_height(self) -> int:
        return int(self.get_tip()["height"])

    def get_block_by_height(self, height: int) -> Optional[Dict[str, Any]]:
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT data FROM blocks WHERE height=?", (height,)).fetchone()
            return json.loads(row["data"]) if row else None

    def insert_block(self, block: Dict[str, Any]) -> None:
        with self.lock, self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO blocks(height, hash, data) VALUES (?, ?, ?)", (int(block["height"]), block["hash"], canonical(block)))
            for tx in block.get("txs", []):
                txid = tx.get("txid")
                if txid:
                    conn.execute("DELETE FROM mempool WHERE txid=?", (txid,))
            conn.commit()

    def replace_chain(self, blocks: List[Dict[str, Any]]) -> None:
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM blocks")
            for b in blocks:
                conn.execute("INSERT INTO blocks(height, hash, data) VALUES (?, ?, ?)", (int(b["height"]), b["hash"], canonical(b)))
            conn.commit()
        self.prune_mempool()

    def get_mempool(self) -> List[Dict[str, Any]]:
        with self.lock, self.connect() as conn:
            rows = conn.execute("SELECT data FROM mempool ORDER BY created_at ASC").fetchall()
            return [json.loads(r["data"]) for r in rows]

    def mempool_has_tx(self, txid: str) -> bool:
        with self.lock, self.connect() as conn:
            return conn.execute("SELECT 1 FROM mempool WHERE txid=?", (txid,)).fetchone() is not None

    def add_mempool_tx(self, tx: Dict[str, Any]) -> bool:
        txid = tx.get("txid") or calc_txid(tx)
        tx["txid"] = txid
        with self.lock, self.connect() as conn:
            try:
                conn.execute("INSERT INTO mempool(txid, data, created_at) VALUES (?, ?, ?)", (txid, canonical(tx), now_ts()))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def list_peers(self) -> List[str]:
        with self.lock, self.connect() as conn:
            rows = conn.execute("SELECT url FROM peers ORDER BY url ASC").fetchall()
            return [r["url"] for r in rows]

    def add_peer(self, url: str) -> bool:
        url = normalize_url(url)
        if not valid_http_url(url):
            raise ValidationError("invalid peer url")
        if url == self.external_url:
            return False
        with self.lock, self.connect() as conn:
            cur = conn.execute("INSERT OR IGNORE INTO peers(url, created_at) VALUES (?, ?)", (url, now_ts()))
            conn.commit()
            return cur.rowcount > 0

    def build_utxo_from_blocks(self, blocks: Optional[List[Dict[str, Any]]] = None) -> Dict[Tuple[str, int], Dict[str, Any]]:
        if blocks is None:
            blocks = self.get_chain()
        utxo: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for block in blocks:
            for tx in block.get("txs", []):
                txid = tx["txid"]
                if tx.get("type") != "coinbase":
                    for inp in tx.get("inputs", []):
                        utxo.pop((inp["txid"], int(inp["index"])), None)
                for idx, out in enumerate(tx.get("outputs", [])):
                    utxo[(txid, idx)] = {"txid": txid, "index": idx, "address": out["address"], "amount": int(out["amount"])}
        return utxo

    def spent_by_mempool(self) -> set[Tuple[str, int]]:
        spent: set[Tuple[str, int]] = set()
        for tx in self.get_mempool():
            for inp in tx.get("inputs", []):
                spent.add((inp["txid"], int(inp["index"])))
        return spent

    def available_utxo(self) -> Dict[Tuple[str, int], Dict[str, Any]]:
        utxo = self.build_utxo_from_blocks()
        for key in self.spent_by_mempool():
            utxo.pop(key, None)
        return utxo

    def get_utxos_for_address(self, address: str) -> List[Dict[str, Any]]:
        return [u for u in self.available_utxo().values() if u["address"] == address]

    def balance_of(self, address: str) -> int:
        return sum(int(u["amount"]) for u in self.get_utxos_for_address(address))

    def validate_transfer_tx(self, tx: Dict[str, Any], utxo: Dict[Tuple[str, int], Dict[str, Any]], forbid_mempool_spends: bool = False) -> int:
        if tx.get("type") != "transfer":
            raise ValidationError("not a transfer transaction")
        if not isinstance(tx.get("inputs"), list) or not tx["inputs"]:
            raise ValidationError("transfer needs inputs")
        if not isinstance(tx.get("outputs"), list) or not tx["outputs"]:
            raise ValidationError("transfer needs outputs")
        if not isinstance(tx.get("signatures"), list) or len(tx["signatures"]) != len(tx["inputs"]):
            raise ValidationError("signature count mismatch")
        pubkey_b64 = tx.get("pubkey", "")
        if not pubkey_b64:
            raise ValidationError("missing pubkey")
        sender_address = public_key_to_address(pubkey_b64)
        try:
            public_key = serialization.load_der_public_key(base64.b64decode(pubkey_b64))
        except Exception as exc:
            raise ValidationError(f"invalid pubkey: {exc}")
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise ValidationError("pubkey must be ECDSA P-256")

        claimed_txid = tx.get("txid")
        actual_txid = calc_txid(tx)
        if claimed_txid and claimed_txid != actual_txid:
            raise ValidationError("txid mismatch")
        tx["txid"] = actual_txid

        message = canonical(sign_payload(tx)).encode("utf-8")
        spent_keys = set()
        input_sum = 0
        mempool_spent = self.spent_by_mempool() if forbid_mempool_spends else set()
        for idx, inp in enumerate(tx["inputs"]):
            key = (inp.get("txid"), int(inp.get("index", -1)))
            if key in spent_keys:
                raise ValidationError("duplicate input")
            spent_keys.add(key)
            if key in mempool_spent:
                raise ValidationError("input already spent by mempool transaction")
            coin = utxo.get(key)
            if not coin:
                raise ValidationError("input UTXO not found")
            if coin["address"] != sender_address or inp.get("address") != sender_address:
                raise ValidationError("input address does not match signer")
            if int(inp.get("amount", -1)) != int(coin["amount"]):
                raise ValidationError("input amount mismatch")
            input_sum += int(coin["amount"])

            sig_raw = base64.b64decode(tx["signatures"][idx])
            if len(sig_raw) != 64:
                raise ValidationError("ECDSA signature must be raw r||s 64 bytes")
            r = int.from_bytes(sig_raw[:32], "big")
            s = int.from_bytes(sig_raw[32:], "big")
            sig_der = utils.encode_dss_signature(r, s)
            try:
                public_key.verify(sig_der, message, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                raise ValidationError("invalid signature")

        output_sum = 0
        for out in tx["outputs"]:
            amount = int(out.get("amount", 0))
            address = out.get("address", "")
            if amount <= 0:
                raise ValidationError("output amount must be positive")
            if not isinstance(address, str) or not address.startswith("RLC_"):
                raise ValidationError("invalid output address")
            output_sum += amount
        fee = int(tx.get("fee", 0))
        if fee < 0:
            raise ValidationError("fee must be non-negative")
        if input_sum < output_sum + fee:
            raise ValidationError("inputs are less than outputs plus fee")
        return input_sum - output_sum

    def apply_tx_to_utxo(self, tx: Dict[str, Any], utxo: Dict[Tuple[str, int], Dict[str, Any]]) -> None:
        txid = tx["txid"]
        if tx.get("type") != "coinbase":
            for inp in tx.get("inputs", []):
                utxo.pop((inp["txid"], int(inp["index"])), None)
        for idx, out in enumerate(tx.get("outputs", [])):
            utxo[(txid, idx)] = {"txid": txid, "index": idx, "address": out["address"], "amount": int(out["amount"])}

    def validate_block(self, block: Dict[str, Any], prev_block: Dict[str, Any], starting_utxo: Dict[Tuple[str, int], Dict[str, Any]]) -> Tuple[int, Dict[Tuple[str, int], Dict[str, Any]]]:
        if int(block.get("height", -1)) != int(prev_block["height"]) + 1:
            raise ValidationError("wrong block height")
        if block.get("prev_hash") != prev_block["hash"]:
            raise ValidationError("wrong prev_hash")
        if block.get("hash") != calc_block_hash(block):
            raise ValidationError("block hash mismatch")
        if not str(block["hash"]).startswith("0" * int(block.get("difficulty", 0))):
            raise ValidationError("proof of work is insufficient")
        if int(block.get("difficulty", 0)) != self.difficulty:
            raise ValidationError("wrong difficulty for this network")
        txs = block.get("txs", [])
        if not txs or txs[0].get("type") != "coinbase":
            raise ValidationError("first transaction must be coinbase")
        if any(t.get("type") == "coinbase" for t in txs[1:]):
            raise ValidationError("coinbase only allowed as first transaction")
        temp_utxo = dict(starting_utxo)
        total_fees = 0
        for tx in txs[1:]:
            fee = self.validate_transfer_tx(tx, temp_utxo, forbid_mempool_spends=False)
            total_fees += fee
            self.apply_tx_to_utxo(tx, temp_utxo)
        coinbase = txs[0]
        if coinbase.get("txid") != calc_txid(coinbase):
            raise ValidationError("coinbase txid mismatch")
        if coinbase.get("inputs") != []:
            raise ValidationError("coinbase cannot have inputs")
        coinbase_out_sum = sum(int(o.get("amount", 0)) for o in coinbase.get("outputs", []))
        if coinbase_out_sum != BASE_REWARD + total_fees:
            raise ValidationError("wrong coinbase reward")
        self.apply_tx_to_utxo(coinbase, temp_utxo)
        return total_fees, temp_utxo

    def validate_chain(self, blocks: List[Dict[str, Any]]) -> None:
        if not blocks:
            raise ValidationError("empty chain")
        if blocks[0] != GENESIS_BLOCK:
            raise ValidationError("genesis block mismatch")
        utxo: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for i in range(1, len(blocks)):
            self.validate_block(blocks[i], blocks[i - 1], utxo)
            # Re-apply after validation; validate_block returns copied utxo, so use return value.
            _, utxo = self.validate_block(blocks[i], blocks[i - 1], utxo)

    def accept_transaction(self, tx: Dict[str, Any]) -> Tuple[bool, str]:
        utxo = self.build_utxo_from_blocks()
        self.validate_transfer_tx(tx, utxo, forbid_mempool_spends=True)
        added = self.add_mempool_tx(tx)
        return added, tx["txid"]

    def accept_block(self, block: Dict[str, Any]) -> Tuple[bool, str]:
        tip = self.get_tip()
        if block.get("hash") == tip.get("hash"):
            return False, "already_tip"
        if int(block.get("height", -1)) <= int(tip["height"]):
            with self.lock, self.connect() as conn:
                exists = conn.execute("SELECT 1 FROM blocks WHERE hash=?", (block.get("hash"),)).fetchone() is not None
            return False, "already_known" if exists else "stale_or_fork_block"
        utxo = self.build_utxo_from_blocks()
        self.validate_block(block, tip, utxo)
        self.insert_block(block)
        return True, block["hash"]

    def build_mining_template(self, reward_address: str) -> Dict[str, Any]:
        if not reward_address.startswith("RLC_"):
            raise ValidationError("invalid reward address")
        tip = self.get_tip()
        utxo = self.build_utxo_from_blocks()
        selected: List[Dict[str, Any]] = []
        total_fees = 0
        for tx in self.get_mempool():
            try:
                fee = self.validate_transfer_tx(tx, utxo, forbid_mempool_spends=False)
                selected.append(tx)
                total_fees += fee
                self.apply_tx_to_utxo(tx, utxo)
            except Exception:
                continue
        coinbase = make_coinbase(int(tip["height"]) + 1, reward_address, BASE_REWARD + total_fees)
        block = {
            "version": 1,
            "height": int(tip["height"]) + 1,
            "prev_hash": tip["hash"],
            "timestamp": now_ts(),
            "difficulty": self.difficulty,
            "nonce": 0,
            "miner": reward_address,
            "txs": [coinbase] + selected,
        }
        block["hash"] = calc_block_hash(block)
        return {"block": block, "fees": total_fees, "base_reward": BASE_REWARD, "tx_count": len(block["txs"]), "tip_hash": tip["hash"]}

    def prune_mempool(self) -> None:
        valid: List[str] = []
        utxo = self.build_utxo_from_blocks()
        for tx in self.get_mempool():
            try:
                self.validate_transfer_tx(tx, utxo, forbid_mempool_spends=False)
                valid.append(tx["txid"])
            except Exception:
                pass
        with self.lock, self.connect() as conn:
            rows = conn.execute("SELECT txid FROM mempool").fetchall()
            for row in rows:
                if row["txid"] not in valid:
                    conn.execute("DELETE FROM mempool WHERE txid=?", (row["txid"],))
            conn.commit()

    def sync_from_peer(self, peer: str) -> Dict[str, Any]:
        peer = normalize_url(peer)
        summary = requests.get(peer + "/chain/summary", timeout=REQUEST_TIMEOUT).json()
        local_tip = self.get_tip()
        if int(summary.get("height", -1)) <= int(local_tip["height"]):
            return {"peer": peer, "action": "no_change", "peer_height": summary.get("height"), "local_height": local_tip["height"]}
        chain = requests.get(peer + "/chain", timeout=REQUEST_TIMEOUT).json().get("blocks", [])
        self.validate_chain(chain)
        if int(chain[-1]["height"]) > int(local_tip["height"]):
            self.replace_chain(chain)
            return {"peer": peer, "action": "synced", "new_height": chain[-1]["height"], "tip_hash": chain[-1]["hash"]}
        return {"peer": peer, "action": "no_change_after_fetch"}


app = Flask(__name__, static_folder=None)
STATE: NodeState


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/", methods=["GET"])
def home():
    return jsonify(status_payload())


@app.route("/status", methods=["GET"])
def status():
    return jsonify(status_payload())


def status_payload() -> Dict[str, Any]:
    tip = STATE.get_tip()
    return {
        "ok": True,
        "project": "RealChain-BTC",
        "version": VERSION,
        "node": STATE.node_name,
        "external_url": STATE.external_url,
        "height": tip["height"],
        "tip_hash": tip["hash"],
        "difficulty": STATE.difficulty,
        "peers": STATE.list_peers(),
        "mempool_size": len(STATE.get_mempool()),
        "time": now_ts(),
    }


@app.route("/chain/summary", methods=["GET"])
def chain_summary():
    tip = STATE.get_tip()
    return jsonify({"height": tip["height"], "tip_hash": tip["hash"], "node": STATE.node_name, "version": VERSION})


@app.route("/chain", methods=["GET"])
def chain():
    return jsonify({"blocks": STATE.get_chain()})


@app.route("/blocks/latest", methods=["GET"])
def latest_block():
    return jsonify({"block": STATE.get_tip()})


@app.route("/blocks/<int:height>", methods=["GET"])
def block_by_height(height: int):
    b = STATE.get_block_by_height(height)
    if not b:
        return jsonify({"ok": False, "error": "block not found"}), 404
    return jsonify({"ok": True, "block": b})


@app.route("/mempool", methods=["GET"])
def mempool():
    return jsonify({"txs": STATE.get_mempool(), "size": len(STATE.get_mempool())})


@app.route("/balance/<address>", methods=["GET"])
def balance(address: str):
    return jsonify({"address": address, "balance": STATE.balance_of(address), "utxos": STATE.get_utxos_for_address(address)})


@app.route("/utxos/<address>", methods=["GET"])
def utxos(address: str):
    return jsonify({"address": address, "utxos": STATE.get_utxos_for_address(address), "balance": STATE.balance_of(address)})


@app.route("/transactions", methods=["POST", "OPTIONS"])
def transactions():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    tx = data.get("tx", data)
    source = normalize_url(data.get("source", "")) if data.get("source") else ""
    try:
        added, txid = STATE.accept_transaction(tx)
        if added:
            threading.Thread(target=broadcast_transaction, args=(tx, source), daemon=True).start()
        return jsonify({"ok": True, "added": added, "txid": txid})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/tx", methods=["POST", "OPTIONS"])
def tx_alias():
    return transactions()


@app.route("/blocks", methods=["POST", "OPTIONS"])
def submit_block():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    block = data.get("block", data)
    source = normalize_url(data.get("source", "")) if data.get("source") else ""
    try:
        added, info = STATE.accept_block(block)
        if added:
            threading.Thread(target=broadcast_block, args=(block, source), daemon=True).start()
        return jsonify({"ok": True, "added": added, "info": info, "height": STATE.get_height(), "tip_hash": STATE.get_tip()["hash"]})
    except Exception as exc:
        # If this is a higher block but prev is missing/stale, try syncing from source once.
        if source:
            try:
                STATE.sync_from_peer(source)
            except Exception:
                pass
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/mine/template", methods=["GET"])
def mine_template():
    address = request.args.get("address", "")
    try:
        return jsonify({"ok": True, **STATE.build_mining_template(address)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/peers", methods=["GET"])
def peers():
    return jsonify({"peers": STATE.list_peers(), "self": STATE.external_url})


@app.route("/peers/add", methods=["POST", "OPTIONS"])
def add_peer():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "")
    try:
        added = STATE.add_peer(url)
        # Try one immediate sync after adding a peer.
        sync_result = None
        try:
            sync_result = STATE.sync_from_peer(normalize_url(url))
        except Exception as exc:
            sync_result = {"action": "sync_failed", "error": str(exc)}
        return jsonify({"ok": True, "added": added, "peers": STATE.list_peers(), "sync": sync_result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/sync/now", methods=["POST", "OPTIONS"])
def sync_now():
    if request.method == "OPTIONS":
        return ("", 204)
    results = []
    for peer in STATE.list_peers():
        try:
            results.append(STATE.sync_from_peer(peer))
        except Exception as exc:
            results.append({"peer": peer, "action": "failed", "error": str(exc)})
    return jsonify({"ok": True, "results": results, "status": status_payload()})


def broadcast_transaction(tx: Dict[str, Any], source: str = "") -> None:
    for peer in STATE.list_peers():
        if source and normalize_url(peer) == source:
            continue
        try:
            requests.post(peer + "/transactions", json={"tx": tx, "source": STATE.external_url}, timeout=REQUEST_TIMEOUT)
        except Exception:
            pass


def broadcast_block(block: Dict[str, Any], source: str = "") -> None:
    for peer in STATE.list_peers():
        if source and normalize_url(peer) == source:
            continue
        try:
            requests.post(peer + "/blocks", json={"block": block, "source": STATE.external_url}, timeout=REQUEST_TIMEOUT)
        except Exception:
            pass


def auto_sync_loop(interval: int) -> None:
    while True:
        time.sleep(max(2, int(interval)))
        for peer in STATE.list_peers():
            try:
                STATE.sync_from_peer(peer)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="RealChain-BTC V2 Final LAN full node")
    parser.add_argument("--node", default="A")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8111)
    parser.add_argument("--db", default="node_v2.db")
    parser.add_argument("--difficulty", type=int, default=DEFAULT_DIFFICULTY)
    parser.add_argument("--external-url", required=True)
    parser.add_argument("--peers", default="", help="comma-separated peer URLs, e.g. http://192.168.1.20:8111")
    parser.add_argument("--sync-interval", type=int, default=5)
    args = parser.parse_args()

    global STATE
    STATE = NodeState(args.db, args.node, args.difficulty, args.external_url)
    for raw in [p.strip() for p in args.peers.split(",") if p.strip()]:
        try:
            STATE.add_peer(raw)
        except Exception as exc:
            print(f"[peer] failed to add {raw}: {exc}")

    threading.Thread(target=auto_sync_loop, args=(args.sync_interval,), daemon=True).start()

    print(f"RealChain-BTC V2 Final node {args.node} running on {args.host}:{args.port}")
    print(f"External URL: {STATE.external_url}")
    print(f"Peers: {STATE.list_peers()}")
    print(f"Difficulty: {STATE.difficulty}, Auto sync interval: {args.sync_interval}s")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()

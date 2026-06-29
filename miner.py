#!/usr/bin/env python3
"""Independent PoW miner for RealChain-BTC V2 Final."""
from __future__ import annotations

import argparse
import json
import hashlib
import time
import requests
from typing import Any, Dict


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def block_hash_payload(block: Dict[str, Any]) -> Dict[str, Any]:
    b = dict(block)
    b.pop("hash", None)
    return b


def calc_block_hash(block: Dict[str, Any]) -> str:
    return sha256_hex(canonical(block_hash_payload(block)))


def mine_once(node: str, reward_address: str, status_every: int = 10000) -> Dict[str, Any]:
    node = node.rstrip("/")
    tpl = requests.get(node + "/mine/template", params={"address": reward_address}, timeout=10).json()
    if not tpl.get("ok"):
        raise RuntimeError(tpl.get("error", "template failed"))
    block = tpl["block"]
    difficulty = int(block["difficulty"])
    target_prefix = "0" * difficulty
    nonce = 0
    started = time.time()
    while True:
        block["nonce"] = nonce
        block["timestamp"] = int(time.time())
        block["hash"] = calc_block_hash(block)
        if block["hash"].startswith(target_prefix):
            res = requests.post(node + "/blocks", json={"block": block}, timeout=10).json()
            return {"template": tpl, "submit": res, "block": block, "seconds": round(time.time() - started, 3), "nonce": nonce}
        nonce += 1
        if status_every and nonce % status_every == 0:
            rate = nonce / max(time.time() - started, 0.001)
            print(f"mining height={block['height']} nonce={nonce} hash={block['hash'][:16]}... rate={rate:.0f} H/s", flush=True)


def main():
    parser = argparse.ArgumentParser(description="RealChain-BTC V2 Final independent miner")
    parser.add_argument("--node", required=True, help="node API, e.g. http://192.168.31.210:8111")
    parser.add_argument("--reward-address", required=True, help="RLC address receiving coinbase reward")
    parser.add_argument("--once", action="store_true", help="mine one block and exit")
    parser.add_argument("--status-every", type=int, default=10000)
    args = parser.parse_args()

    while True:
        try:
            result = mine_once(args.node, args.reward_address, args.status_every)
            b = result["block"]
            submit = result["submit"]
            print(f"MINED height={b['height']} hash={b['hash']} txs={len(b['txs'])} nonce={result['nonce']} seconds={result['seconds']}")
            print("SUBMIT:", json.dumps(submit, ensure_ascii=False))
        except Exception as exc:
            print("MINER ERROR:", exc)
        if args.once:
            break
        time.sleep(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Local-only web wallet and network monitor server."""
from __future__ import annotations

import argparse
from flask import Flask, send_from_directory, redirect

app = Flask(__name__, static_folder="static")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/<path:name>")
def static_file(name: str):
    return send_from_directory("static", name)


def main():
    parser = argparse.ArgumentParser(description="RealChain-BTC V2 Final local wallet UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"RealChain-BTC wallet UI running at http://{args.host}:{args.port}")
    print("Open http://127.0.0.1:8000/wallet.html and http://127.0.0.1:8000/network.html")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()

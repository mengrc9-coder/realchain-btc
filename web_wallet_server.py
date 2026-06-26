#!/usr/bin/env python3
import argparse
from flask import Flask, send_from_directory
app = Flask(__name__, static_folder='static', static_url_path='')
@app.route('/')
def index(): return send_from_directory('static', 'index.html')
@app.route('/<path:path>')
def static_files(path): return send_from_directory('static', path)
def main():
    p=argparse.ArgumentParser(description='RealChain-BTC V1 web wallet server')
    p.add_argument('--host', default='127.0.0.1'); p.add_argument('--port', type=int, default=8000)
    a=p.parse_args()
    print(f'RealChain-BTC V1 web wallet running at http://{a.host}:{a.port}')
    print('This server only serves UI files. Private keys are not uploaded.')
    app.run(host=a.host, port=a.port, debug=False)
if __name__ == '__main__': main()

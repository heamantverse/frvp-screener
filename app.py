"""
Flask Website - PythonAnywhere par host thase.
results.json GitHub (private repo) parthi token sathe vanche chhe.
"""

import os
import time
import requests
from flask import Flask, render_template

app = Flask(__name__)

GITHUB_REPO = "heamantverse/frvp-screener"
GITHUB_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents/results.json"
CACHE_SECONDS = 600  # 10 minute cache

_cache = {"data": None, "time": 0}


def load_results():
    if _cache["data"] and time.time() - _cache["time"] < CACHE_SECONDS:
        return _cache["data"]

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN set nathi")

    resp = requests.get(
        GITHUB_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.raw+json",
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    _cache["data"] = data
    _cache["time"] = time.time()
    return data


@app.route("/")
def index():
    try:
        data = load_results()
    except Exception as e:
        return f"Data load na thayo: {e}"

    return render_template("index.html", stocks=data["stocks"], last_updated=data["last_updated"])


if __name__ == "__main__":
    app.run(debug=True)

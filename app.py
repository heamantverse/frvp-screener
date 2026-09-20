"""
Flask Website - PythonAnywhere પર "Web" tab દ્વારા host થશે
=================================================================
આ ફાઇલ results.json વાંચી, એક સાદું, sortable table તરીકે webpage
પર બતાવે છે. screener_job.py દરરોજ રાત્રે results.json update કરશે,
આ app આપોઆપ latest data બતાવશે (કંઈ redeploy કરવાની જરૂર નથી).
"""

import json
import os
from flask import Flask, render_template

app = Flask(__name__)

RESULTS_FILE = os.path.join(os.path.dirname(__file__), "results.json")


@app.route("/")
def index():
    if not os.path.exists(RESULTS_FILE):
        return "હજુ કોઈ data નથી - પહેલા screener_job.py run થવું જોઈએ (Scheduled Task setup કરો)."

    with open(RESULTS_FILE) as f:
        data = json.load(f)

    return render_template("index.html", stocks=data["stocks"], last_updated=data["last_updated"])


if __name__ == "__main__":
    app.run(debug=True)

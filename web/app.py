from flask import Flask, jsonify, render_template_string
import json

app = Flask(__name__)

TEMPLATE = """
<html>
<head><title>Verifiable Training Dashboard</title></head>
<body>
<h1>Verifiable Decentralized Training — Demo</h1>
{% for r in history %}
  <div style="border:1px solid #ccc;padding:10px;margin:10px">
    <h3>Epoch {{r['epoch']}}</h3>
    <p>Merkle Root: <code>{{r['root']}}</code></p>
    <ul>
    {% for c in r['checkpoints'] %}
      <li>Node {{c['node_id']}} — hash: <code>{{c['hash']}}</code> — weights_sum: {{c['weights_sum']}}</li>
    {% endfor %}
    </ul>
  </div>
{% endfor %}
</body>
</html>
"""

@app.route('/')
def index():
    try:
        with open('history.json') as f:
            history = json.load(f)
    except Exception:
        history = []
    return render_template_string(TEMPLATE, history=history)

@app.route('/api/history')
def api_history():
    try:
        with open('history.json') as f:
            history = json.load(f)
    except Exception:
        history = []
    return jsonify(history)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

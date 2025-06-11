from flask import Flask, request, jsonify, render_template_string, send_from_directory
import os
import logging
from datetime import datetime

app = Flask(__name__, static_folder='')
device_registry = {}

@app.route("/esp/register", methods=["POST"])
def register():
    data = request.json
    mac = data.get("mac")
    ip = request.remote_addr
    if mac:
        device_registry[mac] = {
            "ip": ip,
            "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        return jsonify({"status": "registered", "mac": mac}), 200
    return jsonify({"error": "MAC address required"}), 400

@app.route("/esp")
def dashboard():
    html = "<h1>ESP32 Dashboard</h1><ul>"
    for mac, info in device_registry.items():
        html += f"<li>{mac} — {info['ip']} — {info['last_seen']}</li>"
    html += "</ul>"
    return html

@app.route('/')
def root():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    app.logger.info(filename)
    app.logger.info(filename + "/index.html")
    if os.path.isfile(filename):
        return send_from_directory(app.static_folder, 'filename')
    elif (os.path.isfile(indexfilename = filename+"/index.html")):
        app.logger.info(indexfilename)
        return send_from_directory(app.static_folder, 'indexfilename')
    else:
        return "404 Not Found", 404

if __name__ == "__main__":
    print("Starting")
    app.run(host="0.0.0.0", port=2626, debug=True)


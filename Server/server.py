from flask import Flask, request, jsonify, render_template_string, send_from_directory, session, redirect, url_for
import os
import json
from datetime import datetime, timedelta

app = Flask(__name__, static_folder='.')
app.secret_key = 'beepboop'

REGISTRY_FILE = "device_registry.json"
device_registry = {}

# ------------------------
# Utility: Load/save registry
# ------------------------
def load_registry():
    global device_registry
    if os.path.exists(REGISTRY_FILE):
        with open(REGISTRY_FILE, "r") as f:
            device_registry = json.load(f)
    else:
        device_registry = {}

def save_registry():
    with open(REGISTRY_FILE, "w") as f:
        json.dump(device_registry, f, indent=2)

# ------------------------
# Route: ESP Registration
# ------------------------
@app.route("/esp/register", methods=["POST"])
def register():
    data = request.json
    mac = data.get("mac")
    user = data.get("user")
    passwd = data.get("pass")
    ip = request.remote_addr
    if mac:
        device_registry[mac] = {
            "ip": ip,
            "user": user,
            "pass": passwd,
            "last_seen": datetime.now().isoformat()
        }
        save_registry()
        return jsonify({"status": "registered", "mac": mac}), 200
    return jsonify({"error": "MAC address required"}), 400

# ------------------------
# User Login
# ------------------------
@app.route("/esp/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("user")
        passwd = request.form.get("pass")
        # Check against any entry in device_registry
        for info in device_registry.values():
            if info.get("user") == user and info.get("pass") == passwd:
                session['user'] = user
                return redirect(url_for("select_endpoint"))
        return "Invalid credentials", 403

    return '''
        <form method="POST">
            Username: <input name="user"><br>
            Password: <input type="password" name="pass"><br>
            <input type="submit" value="Login">
        </form>
    '''

# ------------------------
# Endpoint Selector
# ------------------------
@app.route("/esp/select")
def select_endpoint():
    if 'user' not in session:
        return redirect(url_for("login"))

    html = "<h1>Select a Device</h1><ul>"
    for mac in device_registry:
        info = device_registry.get(mac)
        html += f'<li><a href="/esp/endpoint/{mac}">{info["user"]}\'s Device</a></li>'
    html += "</ul> <a href='/esp'>Device Status Dashboard</a> <br> <a href='/esp/logout'>Logout</a>"
    return html

# ------------------------
# Endpoint Landing Page
# ------------------------
@app.route("/esp/endpoint/<mac>")
def endpoint_page(mac):
    if 'user' not in session:
        return redirect(url_for("login"))
    info = device_registry.get(mac)
    if not info:
        return "Endpoint not found", 404
    return f'''
        <h1>{info["user"]}'s device</h1>
        <p>IP: {info["ip"]}</p>
        <p>Last Seen: {info["last_seen"]}</p>
        <p>MAC Address: {mac}</p>
        <p>Registered By: {info["user"]}</p>
        <a href="/esp/select">Back to list</a>
    '''

# ------------------------
# Logout
# ------------------------
@app.route("/esp/logout")
def logout():
    session.pop('user', None)
    return redirect(url_for("dashboard"))


# ------------------------
# Route: Dashboard
# ------------------------
@app.route("/esp")
def dashboard():
    now = datetime.now()
    html = "<h1>Eink Terminal Dashboard</h1><ul>"
    for mac, info in device_registry.items():
        last_seen = datetime.fromisoformat(info['last_seen'])
        online = (now - last_seen) < timedelta(minutes=5)
        expired = (now - last_seen) > timedelta(days=365)
        status = "🟢 Online" if online else "🔴 Offline"
        if not expired:
            html += f"<li>{mac} — {info['ip']} — Last seen: {last_seen.strftime('%Y-%m-%d %H:%M:%S')} — {status}<ul><li>Username: {info['user']}</li></ul></li>"
    html += "</ul> <a href=\"/esp/select\">Select a Device</a> <br> <a href=\"/esp/login\">Login</a>"
    return html
# ------------------------
# Static File Serving
# ------------------------
@app.route('/')
def root():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    file_path = os.path.join(app.static_folder, filename)
    if os.path.isfile(file_path):
        return send_from_directory(app.static_folder, filename)
    index_path = os.path.join(app.static_folder, filename, 'index.html')
    if os.path.isfile(index_path):
        return send_from_directory(os.path.join(app.static_folder, filename), 'index.html')
    return "404 Not Found", 404

# ------------------------
# Main
# ------------------------
if __name__ == "__main__":
    load_registry()
    print("Server running on port 2626")
    app.run(host="0.0.0.0", port=2626)


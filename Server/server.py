from flask import Flask, request, jsonify, render_template_string, send_from_directory, session, redirect, url_for, send_file, render_template
import os
import json
from datetime import datetime, timedelta
from PIL import Image
import numpy as np

app = Flask(__name__, static_folder='.')
app.secret_key = 'beepboop'

REGISTRY_FILE = "device_registry.json"
device_registry = {}

UPLOAD_FOLDER = "static/images"
PALETTE = np.array([
    [0, 0, 0],         # black
    [255, 255, 255],   # white
    [0, 255, 0],       # green
    [0, 0, 255],       # blue
    [255, 0, 0],       # red
    [255, 255, 0],     # yellow
    [255, 128, 0],     # orange
], dtype=np.uint8)

def dither_to_palette(img):
    # Resize early to 600x448 to reduce processing time
    img = img.resize((600, 448)).convert("RGB")
    arr = np.array(img, dtype=np.float32)

    height, width, _ = arr.shape
    for y in range(height):
        for x in range(width):
            old_pixel = arr[y, x]
            distances = np.sum((PALETTE - old_pixel) ** 2, axis=1)
            new_pixel = PALETTE[np.argmin(distances)]
            error = old_pixel - new_pixel
            arr[y, x] = new_pixel

            # Floyd–Steinberg error diffusion
            if x + 1 < width:
                arr[y, x + 1] += error * 7 / 16
            if x - 1 >= 0 and y + 1 < height:
                arr[y + 1, x - 1] += error * 3 / 16
            if y + 1 < height:
                arr[y + 1, x] += error * 5 / 16
            if x + 1 < width and y + 1 < height:
                arr[y + 1, x + 1] += error * 1 / 16

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


@app.route("/esp/endpoint/<mac>/uploadfile", methods=["POST"])
def upload_file_image(mac):
    if 'user' not in session:
        return redirect(url_for("login"))

    if "image" not in request.files:
        return "No image uploaded", 400

    file = request.files["image"]
    if file.filename == "":
        return "Empty filename", 400

    try:
        img = Image.open(file.stream)
        dithered = dither_to_palette(img)  # Dither immediately

        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        path = os.path.join(UPLOAD_FOLDER, f"{mac}.bmp")
        dithered.save(path, format="BMP")  # Save dithered image

        return redirect(url_for("endpoint_page", mac=mac))
    except Exception as e:
        return f"Error processing image: {e}", 500

@app.route("/esp/endpoint/<mac>/upload", methods=["POST"])
def upload_image(mac):
    if 'user' not in session:
        return redirect(url_for("login"))

    if request.is_json:
        data = request.get_json()
        if "image" not in data:
            return "No image data", 400

        try:
            import base64
            from io import BytesIO

            b64data = data["image"].split(",")[1]  # remove 'data:image/png;base64,'
            img_data = base64.b64decode(b64data)
            img = Image.open(BytesIO(img_data))
            dithered = dither_to_palette(img)

            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            path = os.path.join(UPLOAD_FOLDER, f"{mac}.bmp")
            dithered.save(path, format="BMP")
            return jsonify({"status": "saved"}), 200
        except Exception as e:
            return f"Error: {e}", 500
    else:
        return "Invalid content type", 415

@app.route("/esp/save_canvas/<mac>", methods=["POST"])
def save_canvas(mac):
    if 'user' not in session:
        return redirect(url_for("login"))

    data = request.get_json()
    image_data = data.get("image")
    if not image_data:
        return "Missing image data", 400

    # Extract base64 image
    import base64
    from io import BytesIO

    header, encoded = image_data.split(",", 1)
    img_bytes = base64.b64decode(encoded)
    img = Image.open(BytesIO(img_bytes))

    path = os.path.join(UPLOAD_FOLDER, f"{mac}.bmp")
    img = img.convert("RGB").resize((600, 448))  # Ensure correct format & size
    img.save(path, format="BMP")

    return "Saved", 200


@app.route("/esp/images/<mac>.bmp")
def serve_dithered(mac):
    path = os.path.join(UPLOAD_FOLDER, f"{mac}.bmp")
    if os.path.exists(path):
        return send_file(path, mimetype="image/bmp")
    return "Image not found", 404


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

    image_path = os.path.join(UPLOAD_FOLDER, f"{mac}.bmp")
    current_image = os.path.exists(image_path)

    return render_template(
        "endpoint.html",
        mac=mac,
        user=info["user"],
        ip=info["ip"],
        last_seen=info["last_seen"],
        current_image=current_image
    )

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
    app.run(host="0.0.0.0", port=2626, debug=True)


from flask import Flask, request, jsonify, render_template_string, send_from_directory, session, redirect, url_for, send_file, render_template
import os
import time
import requests
import json
from datetime import datetime, timedelta
from PIL import Image
import numpy as np
from threading import Thread

app = Flask(__name__, static_folder='.')
app.secret_key = 'beepboop'

REGISTRY_FILE = "device_registry.json"
device_registry = {}
last_registry_save = time.time()

WEATHER_DIR = 'weather_data'
os.makedirs(WEATHER_DIR, exist_ok=True)

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

def get_location_from_ip(ip):
    response = requests.get(f"https://ipapi.co/{ip}/json/")
    if response.status_code == 200:
        data = response.json()
        return data.get("latitude"), data.get("longitude"), data.get("city"), data.get("region")
    return None, None, None, None

def fetch_weather(mac, lat, lon):
    try:
        # Get the forecast URL
        r = requests.get(f"https://api.weather.gov/points/{lat},{lon}")
        point_data = r.json()
        forecast_url = point_data['properties']['forecast']

        # Get the forecast data
        forecast = requests.get(forecast_url).json()
        periods = forecast['properties']['periods']

        # Filter and reformat the forecast data
        filtered_periods = []

        for i, period in enumerate(periods[:10]):
            base = {
                "name": period.get("name"),
                "temperature": period.get("temperature"),
                "temperatureUnit": period.get("temperatureUnit"),
                "probabilityOfPrecipitation": period.get("probabilityOfPrecipitation", {}).get("value"),
                "windSpeed": period.get("windSpeed"),
                "windDirection": period.get("windDirection"),
                "icon": period.get("icon"),
            }
            if i < 3:
                base.update({
                    "isDaytime": period.get("isDaytime"),
                    "detailedForecast": period.get("detailedForecast")
                })
            else:
                base.update({
                    "shortForecast": period.get("shortForecast")
                })
            filtered_periods.append(base)

        # Extract city/state from device registry if available
        city = device_registry.get(mac, {}).get("city", "Unknown City")
        state = device_registry.get(mac, {}).get("state", "Unknown State")

        simplified = {
            "location": f"{city}, {state}",
            "periods": filtered_periods
        }

        # Save to file
        with open(f"{WEATHER_DIR}/{mac}.json", 'w') as f:
            json.dump(simplified, f, indent=2)

        # Flag device to update display
        if device_registry[mac]['mode'] == "weather":
            device_registry[mac]['update_required'] = True

    except Exception as e:
        print(f"Weather fetch failed for {mac}: {e}")

import pytz

@app.route("/esp/schedule/<mac>", methods=["GET", "POST"])
def schedule_page(mac):
    if 'user' not in session:
        return redirect(url_for("login"))

    info = device_registry.setdefault(mac, {})  # Ensure entry exists

    if request.method == "POST":
        new_schedule = []
        starts = request.form.getlist("start")
        ends = request.form.getlist("end")
        for s, e in zip(starts, ends):
            if s.strip() and e.strip():
                new_schedule.append({"start": s.strip(), "end": e.strip()})

        timezone = request.form.get("timezone", "UTC")

        info['weather_schedule'] = new_schedule
        info['timezone'] = timezone
        save_registry()
        return redirect(url_for("schedule_page", mac=mac))

    schedule = info.get('weather_schedule', [])
    tz = info.get('timezone', 'UTC')
    user = info.get('user')
    timezones = pytz.all_timezones

    html = '''
    <h1>Schedule Weather Mode for {{ user }}</h1>
    <form method="post">
      <label for="timezone">Device Timezone:</label>
      <select name="timezone">
        {% for zone in timezones %}
          <option value="{{ zone }}" {% if zone == tz %}selected{% endif %}>{{ zone }}</option>
        {% endfor %}
      </select>
      <br><br>

      <div id="schedules">
        {% for slot in schedule %}
        <div>
          Start: <input type="time" name="start" value="{{ slot.start }}">
          End: <input type="time" name="end" value="{{ slot.end }}">
          <button type="button" onclick="this.parentElement.remove()">❌</button>
        </div>
        {% endfor %}
      </div>
      <button type="button" onclick="addRow()">➕ Add Time Slot</button><br><br>
      <input type="submit" value="Save Schedule">
    </form>

    <script>
      function addRow() {
        const div = document.createElement('div');
        div.innerHTML = 'Start: <input type="time" name="start"> End: <input type="time" name="end"> <button type="button" onclick="this.parentElement.remove()">❌</button>';
        document.getElementById("schedules").appendChild(div);
      }
    </script>
    <a href="/esp/endpoint/{{ mac }}">⬅ Back to Device</a>
    '''
    return render_template_string(html, mac=mac, schedule=schedule, tz=tz, timezones=timezones, user=user)

from pytz import timezone
def check_weather_mode_schedule(mac):
    schedule = device_registry.get(mac, {}).get('weather_schedule', [])
    now = datetime.now(timezone(device_registry[mac].get("timezone", "UTC"))).strftime("%H:%M")
    for slot in schedule:
        if slot['start'] <= now <= slot['end']:
            return True
    return False

def weather_updater():
    while True:
        for mac, data in device_registry.items():
            lat = data.get('lat', None)
            lon = data.get('lon', None)
            fetch_weather(mac, lat, lon)
        time.sleep(1800)

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
    device_registry[mac]['update_required'] = True
    # save_registry()
    return "Saved", 200


@app.route("/esp/images/<mac>.bmp")
def serve_dithered(mac):
    path = os.path.join(UPLOAD_FOLDER, f"{mac}.bmp")
    # device_registry[mac]['update_required'] = False
    if os.path.exists(path):
        return send_file(path, mimetype="image/bmp")
    return "Image not found", 404

@app.route("/esp/weather_data/<mac>.json")
def serve_weatherdata(mac):
    path = os.path.join(WEATHER_DIR, f"{mac}.json")
    # device_registry[mac]['update_required'] = False
    if os.path.exists(path):
        return send_file(path, mimetype="json")
    return "Image not found", 404



@app.route("/esp/image_version/<mac>")
def image_version(mac):
    global last_registry_save
    entry = device_registry.setdefault(mac, {})
    entry["last_seen"] = datetime.now().isoformat()
    previous_mode = entry.get("mode","image")
    # Check and update mode based on time schedule
    if 'weather_schedule' in entry:
        new_mode = 'weather' if check_weather_mode_schedule(mac) else 'image'
        entry['mode'] = new_mode
        if previous_mode != new_mode:
            entry['update_required'] = True


    if time.time() - last_registry_save > 1800:
        save_registry()
        last_registry_save = time.time()
    update_required=entry.get('update_required', False)
    device_registry[mac]['update_required'] = False
    return jsonify(
        update_required=update_required,
        mode=entry.get('mode', 'image')
    )
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
    lat, lon, city, state = get_location_from_ip(ip)
    if mac:
        device_registry[mac] = {
            "ip": ip,
            "user": user,
            "pass": passwd,
            "last_seen": datetime.now().isoformat(),
            "lat" : lat,
            "lon" : lon,
            "city" : city,
            "state" : state,
            "mode" : "image",
            "update_required" : True,
            "weather_schedule" : device_registry[mac].get("weather_schedule",[]),
            "timezone" : device_registry[mac].get("timezone","UTC")
        }
        save_registry()
        fetch_weather(mac, lat, lon)
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
        online = (now - last_seen) < timedelta(seconds=180)
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
    Thread(target=weather_updater, daemon=True).start()
    print("Server running on port 2626")
    app.run(host="0.0.0.0", port=2626)


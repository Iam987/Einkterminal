#include <WiFi.h>
#include <WebServer.h>
#include <EEPROM.h>
#include <HTTPClient.h>
#include <Ticker.h>
#include <WiFiClient.h>
#include <FS.h>
#include <SPIFFS.h>
#include "buff.h"  // POST request data accumulator
#include "epd.h"   // e-Paper driver
#include <algorithm>
#include "scripts.h"  // JavaScript code
#include "css.h"      // Cascading Style Sheets
#include "html.h"     // HTML page of the tool
#include <ArduinoJson.h>

#define IMAGE_URL_BASE "http://kool105.ddns.net:2626/esp/images/"
#define EEPROM_SIZE 96  // Total: 32 + 32 + 16 + 16
#define SSID_ADDR 0
#define PASS_ADDR 32
#define USER_ADDR 64
#define USERPASS_ADDR 80

const char* fallbackSSID = "ESP32-Setup";
const char* fallbackPASS = "configureme";

WebServer server(80);

Ticker registerTicker;
volatile bool shouldRegister = false;
volatile bool RegisterFail = false;
volatile bool WeatherFail = false;
volatile int Timeout = 5;
volatile int FailCount = 0;
String lastImageVersion = "";  // Global variable to store last known version

void IRAM_ATTR triggerRegister() {
  shouldRegister = true;
}


void registerWithServer() {
  String ssid, pass, user, userpass;
  loadWiFiCredentials(ssid, pass, user, userpass);

  String mac = WiFi.macAddress();
  HTTPClient http;
  http.begin("http://kool105.ddns.net:2626/esp/register");
  http.addHeader("Content-Type", "application/json");

  String json = "{\"mac\": \"" + mac + "\", \"user\": \"" + user + "\", \"pass\": \"" + userpass + "\"}";
  int httpCode = http.POST(json);
  if (httpCode > 0) {
    Serial.printf("Registration response: %s\n", http.getString().c_str());
    RegisterFail = false;
    Timeout = 5;
    FailCount = 0;
  } else {
    Serial.printf("Registration failed: %s\n", http.errorToString(httpCode).c_str());
    RegisterFail = true;
    Timeout = 1;
    FailCount++;
    if (FailCount > 90) ESP.restart();
  }
  http.end();
}

void fetchAndMaybeDisplayBMP(const String& mac) {
  String versionUrl = "http://kool105.ddns.net:2626/esp/image_version/" + mac;

  HTTPClient versionHttp;
  versionHttp.begin(versionUrl);
  int code = versionHttp.GET();

  if (code == 200) {
    String payload = versionHttp.getString();
    versionHttp.end();

    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, payload);
    if (error) {
      Serial.println("Failed to parse version JSON");
      return;
    }

    bool needsUpdate = doc["update_required"];
    String mode = doc["mode"] | "image";

    Serial.printf("Mode: %s | Update Required: %s\n", mode.c_str(), needsUpdate ? "Yes" : "No");

    if (!needsUpdate && !WeatherFail) {
      Serial.print(".");
      Timeout = 5;
      FailCount = 0;
      return;
    }

    if (mode == "weather") {
      Serial.println("Fetching weather...");
      fetchAndDisplayBMP(mac, "weather_data/");
    } else {
      Serial.println("Fetching image...");
      fetchAndDisplayBMP(mac, "images/");
    }

    Timeout = 5;
    FailCount = 0;
  } else {
    Serial.println("Version check failed");
    Timeout = 1;
    FailCount++;
    if (FailCount > 90) ESP.restart();
    versionHttp.end();
  }
}



void fetchAndDisplayBMP(const String& mac, const String& path1) {
  String url = "http://kool105.ddns.net:2626/esp/" + path1 + mac + ".bmp";

  HTTPClient http;
  http.begin(url);
  int httpCode = http.GET();

  if (httpCode == 200) {
    WiFiClient* stream = http.getStreamPtr();

    // Skip BMP header (54 bytes)
    for (int i = 0; i < 54; i++) {
      if (stream->available()) stream->read();
    }

    const int width = 600;
    const int height = 448;
    const int rowBytes = width * 3;
    uint8_t rowBuf[rowBytes];
    bool toggle = false;
    uint8_t last = 0;

    EPD_5IN65F_init();

    for (int row = 0; row < height; ++row) {
      while (stream->available() < rowBytes)
        ;
      stream->readBytes(rowBuf, rowBytes);

      for (int col = 0; col < width; col += 2) {
        // Flip pairs of pixels: col and col+1 are swapped
        for (int j = 1; j >= 0; --j) {
          int flippedCol = col + j;
          if (flippedCol >= width) continue;
          int i = flippedCol * 3;

          uint8_t b = rowBuf[i];
          uint8_t g = rowBuf[i + 1];
          uint8_t r = rowBuf[i + 2];

          uint8_t epdColor = rgbToEpdIndex(r, g, b);

          if (toggle) {
            EPD_SendData((epdColor << 4) | last);
          } else {
            last = epdColor;
          }

          toggle = !toggle;
        }
      }
    }

    if (!toggle) {
      EPD_SendData(last << 4);
    }

    EPD_5IN65F_Show();
  } else {
    Serial.println("Failed to fetch image");
  }

  http.end();
}

uint8_t rgbToEpdIndex(uint8_t r, uint8_t g, uint8_t b) {
  if (r < 64 && g < 64 && b < 64) return 0;               // Black
  if (r > 200 && g > 200 && b > 200) return 1;            // White
  if (r < 80 && g > 180 && b < 80) return 2;              // Green
  if (r < 80 && g < 80 && b > 180) return 3;              // Blue
  if (r > 180 && g < 80 && b < 80) return 4;              // Red
  if (r > 200 && g > 180 && b < 100) return 5;            // Yellow
  if (r > 200 && g > 100 && g < 180 && b < 80) return 6;  // Orange
  return 1;                                               // Default to white
}


void saveWiFiCredentials(const String& ssid, const String& pass, const String& user, const String& userpass) {
  for (int i = 0; i < 32; ++i) {
    EEPROM.write(SSID_ADDR + i, i < ssid.length() ? ssid[i] : 0);
    EEPROM.write(PASS_ADDR + i, i < pass.length() ? pass[i] : 0);
  }
  for (int i = 0; i < 16; ++i) {
    EEPROM.write(USER_ADDR + i, i < user.length() ? user[i] : 0);
    EEPROM.write(USERPASS_ADDR + i, i < userpass.length() ? userpass[i] : 0);
  }
  EEPROM.commit();
}

void loadWiFiCredentials(String& ssid, String& pass, String& user, String& userpass) {
  char ssidBuf[33], passBuf[33], userBuf[17], userpassBuf[17];
  for (int i = 0; i < 32; ++i) {
    ssidBuf[i] = EEPROM.read(SSID_ADDR + i);
    passBuf[i] = EEPROM.read(PASS_ADDR + i);
  }
  for (int i = 0; i < 16; ++i) {
    userBuf[i] = EEPROM.read(USER_ADDR + i);
    userpassBuf[i] = EEPROM.read(USERPASS_ADDR + i);
  }
  ssidBuf[32] = passBuf[32] = '\0';
  userBuf[16] = userpassBuf[16] = '\0';
  ssid = String(ssidBuf);
  pass = String(passBuf);
  user = String(userBuf);
  userpass = String(userpassBuf);
}

void startAPMode() {
  WiFi.disconnect(true);  // Clear any previous WiFi connection
  delay(1000);

  WiFi.mode(WIFI_AP);
  bool apResult = WiFi.softAP(fallbackSSID, fallbackPASS);
  IPAddress IP = WiFi.softAPIP();
  Serial.println(apResult ? "AP Started" : "AP Failed");
  Serial.print("AP IP Address: ");
  Serial.println(IP);

  server.on("/", []() {
    server.send(200, "text/html", R"rawliteral(
    <form action="/save" method="GET">
      SSID: <input name="ssid"><br>
      Password: <input name="pass" type="password"><br>
      Username: <input name="user"><br>
      User Password: <input name="userpass" type="password"><br>
      <input type="submit" value="Save">
    </form>
    <form action="/restart" method="GET">
      <input type="submit" value="Restart">
    </form>
  )rawliteral");
  });

  server.on("/save", []() {
    String ssid = server.arg("ssid");
    String pass = server.arg("pass");
    String user = server.arg("user");
    String userpass = server.arg("userpass");
    saveWiFiCredentials(ssid, pass, user, userpass);
    server.send(200, "text/html", "Saved. Restarting...");
    delay(1000);
    ESP.restart();
  });

  server.on("/restart", []() {
    server.send(200, "text/html", "Restarting...");
    delay(1000);
    ESP.restart();
  });

  server.begin();
  Serial.println("Web server started. Connect to 192.168.4.1");
}

bool tryConnectWiFi(const String& ssid, const String& pass, int timeout = 10000) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid.c_str(), pass.c_str());
  Serial.print("Connecting to WiFi");

  int elapsed = 0;
  while (WiFi.status() != WL_CONNECTED && elapsed < timeout) {
    delay(500);
    Serial.print(".");
    elapsed += 500;
  }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

void fetchWebText() {
  HTTPClient http;
  http.begin("http://kool105.ddns.net:2626/esp");  // Replace with your URL
  int httpCode = http.GET();
  if (httpCode > 0) {
    String payload = http.getString();
    Serial.println("Web content:");
    Serial.println(payload);
  } else {
    Serial.printf("HTTP GET failed: %s\n", http.errorToString(httpCode).c_str());
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  EEPROM.begin(EEPROM_SIZE);

  // SPI initialization
  EPD_initSPI();

  String ssid, pass, user, userpass;
  loadWiFiCredentials(ssid, pass, user, userpass);

  if (ssid.length() < 1 || !tryConnectWiFi(ssid, pass)) {
    Serial.println("WiFi failed. Starting fallback AP mode.");
    startAPMode();
  } else {
    Serial.print("Connected to ");
    Serial.println(ssid);
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
    registerWithServer();
    fetchAndMaybeDisplayBMP(WiFi.macAddress());
    registerTicker.attach(Timeout, triggerRegister);
  }
}

void loop() {
  server.handleClient();

  if (shouldRegister) {
    shouldRegister = false;
    if (!RegisterFail) {
      fetchAndMaybeDisplayBMP(WiFi.macAddress());
    } else {
      registerWithServer();
    }
    registerTicker.attach(Timeout, triggerRegister);
  }
}

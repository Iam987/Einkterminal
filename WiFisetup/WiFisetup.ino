#include <WiFi.h>
#include <WebServer.h>
#include <EEPROM.h>
#include <HTTPClient.h>
#include <Ticker.h>

#define EEPROM_SIZE 96
#define SSID_ADDR 0
#define PASS_ADDR 32

const char* fallbackSSID = "ESP32-Setup";
const char* fallbackPASS = "configureme";

WebServer server(80);

Ticker registerTicker;
volatile bool shouldRegister = false;
volatile int Timeout = 290;
volatile int FailCount = 0;

void IRAM_ATTR triggerRegister() {
  shouldRegister = true;
}

void registerWithServer() {
  String mac = WiFi.macAddress();
  HTTPClient http;
  http.begin("http://kool105.ddns.net:2626/esp/register");
  http.addHeader("Content-Type", "application/json");

  String json = "{\"mac\": \"" + mac + "\"}";
  int httpCode = http.POST(json);
  if (httpCode > 0) {
    Serial.printf("Registration response: %s\n", http.getString().c_str());
    Timeout = 290;
    FailCount = 0
  } else {
    Serial.printf("Registration failed: %s\n", http.errorToString(httpCode).c_str());
    Timeout = 10;
    FailCount += 1;
    if (FailCount > 30){
      ESP.restart();
    }
  }
  http.end();
}


void saveWiFiCredentials(const String& ssid, const String& pass) {
  for (int i = 0; i < 32; ++i) {
    EEPROM.write(SSID_ADDR + i, i < ssid.length() ? ssid[i] : 0);
    EEPROM.write(PASS_ADDR + i, i < pass.length() ? pass[i] : 0);
  }
  EEPROM.commit();
}

void loadWiFiCredentials(String& ssid, String& pass) {
  char ssidBuf[33], passBuf[33];
  for (int i = 0; i < 32; ++i) {
    ssidBuf[i] = EEPROM.read(SSID_ADDR + i);
    passBuf[i] = EEPROM.read(PASS_ADDR + i);
  }
  ssidBuf[32] = '\0';
  passBuf[32] = '\0';
  ssid = String(ssidBuf);
  pass = String(passBuf);
}

void startAPMode() {
  WiFi.disconnect(true);      // Clear any previous WiFi connection
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
    saveWiFiCredentials(ssid, pass);
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
  http.begin("http://kool105.ddns.net:2626/esp"); // Replace with your URL
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

  String ssid, pass;
  loadWiFiCredentials(ssid, pass);

  if (ssid.length() < 1 || !tryConnectWiFi(ssid, pass)) {
    Serial.println("WiFi failed. Starting fallback AP mode.");
    startAPMode();
  } else {
    Serial.print("Connected to ");
    Serial.println(ssid);
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
    registerWithServer();
    registerTicker.attach(Timeout,triggerRegister);
    
  }
}

void loop() {
  server.handleClient();

  if (shouldRegister){
    shouldRegister = false;
    registerWithServer();
    registerTicker.attach(Timeout,triggerRegister);
  }
}

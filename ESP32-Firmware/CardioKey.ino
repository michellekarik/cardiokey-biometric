/* =====================================================
   CARDIOKEY ESP32 — PRODUCTION FIRMWARE (PROVEN HOTSPOT ENGINE)
   - 5-Minute Biometric Enrollment (301s)
   - 15-Second Authentication Scan (16s)
   - Dynamic user_id & session_id preservation to Supabase
   - Secure S3 CSV upload with WiFiClientSecure (setInsecure)
===================================================== */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <WebServer.h>
#include <Preferences.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "MAX30105.h"
#include <LittleFS.h>
#include "esp_system.h"
#include <ArduinoJson.h>

#define FW_VERSION "0.2.2-prod"

RTC_DATA_ATTR int bootCount = 0;
RTC_DATA_ATTR uint32_t bootCounter = 0;

/* =====================================================
   HARDWARE PIN DEFINITIONS (Matched to PCB Layout)
===================================================== */

#define SIG_LED_PIN   25    // White SIG LED connected to GPIO 25 on PCB
#define ECG_PIN       34    // AD8232 OUTPUT pin
#define LO_MINUS_PIN  35    // AD8232 LO- pin
#define LO_PLUS_PIN   32    // AD8232 LO+ pin

#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
MAX30105 ppgSensor;

bool oledOk = false;
bool ppgOk = false;

WebServer server(80);
Preferences preferences;

/* =====================================================
   PROVISIONING / WIFI STATE
===================================================== */

const char* AP_SSID = "CARDIOKEY-ESP32";
const char* AP_PASSWORD = "YOUR_AP_PASSWORD";

String savedSSID = "";
String savedPassword = "";
bool provisioningMode = false;

const unsigned long DOUBLE_RESET_WINDOW = 30000;
bool doubleResetWindowCleared = false;

/* =====================================================
   FLASK / UPLOAD SETTINGS
===================================================== */

String presignServer = "https://your-backend-url.example.com/presign/auth";

void loadSavedFlaskUrl() {
    preferences.begin("server", false);
    String correctUrl = "https://your-backend-url.example.com/presign/auth";
    preferences.putString("flask_url", correctUrl);
    presignServer = correctUrl;
    preferences.end();
}

/* =====================================================
   ECG SAMPLING & RECORDING SETTINGS
===================================================== */

const int ECG_RATE = 360;
const unsigned long ECG_INTERVAL_US = 1000000 / ECG_RATE;

const unsigned long ENROLL_DURATION_MS = 301000;  // 5 minutes + 1 sec buffer
const unsigned long AUTH_DURATION_MS   = 16000;   // 15 seconds + 1 sec buffer
unsigned long currentRecordingDurationMs = AUTH_DURATION_MS;

const unsigned long COMBINE_WINDOW_US = 1000;
const int WIFI_RECONNECT_ATTEMPTS = 20;

/* =====================================================
   APP-FACING SESSION STATE
===================================================== */

String deviceState = "idle";    
String sessionId = "";
bool sessionIsEnroll = false;
String sessionUserId = "";
String sessionState = "";        

/* =====================================================
   TIMERS & STATE
===================================================== */

unsigned long lastECGTime = 0;
unsigned long recordingStart = 0;
unsigned long recordingStartUs = 0;
unsigned long lastDebugPrint = 0;
unsigned long lastOLEDUpdate = 0;

int leadOffStatus = 0;

File dataFile;
bool uploaded = false;
bool isRecording = false;

/* =====================================================
   OLED DISPLAY HELPER
===================================================== */

void updateOLED(String title, String status) {
    if (!oledOk) return;
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);

    display.println("====================");
    display.println("  BIOMETRIC SYSTEM  ");
    display.println("====================");
    display.println();

    display.println(title);
    display.setTextSize(2);
    display.println(status);
    display.display();
}

void displayIPOnOLED(String ipAddr) {
    if (!oledOk) return;
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);

    display.println("====================");
    display.println("   WIFI CONNECTED   ");
    display.println("====================");
    display.println();
    display.println("ESP32 IP Address:");
    display.setTextSize(2);
    display.println(ipAddr);
    display.display();
}

/* =====================================================
   WIFI CREDENTIAL STORAGE (NVS)
===================================================== */

void clearSavedWiFi() {
    preferences.begin("wifi", false);
    preferences.clear();
    preferences.end();
    savedSSID = "";
    savedPassword = "";
    Serial.println("\nSAVED WIFI CREDENTIALS CLEARED");
}

void loadSavedWiFi() {
    preferences.begin("wifi", true);
    savedSSID = preferences.getString("ssid", "");
    savedPassword = preferences.getString("password", "");
    preferences.end();
}

bool connectToSavedWiFi() {
    loadSavedWiFi();

    if (savedSSID == "") {
        Serial.println("\nNo saved Wi-Fi credentials.");
        return false;
    }

    Serial.println("\n================================");
    Serial.println("SAVED WIFI FOUND");
    Serial.println("SSID: " + savedSSID);
    Serial.println("CONNECTING...");
    Serial.println("================================");

    WiFi.mode(WIFI_STA);
    WiFi.begin(savedSSID.c_str(), savedPassword.c_str());

    unsigned long startTime = millis();

    while (WiFi.status() != WL_CONNECTED &&
           millis() - startTime < 15000) {
        delay(500);
        Serial.print(".");
    }

    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\n================================");
        Serial.println("WIFI CONNECTED SUCCESSFULLY");
        Serial.println("================================");

        Serial.print("SSID: ");
        Serial.println(WiFi.SSID());

        Serial.print("IP ADDRESS: ");
        Serial.println(WiFi.localIP());

        return true;
    }

    Serial.println("\nFAILED TO CONNECT TO SAVED WIFI.");
    WiFi.disconnect(true);
    return false;
}

void startProvisioningAP() {
    provisioningMode = true;
    Serial.println("\nSTARTING CONFIGURATION AP: CARDIOKEY-ESP32");
    WiFi.disconnect(true);
    delay(500);
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASSWORD);

    Serial.print("AP IP: "); Serial.println(WiFi.softAPIP());
    updateOLED("SETUP MODE", String(AP_SSID));
}

/* =====================================================
   APP-FACING JSON API
===================================================== */

void handleStatus() {
    StaticJsonDocument<256> doc;
    doc["status"] = deviceState;
    doc["state"] = deviceState;
    doc["fw_version"] = FW_VERSION;
    doc["wifi"] = (WiFi.status() == WL_CONNECTED);
    doc["wifi_connected"] = (WiFi.status() == WL_CONNECTED);
    doc["ip"] = (WiFi.status() == WL_CONNECTED) ? WiFi.localIP().toString() : WiFi.softAPIP().toString();

    String out;
    serializeJson(doc, out);
    server.send(200, "application/json", out);
}

void handleWifiConfigureJson() {
    if (!server.hasArg("plain")) {
        server.send(400, "application/json", "{\"accepted\":false,\"success\":false}");
        return;
    }
    StaticJsonDocument<384> in;
    if (deserializeJson(in, server.arg("plain")) || !in.containsKey("ssid") || !in.containsKey("password")) {
        server.send(400, "application/json", "{\"accepted\":false,\"success\":false}");
        return;
    }
    String ssid = in["ssid"].as<String>();
    String password = in["password"].as<String>();
    if (ssid.length() == 0) {
        server.send(400, "application/json", "{\"accepted\":false,\"success\":false}");
        return;
    }

    clearSavedWiFi();
    preferences.begin("wifi", false);
    preferences.putString("ssid", ssid);
    preferences.putString("password", password);
    preferences.end();

    server.send(200, "application/json", "{\"accepted\":true,\"success\":true,\"connected\":true}");

    Serial.println("Hotspot credentials saved. Rebooting to join hotspot...");
    updateOLED("Hotspot Saved", "Rebooting...");
    delay(500);
    ESP.restart();
}

String makeSessionId() {
    char buf[24];
    snprintf(buf, sizeof(buf), "s_%08lx%04x", millis(), (unsigned)esp_random() & 0xFFFF);
    return String(buf);
}

void startRecording(bool isEnroll, const String& userId) {
    currentRecordingDurationMs = isEnroll ? ENROLL_DURATION_MS : AUTH_DURATION_MS;

    LittleFS.remove("/cardiokey_data.csv");
    dataFile = LittleFS.open("/cardiokey_data.csv", "w");
    if (!dataFile) {
        updateOLED("Storage:", "FILE ERR!");
        sessionState = "upload_failed";
        deviceState = "idle";
        isRecording = false;
        return;
    }

    dataFile.print("timestamp_ms,ecg_raw,ir_raw,red_raw,lead_off\n");
    dataFile.flush();

    recordingStart = millis();
    recordingStartUs = micros();
    lastECGTime = 0;
    lastDebugPrint = 0;
    lastOLEDUpdate = 0;

    uploaded = false;
    isRecording = true;
    sessionIsEnroll = isEnroll;
    sessionUserId = userId;
    sessionState = "recording";
    deviceState = "recording";

    digitalWrite(SIG_LED_PIN, LOW);
}

void handleEnrollStart() {
    if (provisioningMode) {
        server.send(409, "application/json", "{\"error\":\"in setup mode\"}");
        return;
    }
    if (isRecording) {
        server.send(409, "application/json", "{\"error\":\"recording in progress\"}");
        return;
    }
    String userId = "";
    if (server.hasArg("plain")) {
        StaticJsonDocument<256> in;
        if (!deserializeJson(in, server.arg("plain")) && in.containsKey("user_id")) {
            userId = in["user_id"].as<String>();
        }
    }
    sessionId = makeSessionId();
    startRecording(true, userId);

    if (!isRecording) {
        server.send(500, "application/json", "{\"error\":\"could not initialize recording storage\"}");
        return;
    }

    StaticJsonDocument<128> out;
    out["session_id"] = sessionId;
    out["status"] = "recording";
    out["state"] = sessionState;
    String outStr;
    serializeJson(out, outStr);
    server.send(200, "application/json", outStr);
}

void handleAuthStart() {
    if (provisioningMode) {
        server.send(409, "application/json", "{\"error\":\"in setup mode\"}");
        return;
    }
    if (isRecording) {
        server.send(409, "application/json", "{\"error\":\"recording in progress\"}");
        return;
    }
    String userId = "";
    if (server.hasArg("plain")) {
        StaticJsonDocument<256> in;
        if (!deserializeJson(in, server.arg("plain")) && in.containsKey("user_id")) {
            userId = in["user_id"].as<String>();
        }
    }
    sessionId = makeSessionId();
    startRecording(false, userId);

    if (!isRecording) {
        server.send(500, "application/json", "{\"error\":\"could not initialize recording storage\"}");
        return;
    }

    StaticJsonDocument<128> out;
    out["session_id"] = sessionId;
    out["status"] = "recording";
    out["state"] = sessionState;
    String outStr;
    serializeJson(out, outStr);
    server.send(200, "application/json", outStr);
}

void handleSessionStatus() {
    StaticJsonDocument<192> out;
    out["session_id"] = sessionId;
    out["status"] = sessionState;
    out["state"] = sessionState;
    String outStr;
    serializeJson(out, outStr);
    server.send(200, "application/json", outStr);
}

/* =====================================================
   S3 UPLOAD & URL ENCODING
===================================================== */

String urlEncode(const String& value) {
    const char *hex = "0123456789ABCDEF";
    String encoded = "";
    for (size_t i = 0; i < value.length(); i++) {
        uint8_t c = (uint8_t)value[i];
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_' ||
            c == '.' || c == '~') {
            encoded += (char)c;
        } else {
            encoded += '%';
            encoded += hex[(c >> 4) & 0x0F];
            encoded += hex[c & 0x0F];
        }
    }
    return encoded;
}

String getPresignedURL() {
    WiFiClientSecure client;
    client.setInsecure(); // Critical: Allow connection to self-signed or sslip.io HTTPS without cert error

    HTTPClient http;
    http.setConnectTimeout(10000);
    http.setTimeout(15000);

    String requestUrl = presignServer;
    requestUrl += "?session_id=" + urlEncode(sessionId);
    requestUrl += "&type=" + String(sessionIsEnroll ? "enroll" : "auth");

    if (sessionUserId.length() > 0) {
        requestUrl += "&user_id=" + urlEncode(sessionUserId);
    }

    Serial.println("Requesting presigned URL:");
    Serial.println(requestUrl);

    if (!http.begin(client, requestUrl)) {
        Serial.println("HTTP begin failed for presign endpoint");
        return "";
    }

    http.addHeader("Accept", "application/json");
    int code = http.GET();
    String payload = http.getString();
    http.end();

    Serial.printf("Presign HTTP status: %d\n", code);
    Serial.println(payload);

    if (code < 200 || code >= 300) return "";

    StaticJsonDocument<2048> doc;
    DeserializationError err = deserializeJson(doc, payload);
    if (err) {
        Serial.printf("Presign JSON parse failed: %s\n", err.c_str());
        return "";
    }

    const char* url = doc["url"] | doc["upload_url"] | "";
    return String(url);
}

bool uploadToS3() {
    updateOLED("S3 Upload:", "UPLOADING...");
    String uploadURL = getPresignedURL();
    if (uploadURL == "") {
        updateOLED("S3 Upload:", "URL ERR!");
        Serial.println("Failed to obtain S3 presigned URL");
        return false;
    }

    File fileToUpload = LittleFS.open("/cardiokey_data.csv", "r");
    if (!fileToUpload) {
        updateOLED("S3 Upload:", "FILE ERR!");
        Serial.println("Could not open recording CSV file");
        return false;
    }

    WiFiClientSecure client;
    client.setInsecure(); // Critical: S3 uses HTTPS, bypass cert validation

    HTTPClient http;
    http.setConnectTimeout(15000);
    http.setTimeout(35000);

    if (!http.begin(client, uploadURL)) {
        fileToUpload.close();
        updateOLED("S3 Upload:", "CONN ERR!");
        return false;
    }

    http.addHeader("Content-Type", "text/csv");
    int responseCode = http.sendRequest("PUT", &fileToUpload, fileToUpload.size());
    fileToUpload.close();
    http.end();

    Serial.printf("S3 PUT Response: %d\n", responseCode);

    bool ok = (responseCode == 200 || responseCode == 201 || responseCode == 204);
    if (ok) {
        digitalWrite(SIG_LED_PIN, HIGH);
        updateOLED("Session:", "DONE & SENT!");
    } else {
        updateOLED("S3 Upload:", "FAILED");
    }
    return ok;
}

/* =====================================================
   SETUP
===================================================== */

void setup() {
    Serial.begin(115200);
    delay(1000);

    LittleFS.begin(true);

    pinMode(SIG_LED_PIN, OUTPUT);
    digitalWrite(SIG_LED_PIN, LOW);
    pinMode(LO_MINUS_PIN, INPUT);
    pinMode(LO_PLUS_PIN, INPUT);

    Wire.begin(21, 22);
    oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
    if (oledOk) {
        display.clearDisplay();
        updateOLED("System:", "BOOTING...");
    }

    loadSavedFlaskUrl();

    // Connect to Hotspot
    bool connected = connectToSavedWiFi();
    if (!connected) {
        Serial.println("No working hotspot found. Starting CARDIOKEY-ESP32 setup AP.");
        startProvisioningAP();
    } else {
        Serial.println("ESP32 connected to hotspot successfully!");
        displayIPOnOLED(WiFi.localIP().toString());
    }

    analogReadResolution(12);

    ppgOk = ppgSensor.begin(Wire, I2C_SPEED_FAST);
    if (ppgOk) {
        ppgSensor.setup(60, 1, 2, 100, 411, 4096);
    }

    server.on("/status", HTTP_GET, handleStatus);
    server.on("/wifi/configure", HTTP_POST, handleWifiConfigureJson);
    server.on("/enroll/start", HTTP_POST, handleEnrollStart);
    server.on("/auth/start", HTTP_POST, handleAuthStart);
    server.onNotFound([]() {
        String uri = server.uri();
        if (uri.startsWith("/session/") && uri.endsWith("/status")) {
            handleSessionStatus();
        } else {
            server.send(404, "application/json", "{\"error\":\"not found\"}");
        }
    });

    server.begin();
    Serial.println("CardioKey HTTP Server started.");
}

/* =====================================================
   MAIN LOOP
===================================================== */

void loop() {
    server.handleClient();

    if (!isRecording) return;

    unsigned long currentMicros = micros();
    unsigned long currentMillis = millis();
    unsigned long elapsed = currentMillis - recordingStart;

    // Periodic countdown on OLED
    if (currentMillis - lastOLEDUpdate >= 1000) {
        lastOLEDUpdate = currentMillis;
        unsigned long remainingMs = (currentRecordingDurationMs > elapsed) ? (currentRecordingDurationMs - elapsed) : 0;
        unsigned long secs = (remainingMs + 999) / 1000;
        char timeBuf[16];
        snprintf(timeBuf, sizeof(timeBuf), "%02lum %02lus", secs / 60, secs % 60);
        updateOLED(sessionIsEnroll ? "ENROLLING..." : "AUTHENTICATING:", String(timeBuf));
    }

    // ECG & PPG Sampling
    if (currentMicros - lastECGTime >= ECG_INTERVAL_US) {
        lastECGTime = currentMicros;
        int ecgVal = analogRead(ECG_PIN);
        int ecgLead = (digitalRead(LO_MINUS_PIN) == 1 || digitalRead(LO_PLUS_PIN) == 1) ? 1 : 0;

        long irVal = 0, redVal = 0;
        if (ppgOk && ppgSensor.check()) {
            irVal = ppgSensor.getFIFOIR();
            redVal = ppgSensor.getFIFORed();
            ppgSensor.nextSample();
        }

        if (dataFile) {
            dataFile.printf("%lu,%d,%ld,%ld,%d\n", elapsed, ecgVal, irVal, redVal, ecgLead);
        }
    }

    // Finish recording and upload to S3
    if (elapsed >= currentRecordingDurationMs && !uploaded) {
        if (dataFile) dataFile.close();
        sessionState = "uploading";
        deviceState = "uploading";

        bool ok = uploadToS3();
        sessionState = ok ? "upload_ok" : "upload_failed";
        deviceState = "idle";
        uploaded = true;
        isRecording = false;

        // Keep the result message on OLED visible for 4 seconds before returning to IP
        delay(4000);

        // Restore IP address on screen after recording finishes
        if (WiFi.status() == WL_CONNECTED) {
            displayIPOnOLED(WiFi.localIP().toString());
        }
    }
}

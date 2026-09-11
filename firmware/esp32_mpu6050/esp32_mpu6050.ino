/*
 * AI Gym & Fitness Assistant - ESP32 + MPU6050 IMU node
 * =====================================================
 *
 * Reads the MPU6050 at 50 Hz and publishes raw 6-DoF samples as JSON, either
 * over MQTT (preferred) or plain HTTP POST (fallback for networks that block
 * port 1883 - most college wifi does).
 *
 * The rep DETECTION is done on the server, not here. That is deliberate:
 *   - the detector and the fusion layer stay in one language, in one repo,
 *     covered by one test suite;
 *   - you can re-run detection on archived raw data with new thresholds,
 *     which is exactly what the evaluation script does. Firmware that only
 *     reports "rep!" throws away the evidence you need for the report.
 *
 * ---------------------------------------------------------------- WIRING --
 *   MPU6050        ESP32 (DevKit v1)
 *   -----------    -----------------
 *   VCC            3V3      <-- NOT 5V
 *   GND            GND
 *   SCL            GPIO 22
 *   SDA            GPIO 21
 *   AD0            GND      (sets I2C address 0x68)
 *
 * ------------------------------------------------------------ LIBRARIES --
 * Arduino IDE -> Tools -> Board -> Boards Manager -> install "esp32" by Espressif.
 * Then Sketch -> Include Library -> Manage Libraries, install:
 *   - "Adafruit MPU6050"   by Adafruit   (pulls in Adafruit Unified Sensor)
 *   - "PubSubClient"       by Nick O'Leary
 *   - "ArduinoJson"        by Benoit Blanchon  (v7)
 *
 * ------------------------------------------------------------- COMMON ERRORS
 * "Failed to find MPU6050 chip"
 *      -> SDA/SCL swapped, or the board is on 5V, or AD0 is floating.
 *         Run an I2C scanner; the sensor must answer at 0x68 (0x69 if AD0 high).
 * "Brownout detector was triggered"
 *      -> the USB cable or port cannot supply enough current. Use a different
 *         cable, or power the board from a proper 5V supply.
 * MQTT state -2 forever
 *      -> the broker is unreachable from this network. Set USE_HTTP to true.
 * Samples arrive but the server never detects a rep
 *      -> check the axis: the working axis must actually rotate. Open
 *         /api/imu/status and confirm samples_seen is climbing, then move the
 *         sensor through a full rep slowly.
 */

#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <Wire.h>
#include <HTTPClient.h>

// ============================ CONFIGURE ME ================================
const char *WIFI_SSID     = "YOUR_WIFI_NAME";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Must match the "Device ID" field on the Profile page of the web app.
const char *DEVICE_ID = "esp32-a1";

// ---- Transport A: MQTT (preferred) ----
const bool  USE_MQTT   = true;
const char *MQTT_HOST  = "broker.emqx.io";   // free public test broker
const int   MQTT_PORT  = 1883;
const char *MQTT_USER  = "";                 // blank for the public broker
const char *MQTT_PASS  = "";

// ---- Transport B: HTTP fallback ----
// Set USE_MQTT=false and put your Render backend URL here.
const bool  USE_HTTP   = false;
const char *HTTP_URL   = "https://YOUR-BACKEND.onrender.com/api/imu/ingest/batch";

// ---- Sampling ----
const int SAMPLE_HZ    = 50;      // 50 Hz is plenty; the rep band is under 3 Hz
const int BATCH_SIZE   = 10;      // samples per HTTP POST (ignored for MQTT)
// ==========================================================================

const unsigned long SAMPLE_PERIOD_MS = 1000UL / SAMPLE_HZ;

Adafruit_MPU6050 mpu;
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

char topic[64];
unsigned long lastSample = 0;
unsigned long lastReport = 0;
unsigned long sampleCount = 0;

// HTTP batching buffer
StaticJsonDocument<3072> batchDoc;
JsonArray batchArr;

// --------------------------------------------------------------------------
void connectWifi() {
  Serial.printf("[wifi] connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 30000) {
    delay(400);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[wifi] connected, ip=%s rssi=%d dBm\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    Serial.println("\n[wifi] FAILED - check SSID/password. Note the ESP32 cannot");
    Serial.println("       use 5 GHz networks; it must be a 2.4 GHz SSID.");
  }
}

void connectMqtt() {
  if (!USE_MQTT) return;
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
  int attempts = 0;
  while (!mqtt.connected() && attempts++ < 5) {
    String clientId = String("aigym-") + DEVICE_ID + "-" + String(random(0xffff), HEX);
    Serial.printf("[mqtt] connecting to %s:%d ... ", MQTT_HOST, MQTT_PORT);
    bool ok = (strlen(MQTT_USER) > 0)
                  ? mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS)
                  : mqtt.connect(clientId.c_str());
    if (ok) {
      Serial.printf("connected. publishing to %s\n", topic);
      return;
    }
    // state() meanings: -4 timeout, -3 connection lost, -2 connect failed,
    // -1 disconnected, 1..5 protocol/auth errors.
    Serial.printf("failed, state=%d. Retrying in 2 s\n", mqtt.state());
    delay(2000);
  }
}

void setupImu() {
  Wire.begin(21, 22);
  if (!mpu.begin()) {
    Serial.println("[imu] Failed to find MPU6050 chip.");
    Serial.println("      Check: 3V3 not 5V, SDA=21, SCL=22, AD0 tied to GND.");
    while (true) delay(1000);
  }
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  // 500 deg/s covers a fast curl (~200 deg/s peak) with headroom. Going to
  // 2000 deg/s would waste resolution on motion a human cannot produce.
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  // 21 Hz on-chip low-pass: above the rep band (<3 Hz), below the sample
  // rate, so it removes machine vibration without smearing the rep itself.
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
  Serial.println("[imu] MPU6050 ready (8g, 500 deg/s, 21 Hz LPF)");
}

// --------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=== AI Gym IMU node ===");
  Serial.printf("[cfg] device_id=%s transport=%s\n", DEVICE_ID,
                USE_MQTT ? "MQTT" : (USE_HTTP ? "HTTP" : "SERIAL ONLY"));

  snprintf(topic, sizeof(topic), "gym/%s/imu", DEVICE_ID);
  setupImu();
  connectWifi();
  connectMqtt();
  batchArr = batchDoc.to<JsonArray>();
}

void publishSample(float t, sensors_event_t &a, sensors_event_t &g) {
  // Gyro is published in DEGREES per second: the server's thresholds are
  // documented in deg/s, and Adafruit hands us rad/s.
  const float RAD2DEG = 57.2957795f;

  if (USE_MQTT) {
    StaticJsonDocument<256> doc;
    doc["device_id"] = DEVICE_ID;
    doc["t"]  = t;
    doc["ax"] = a.acceleration.x;
    doc["ay"] = a.acceleration.y;
    doc["az"] = a.acceleration.z;
    doc["gx"] = g.gyro.x * RAD2DEG;
    doc["gy"] = g.gyro.y * RAD2DEG;
    doc["gz"] = g.gyro.z * RAD2DEG;

    char buf[256];
    size_t n = serializeJson(doc, buf, sizeof(buf));
    if (!mqtt.publish(topic, buf, n)) {
      Serial.println("[mqtt] publish failed");
    }
    return;
  }

  if (USE_HTTP) {
    JsonObject o = batchArr.createNestedObject();
    o["device_id"] = DEVICE_ID;
    o["t"]  = t;
    o["ax"] = a.acceleration.x;
    o["ay"] = a.acceleration.y;
    o["az"] = a.acceleration.z;
    o["gx"] = g.gyro.x * RAD2DEG;
    o["gy"] = g.gyro.y * RAD2DEG;
    o["gz"] = g.gyro.z * RAD2DEG;

    if (batchArr.size() >= BATCH_SIZE) {
      StaticJsonDocument<3200> wrapper;
      wrapper["samples"] = batchArr;
      String body;
      serializeJson(wrapper, body);

      HTTPClient http;
      http.begin(HTTP_URL);
      http.addHeader("Content-Type", "application/json");
      int code = http.POST(body);
      if (code != 200) Serial.printf("[http] POST -> %d\n", code);
      http.end();

      batchDoc.clear();
      batchArr = batchDoc.to<JsonArray>();
    }
    return;
  }

  // No transport configured: print so you can still see the sensor working.
  Serial.printf("t=%.3f ax=%.2f ay=%.2f az=%.2f gx=%.1f gy=%.1f gz=%.1f\n",
                t, a.acceleration.x, a.acceleration.y, a.acceleration.z,
                g.gyro.x * RAD2DEG, g.gyro.y * RAD2DEG, g.gyro.z * RAD2DEG);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (USE_MQTT) {
    if (!mqtt.connected()) connectMqtt();
    mqtt.loop();
  }

  unsigned long now = millis();
  if (now - lastSample < SAMPLE_PERIOD_MS) return;
  lastSample = now;

  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);

  // Seconds since boot. The board has no real-time clock, so this is NOT wall
  // time - and it does not need to be. fusion_engine.estimate_offset recovers
  // the constant offset between this clock and the browser's automatically.
  float t = now / 1000.0f;
  publishSample(t, a, g);
  sampleCount++;

  if (now - lastReport > 5000) {
    lastReport = now;
    Serial.printf("[stat] %lu samples sent, %.1f Hz, wifi=%d mqtt=%d\n",
                  sampleCount, 1000.0f * SAMPLE_HZ / SAMPLE_HZ,
                  WiFi.status() == WL_CONNECTED, mqtt.connected());
  }
}

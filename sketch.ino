#include <WiFi.h>
#include <PubSubClient.h>

// ---------- Pin (Sesuai diagram.json Anda) ----------
const int PIN_BUTTON    = 2;   // D2
const int PIN_BUZZER    = 4;   // D4
const int PIN_LED_RED   = 12;  // D12
const int PIN_LED_GREEN = 13;  // D13

// ---------- WiFi & MQTT ----------
const char* ssid = "Wokwi-GUEST";
const char* password = "";
const char* mqtt_server = "broker.hivemq.com";
const int   mqtt_port = 1883;

// Gunakan topik yang unik agar tidak bentrok di broker publik
const char* topic_alarm = "safeschool/dhanis/alarm";
const char* topic_cmd   = "safeschool/dhanis/command";
const char* BUTTON_LABEL = "DAB";  // Label tombol sesuai area (Dekat Ruang AB)

WiFiClient espClient;
PubSubClient mqtt(espClient);

// ---------- Debounce ----------
const unsigned long DEBOUNCE_MS = 30;
int lastButtonReading = HIGH;
int stableButtonState = HIGH;
unsigned long lastDebounceTime = 0;

// ---------- Alarm ----------
bool alarmActive = false;
unsigned long alarmStartTime = 0;

// ---------- Timing ----------
unsigned long lastBuzzerToggle = 0;
bool buzzerState = false;
unsigned long lastLedToggle = 0;
bool ledState = false;

// ============================================================
void setup_wifi() {
  delay(10);
  Serial.print("Connecting WiFi...");
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected");
}

void mqtt_callback(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  Serial.printf("[MQTT] Received on %s: %s\n", topic, msg.c_str());

  // Jika menerima perintah "OFF" dari dashboard Python
  if (String(topic) == topic_cmd && msg == "OFF") {
    if (alarmActive) {
      stopAlarm();
      Serial.println("[MQTT] Remote OFF received, alarm stopped.");
    }
  }
}

void reconnect_mqtt() {
  while (!mqtt.connected()) {
    Serial.print("MQTT connecting...");
    String clientId = "SafeSchool-" + String(random(0xffff), HEX);
    if (mqtt.connect(clientId.c_str())) {
      Serial.println("connected");
      mqtt.subscribe(topic_cmd);
    } else {
      Serial.printf("failed rc=%d, retry in 3s\n", mqtt.state());
      delay(3000);
    }
  }
}

// ============================================================
void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_LED_GREEN, OUTPUT);

  noTone(PIN_BUZZER);
  digitalWrite(PIN_LED_RED, LOW);
  digitalWrite(PIN_LED_GREEN, HIGH);

  setup_wifi();
  mqtt.setServer(mqtt_server, mqtt_port);
  mqtt.setCallback(mqtt_callback);

  Serial.println("[BOOT] SafeSchool ready.");
}

// ============================================================
void loop() {
  if (!mqtt.connected()) reconnect_mqtt();
  mqtt.loop();

  // 1. Debounce 按钮
  int reading = digitalRead(PIN_BUTTON);
  if (reading != lastButtonReading) lastDebounceTime = millis();

  if ((millis() - lastDebounceTime) > DEBOUNCE_MS) {
    if (reading != stableButtonState) {
      stableButtonState = reading;
      if (stableButtonState == LOW && !alarmActive) {
        triggerAlarm();
      }
    }
  }
  lastButtonReading = reading;

  // 2. Output kontrol (buzzer & LED)
  if (alarmActive) {
    if (millis() - lastBuzzerToggle > 200) {
      buzzerState = !buzzerState;
      if (buzzerState) tone(PIN_BUZZER, 1000);
      else noTone(PIN_BUZZER);
      lastBuzzerToggle = millis();
    }
    if (millis() - lastLedToggle > 100) {
      ledState = !ledState;
      digitalWrite(PIN_LED_RED, ledState ? HIGH : LOW);
      lastLedToggle = millis();
    }
    digitalWrite(PIN_LED_GREEN, LOW);
  } else {
    noTone(PIN_BUZZER);
    if (millis() - lastLedToggle > 1000) {
      ledState = !ledState;
      digitalWrite(PIN_LED_GREEN, ledState ? HIGH : LOW);
      lastLedToggle = millis();
    }
    digitalWrite(PIN_LED_RED, LOW);
  }
}

// ============================================================
void triggerAlarm() {
  alarmActive = true;
  lastBuzzerToggle = 0;
  lastLedToggle = 0;
  buzzerState = false;
  ledState = false;

  Serial.println("[BTN] Emergency button pressed!");
  
  // Kirim JSON ke MQTT
  String payload = "{\"button\":\"" + String(BUTTON_LABEL) + "\",\"time\":\"" + String(millis()) + "\"}";
  mqtt.publish(topic_alarm, payload.c_str());
  Serial.printf("[MQTT] Published alarm: %s\n", payload.c_str());
}

void stopAlarm() {
  alarmActive = false;
  noTone(PIN_BUZZER);
  digitalWrite(PIN_LED_RED, LOW);
  digitalWrite(PIN_LED_GREEN, HIGH);
  Serial.println("[ALARM] Stopped by remote, back to NORMAL.");
}
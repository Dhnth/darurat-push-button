# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "flask>=3.0",
#     "paho-mqtt>=2.1",
# ]
# ///
#
# Jalankan dengan: uv run dashboard.py

import paho.mqtt.client as mqtt
from flask import Flask, render_template_string, jsonify, redirect, url_for
import json
import datetime
import threading
import os
import tempfile

app = Flask(__name__)

# ============ KONFIGURASI MQTT ============
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
TOPIC_ALARM = "safeschool/dhanis/alarm"      
TOPIC_CMD   = "safeschool/dhanis/command"    

# ============ PEMETAAN TOMBOL ============
LOKASI_TOMBOL = {
    "DAB": "Ruang AB",
}

# ============ STATE ============
current_alarm = {
    "active": False,
    "button": None,
    "timestamp": None,
    "location_text": None,
    "muted": False
}

# ============ RIWAYAT KEJADIAN ============
# Disimpan di memori: entri terbaru ada di index 0, maksimal MAX_INCIDENTS entri.
MAX_INCIDENTS = 50
incidents = []
incident_counter = 0
revision = 0          # naik setiap ada perubahan, dipakai untuk auto-refresh dashboard
state_lock = threading.RLock()


def _time_label(dt):
    """Label tanggal ramah dibaca: Hari ini / Kemarin / 27 Okt 2026."""
    today = datetime.date.today()
    if dt.date() == today:
        return "Hari ini"
    if dt.date() == today - datetime.timedelta(days=1):
        return "Kemarin"
    return dt.strftime("%d %b %Y")


def _refresh_banner():
    """Sinkronkan banner atas dengan kejadian terbaru yang masih menunggu tindakan."""
    pending = [i for i in incidents if i["status"] == "pending"]
    if pending:
        inc = pending[0]
        current_alarm["active"] = True
        current_alarm["button"] = inc["button"]
        current_alarm["location_text"] = inc["location"]
        current_alarm["timestamp"] = inc["time_str"]
        current_alarm["muted"] = any(i["muted"] for i in pending)
    else:
        current_alarm["active"] = False
        current_alarm["button"] = None
        current_alarm["location_text"] = None
        current_alarm["timestamp"] = None
        current_alarm["muted"] = False


def _publish_cmd(payload, note):
    try:
        mqtt_client.publish(TOPIC_CMD, payload)
        print(f"\n[MQTT] Perintah {payload} dikirim. {note}")
    except Exception as e:
        print(f"[MQTT] Gagal mengirim perintah {payload}: {e}")


# ============ PERSISTENSI RIWAYAT (JSON) ============
DATA_FILE = os.environ.get(
    "SAFESCHOOL_HISTORY_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "incident_history.json"),
)


def _load_history():
    """Muat riwayat dari file JSON saat server start."""
    global incidents, incident_counter, revision
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            raise ValueError("format file harus berupa list")
    except FileNotFoundError:
        return
    except Exception as e:
        print(f"[RIWAYAT] Gagal membaca {DATA_FILE}: {e}")
        return

    loaded = []
    for item in raw:
        try:
            at = datetime.datetime.fromisoformat(item["at"])
        except Exception:
            continue
        loaded.append({
            "id": int(item.get("id", 0)),
            "at": at,
            "time_str": item.get("time_str", at.strftime("%H:%M:%S")),
            "date_label": _time_label(at),
            "button": item.get("button", "UNKNOWN"),
            "location": item.get("location", "Area Tidak Diketahui"),
            "status": "resolved" if item.get("status") == "resolved" else "pending",
            "muted": bool(item.get("muted", False)),
            "resolved_at": item.get("resolved_at"),
            "resolved_label": item.get("resolved_label"),
        })

    loaded.sort(key=lambda i: i["at"], reverse=True)
    with state_lock:
        incidents[:] = loaded[:MAX_INCIDENTS]
        incident_counter = max((i["id"] for i in incidents), default=0)
        _refresh_banner()
        revision += 1
    print(f"[RIWAYAT] {len(incidents)} kejadian dimuat dari {DATA_FILE}")


def _save_history():
    """Simpan riwayat ke file JSON (tulis ke file sementara lalu rename, biar tidak korup)."""
    tmp_path = None
    try:
        with state_lock:
            payload = [
                {**inc, "at": inc["at"].isoformat(timespec="seconds")}
                for inc in incidents
            ]
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=os.path.dirname(DATA_FILE) or ".",
            prefix=".incident_history-", suffix=".tmp", delete=False,
        ) as f:
            tmp_path = f.name
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, DATA_FILE)
    except Exception as e:
        print(f"[RIWAYAT] Gagal menyimpan riwayat: {e}")
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"Terhubung ke MQTT Broker (Status: {reason_code})")
    client.subscribe(TOPIC_ALARM)

def on_message(client, userdata, msg):
    global incident_counter, revision
    try:
        data = json.loads(msg.payload.decode())
        btn = data.get("button", "UNKNOWN")
        loc = LOKASI_TOMBOL.get(btn, "Area Tidak Diketahui")
        now = datetime.datetime.now()

        with state_lock:
            incident_counter += 1
            incidents.insert(0, {
                "id": incident_counter,
                "at": now,
                "time_str": now.strftime("%H:%M:%S"),
                "date_label": _time_label(now),
                "button": btn,
                "location": loc,
                "status": "pending",
                "muted": False,
                "resolved_at": None,
                "resolved_label": None,
            })
            del incidents[MAX_INCIDENTS:]
            _refresh_banner()
            revision += 1

        _save_history()
        print(f"\n[ALARM] Tombol {btn} ditekan. Lokasi: {loc}")
    except Exception as e:
        print(f"Error parsing message: {e}")

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

# Muat riwayat kejadian dari penyimpanan lokal sebelum server jalan.
_load_history()

# Koneksi MQTT tidak dibuat fatal: dashboard tetap bisa dibuka meski broker/network down.
try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()
except Exception as e:
    print(f"[MQTT] Gagal terhubung ke {MQTT_BROKER}:{MQTT_PORT} -> {e}")
    print("[MQTT] Dashboard tetap berjalan tanpa koneksi MQTT.")

# ============ HTML DASHBOARD ============
HTML = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>SafeSchool AIoT - Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" rel="stylesheet"/>
<script src="https://cdn.tailwindcss.com"></script>
<script id="tailwind-config">
tailwind.config = {
  darkMode: "class",
  theme: {
    extend: {
      "colors": {
        "surface": "#faf8ff", "on-surface": "#131b2e", "primary": "#0041a7",
        "on-primary": "#ffffff", "secondary": "#ba0035", "on-secondary": "#ffffff",
        "surface-container-lowest": "#ffffff", "surface-container-low": "#f2f3ff",
        "surface-container": "#eaedff", "surface-container-high": "#e2e7ff",
        "surface-container-highest": "#dae2fd", "on-surface-variant": "#424654",
        "outline": "#737686", "outline-variant": "#c3c6d7", "error-container": "#ffdad6",
        "on-error-container": "#93000a", "primary-fixed": "#dae2ff", "primary-container": "#0057d9"
      },
      "fontFamily": {
        "headline-lg": ["Space Grotesk"], "headline-md": ["Space Grotesk"], "headline-sm": ["Space Grotesk"],
        "body-md": ["Inter"], "body-sm": ["Inter"], "label-md": ["Inter"], "label-lg": ["Inter"],
        "code-telemetry": ["JetBrains Mono"], "code-badge": ["JetBrains Mono"]
      },
      "fontSize": {
        "headline-lg": ["28px", { "lineHeight": "36px", "fontWeight": "600" }],
        "headline-md": ["22px", { "lineHeight": "28px", "fontWeight": "600" }],
        "headline-sm": ["18px", { "lineHeight": "24px", "fontWeight": "600" }],
        "body-md": ["14px", { "lineHeight": "20px", "fontWeight": "400" }],
        "body-sm": ["12px", { "lineHeight": "16px", "fontWeight": "400" }],
        "label-md": ["12px", { "lineHeight": "16px", "fontWeight": "500" }],
        "label-lg": ["14px", { "lineHeight": "20px", "fontWeight": "600" }],
        "code-telemetry": ["13px", { "lineHeight": "18px", "fontWeight": "500" }],
        "code-badge": ["11px", { "lineHeight": "14px", "fontWeight": "600" }]
      },
      "boxShadow": {
        "soft": "0 4px 20px rgba(19, 27, 46, 0.06)",
        "glow": "0 0 0 4px rgba(186, 0, 53, 0.12)"
      }
    }
  }
}
</script>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
  html,body{margin:0;padding:0;}
  body{overscroll-behavior:none;background: radial-gradient(circle at 1px 1px, #e2e7ff 1px, transparent 0); background-size: 24px 24px;}
  ::-webkit-scrollbar{width:8px;height:8px;}
  ::-webkit-scrollbar-thumb{background:#c3c6d7;border-radius:8px;}
  .pulse-dot{animation:pulse 1.4s ease-in-out infinite;}
  @keyframes pulse{0%,100%{opacity:1;transform:scale(1);}50%{opacity:.4;transform:scale(.85);}}
  .glow-danger{animation:glow 1.8s ease-in-out infinite;}
  @keyframes glow{0%,100%{box-shadow:0 0 0 0 rgba(186,0,53,0.35);}50%{box-shadow:0 0 0 10px rgba(186,0,53,0);}}
  .fade-in{animation:fadeIn .4s ease-out;}
  @keyframes fadeIn{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:translateY(0);}}
</style>
</head>
<body class="font-body-md text-body-md text-on-surface">

<!-- SIDEBAR -->
<aside class="fixed left-0 top-0 h-full w-72 bg-surface-container-lowest shadow-soft z-50 flex flex-col justify-between py-5">
    <div class="flex flex-col gap-5">
        <div class="px-5 flex flex-col gap-3">
            <div class="flex items-center gap-3">
                <div class="w-11 h-11 rounded-xl bg-primary flex items-center justify-center shadow-md">
                    <span class="material-symbols-outlined text-on-primary text-[24px]">shield</span>
                </div>
                <div class="flex flex-col">
                    <span class="font-headline-sm text-headline-sm text-primary leading-tight tracking-tight">SafeSchool</span>
                    <span class="font-code-badge text-code-badge text-on-surface-variant">AIoT Platform</span>
                </div>
            </div>
            <div class="bg-gradient-to-br from-surface-container-low to-surface-container p-3 rounded-xl mt-1 border border-surface-container-high">
                <span class="font-label-md text-label-md text-on-surface-variant block">SMK Negeri 1 Banjar</span>
                <span class="font-code-badge text-code-badge text-outline mt-0.5 block">Tim 400 Bad Habit</span>
            </div>
        </div>
        <nav class="flex flex-col gap-1 px-4">
            <a class="flex items-center gap-3 px-4 py-2.5 transition-all bg-primary text-on-primary font-label-lg rounded-xl shadow-md" href="#pantauan">
                <span class="material-symbols-outlined text-[20px]">radar</span>
                <span class="font-label-lg text-label-lg">Pantauan Langsung</span>
            </a>
            <a class="flex items-center gap-3 px-4 py-2.5 rounded-xl text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-all" href="#riwayat">
                <span class="material-symbols-outlined text-[20px]">history_toggle_off</span>
                <span class="font-label-lg text-label-lg">Riwayat Kejadian</span>
                {% if pending_count %}
                <span class="ml-auto font-code-badge text-code-badge bg-secondary text-on-secondary px-2 py-0.5 rounded-md font-bold">{{ pending_count }}</span>
                {% endif %}
            </a>
        </nav>
    </div>
    
    <div class="px-4">
        <div class="p-3 bg-surface-container rounded-xl flex flex-col gap-2 border border-surface-container-high">
            <div class="flex items-center justify-between">
                <span class="font-label-md text-label-md text-on-surface-variant">Status Sirine</span>
                <span class="font-code-badge text-code-badge {% if active and not muted %}text-secondary bg-error-container{% else %}text-primary bg-primary-fixed{% endif %} px-2 py-0.5 rounded-md font-bold">
                    {{ 'AKTIF' if active and not muted else ('DIBUNGKAM' if muted else 'SIAGA') }}
                </span>
            </div>
            <form action="/ack" method="POST">
                <button class="w-full {% if active %}bg-secondary text-on-secondary hover:opacity-90{% else %}bg-surface-container-highest text-on-surface-variant cursor-not-allowed{% endif %} py-2.5 rounded-lg text-center font-label-md text-label-md transition-all flex items-center justify-center gap-2 font-semibold" type="submit" {{ 'disabled' if not active }}>
                    <span class="material-symbols-outlined text-[18px]">volume_off</span>
                    <span>Matikan Sirine</span>
                </button>
            </form>
        </div>
    </div>
</aside>

<!-- MAIN CONTENT -->
<div class="pl-72">
    <header class="fixed top-0 left-72 right-0 h-16 bg-surface/80 backdrop-blur-xl border-b border-surface-container-high z-40 flex items-center justify-between px-8">
        <div class="flex items-center gap-2">
            <span class="font-headline-sm text-headline-sm text-on-surface">Dashboard Pemantauan</span>
        </div>
        <div class="flex items-center gap-3">
            <div class="hidden md:flex flex-col text-right">
                <span class="font-label-lg text-label-lg text-on-surface font-semibold">Petugas Keamanan</span>
                <span class="font-label-md text-label-md text-on-surface-variant">Satpam / BK</span>
            </div>
            <div class="w-9 h-9 rounded-full bg-primary flex items-center justify-center">
                <span class="material-symbols-outlined text-on-primary text-[18px]">person</span>
            </div>
        </div>
    </header>

    <main class="w-full pt-16 px-8">
        <div class="flex flex-col w-full gap-6 pb-10 pt-6 max-w-[1400px] mx-auto">

            <!-- STATUS BANNER -->
            <section id="pantauan" class="relative overflow-hidden bg-surface-container-lowest rounded-2xl shadow-soft border border-surface-container-high fade-in">
                <div class="p-7 flex flex-col xl:flex-row items-start xl:items-center justify-between gap-6">
                    
                    <div class="flex items-start gap-5 max-w-3xl">
                        <div class="w-16 h-16 rounded-2xl {% if active and not muted %}bg-error-container text-on-error-container glow-danger{% elif active and muted %}bg-surface-container-high text-outline{% else %}bg-primary-fixed text-primary{% endif %} flex items-center justify-center shrink-0">
                            <span class="material-symbols-outlined text-[34px]">
                                {{ 'crisis_alert' if active and not muted else ('volume_off' if active and muted else 'verified_user') }}
                            </span>
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <div class="flex flex-wrap items-center gap-2 mb-1">
                                <span class="font-code-badge text-code-badge {% if active %}bg-secondary text-on-secondary{% else %}bg-primary text-on-primary{% endif %} px-2.5 py-1 rounded-md font-bold uppercase tracking-wide">
                                    {{ 'Darurat' if active else 'Kondisi Aman' }}
                                </span>
                            </div>
                            
                            <h1 class="font-headline-lg text-headline-lg {% if active %}text-secondary{% else %}text-primary{% endif %} tracking-tight">
                                {% if active %}
                                    Tanda darurat terdeteksi di {{ location_text }}
                                {% else %}
                                    Seluruh area dalam kondisi aman
                                {% endif %}
                            </h1>
                            
                            <p class="font-body-md text-body-md text-on-surface-variant max-w-xl">
                                {% if active %}
                                    Terjadi pemicuan tombol darurat <strong class="text-on-surface">{{ button }}</strong> pada pukul <strong class="text-on-surface">{{ timestamp }} WIB</strong>. Sirine saat ini {% if muted %}<strong class="text-on-surface">dibungkam sementara</strong>{% else %}<strong class="text-secondary">berbunyi</strong>{% endif %}. Mohon segera kirim petugas ke lokasi.
                                {% else %}
                                    Sistem memantau seluruh tombol darurat secara berkelanjutan. Tidak ada indikasi bahaya saat ini.
                                {% endif %}
                            </p>
                        </div>
                    </div>

                    <!-- ACTION CONTROLS -->
                    <div class="flex flex-col gap-2.5 w-full xl:w-auto shrink-0 xl:min-w-[260px]">
                        <form action="/ack" method="POST">
                            <button class="w-full flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl font-label-lg text-label-lg font-bold 
                                {% if active %}bg-secondary text-on-secondary shadow-md hover:opacity-90{% else %}bg-surface-container-highest text-on-surface-variant cursor-not-allowed{% endif %} 
                                active:scale-[0.98] transition-all" type="submit" {{ 'disabled' if not active }}>
                                <span class="material-symbols-outlined text-[22px]">notifications_off</span>
                                <span>Selesaikan Alarm</span>
                            </button>
                        </form>
                        <form action="/mute" method="POST">
                            <button class="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl font-label-md text-label-md bg-surface-container-high text-on-surface hover:bg-surface-container-highest transition-colors font-semibold" type="submit" {{ 'disabled' if not active or muted }}>
                                <span class="material-symbols-outlined text-[18px]">volume_mute</span>
                                <span>Bungkam Sementara</span>
                            </button>
                        </form>
                    </div>
                </div>
            </section>

            <!-- QUICK METRICS -->
            <section class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="bg-surface-container-lowest p-5 rounded-2xl shadow-soft border border-surface-container-high flex flex-col gap-3">
                    <div class="flex items-center justify-between">
                        <span class="font-label-md text-label-md text-on-surface-variant font-semibold uppercase tracking-wide">Zona Terpantau</span>
                        <div class="w-8 h-8 rounded-lg bg-primary-fixed flex items-center justify-center">
                            <span class="material-symbols-outlined text-primary text-[18px]">map</span>
                        </div>
                    </div>
                    <div class="flex items-baseline gap-2">
                        <span class="font-headline-lg text-headline-lg text-on-surface font-bold">1</span>
                        <span class="font-body-sm text-body-sm text-on-surface-variant">titik tombol darurat</span>
                    </div>
                    <div class="flex flex-wrap gap-1.5 pt-1">
                        <span class="font-code-badge text-code-badge bg-surface-container px-2 py-1 rounded-md text-on-surface-variant">Ruang AB</span>
                    </div>
                </div>

                <div class="bg-surface-container-lowest p-5 rounded-2xl shadow-soft border border-surface-container-high flex flex-col gap-3">
                    <div class="flex items-center justify-between">
                        <span class="font-label-md text-label-md text-on-surface-variant font-semibold uppercase tracking-wide">Status Alarm</span>
                        <div class="w-8 h-8 rounded-lg {% if active %}bg-error-container{% else %}bg-primary-fixed{% endif %} flex items-center justify-center">
                            <span class="material-symbols-outlined {% if active %}text-secondary{% else %}text-primary{% endif %} text-[18px]">{{ 'warning' if active else 'check_circle' }}</span>
                        </div>
                    </div>
                    <div class="flex items-baseline gap-2">
                        <span class="font-headline-lg text-headline-lg {% if active %}text-secondary{% else %}text-on-surface{% endif %} font-bold">
                            {{ 'Aktif' if active else 'Siaga' }}
                        </span>
                    </div>
                    <div class="font-body-sm text-body-sm text-on-surface-variant pt-1">
                        {% if active %}Sirine {% if muted %}dibungkam{% else %}berbunyi{% endif %} — menunggu tindakan petugas.{% else %}Sistem siap mendeteksi pemicuan darurat.{% endif %}
                    </div>
                </div>

                <div class="bg-surface-container-lowest p-5 rounded-2xl shadow-soft border border-surface-container-high flex flex-col gap-3">
                    <div class="flex items-center justify-between">
                        <span class="font-label-md text-label-md text-on-surface-variant font-semibold uppercase tracking-wide">Kejadian Hari Ini</span>
                        <div class="w-8 h-8 rounded-lg {% if pending_count %}bg-error-container{% else %}bg-surface-container-high{% endif %} flex items-center justify-center">
                            <span class="material-symbols-outlined {% if pending_count %}text-secondary{% else %}text-on-surface-variant{% endif %} text-[18px]">history</span>
                        </div>
                    </div>
                    <div class="flex items-baseline gap-2">
                        <span class="font-headline-lg text-headline-lg text-on-surface font-bold">{{ today_count }}</span>
                        <span class="font-body-sm text-body-sm text-on-surface-variant">kejadian tercatat</span>
                    </div>
                    <div class="font-body-sm text-body-sm text-on-surface-variant pt-1">
                        {% if pending_count %}{{ pending_count }} kejadian belum ditindaklanjuti petugas.{% elif today_count %}Semua kejadian hari ini sudah ditangani.{% else %}Tidak ada kejadian darurat hari ini.{% endif %}
                    </div>
                </div>
            </section>

            <!-- LOG KEJADIAN -->
            <section id="riwayat" class="bg-surface-container-lowest p-6 rounded-2xl shadow-soft border border-surface-container-high flex flex-col gap-4">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-3 pb-1">
                    <div>
                        <span class="font-code-badge text-code-badge text-primary uppercase font-bold tracking-wider">Riwayat Kejadian</span>
                        <h2 class="font-headline-md text-headline-md text-on-surface mt-0.5">Log Kejadian &amp; Tindak Lanjut</h2>
                    </div>
                    <div class="flex flex-wrap items-center gap-2">
                        <span class="inline-flex items-center gap-1.5 font-code-badge text-code-badge bg-surface-container px-2.5 py-1 rounded-md text-on-surface-variant font-bold">
                            Total {{ incidents|length }}
                        </span>
                        <span class="inline-flex items-center gap-1.5 font-code-badge text-code-badge {% if pending_count %}bg-secondary text-on-secondary{% else %}bg-surface-container text-on-surface-variant{% endif %} px-2.5 py-1 rounded-md font-bold">
                            Menunggu {{ pending_count }}
                        </span>
                        <span class="inline-flex items-center gap-1.5 font-code-badge text-code-badge bg-primary-fixed text-primary px-2.5 py-1 rounded-md font-bold">
                            Ditangani {{ handled_count }}
                        </span>
                    </div>
                </div>
                
                <div class="overflow-x-auto w-full">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="text-on-surface-variant font-label-md text-label-md">
                                <th class="p-3 font-semibold">Waktu</th>
                                <th class="p-3 font-semibold">Lokasi</th>
                                <th class="p-3 font-semibold">Pemicu</th>
                                <th class="p-3 font-semibold">Status</th>
                                <th class="p-3 text-right font-semibold">Tindakan</th>
                            </tr>
                        </thead>
                        <tbody class="font-body-md text-body-md" id="incidentLogTableBody">
                            {% for inc in incidents %}
                            <tr class="{% if inc.status == 'pending' %}bg-error-container/30 hover:bg-error-container/40{% else %}hover:bg-surface-container-low{% endif %} transition-colors border-t border-surface-container-high fade-in">
                                <td class="p-3 font-code-telemetry text-code-telemetry {% if inc.status == 'pending' %}text-secondary{% else %}text-on-surface-variant{% endif %} font-bold whitespace-nowrap">
                                    {{ inc.time_str }}
                                    <span class="block text-code-badge font-normal text-on-surface-variant">{{ inc.date_label }}</span>
                                </td>
                                <td class="p-3">
                                    <span class="font-label-lg text-label-lg font-semibold text-on-surface block">{{ inc.location }}</span>
                                    <span class="font-code-badge text-code-badge text-outline">#{{ inc.id }} · {{ inc.button }}</span>
                                </td>
                                <td class="p-3">
                                    <span class="inline-flex items-center gap-1.5 font-code-badge text-code-badge {% if inc.status == 'pending' %}bg-secondary text-on-secondary{% else %}bg-surface-container-high text-on-surface-variant{% endif %} px-2.5 py-1 rounded-md font-bold">
                                        <span class="material-symbols-outlined text-[14px]">touch_app</span> Tombol Fisik
                                    </span>
                                </td>
                                <td class="p-3">
                                    {% if inc.status == 'pending' %}
                                    <span class="inline-flex items-center gap-1.5 font-label-md text-label-md bg-secondary text-on-secondary px-2.5 py-1 rounded-full font-semibold">
                                        <span class="w-1.5 h-1.5 rounded-full bg-surface-container-lowest pulse-dot"></span> Menunggu Petugas
                                    </span>
                                    {% if inc.muted %}
                                    <span class="flex items-center gap-1 mt-1.5 font-code-badge text-code-badge text-outline">
                                        <span class="material-symbols-outlined text-[14px]">volume_off</span> Sirine dibungkam
                                    </span>
                                    {% endif %}
                                    {% else %}
                                    <span class="inline-flex items-center gap-1.5 font-label-md text-label-md bg-primary-fixed text-primary px-2.5 py-1 rounded-full font-semibold">
                                        <span class="material-symbols-outlined text-[14px]">task_alt</span> Ditangani
                                    </span>
                                    <span class="block mt-1.5 font-code-badge text-code-badge text-outline">
                                        {{ inc.resolved_at }} · {{ inc.resolved_label }}
                                    </span>
                                    {% endif %}
                                </td>
                                <td class="p-3 text-right whitespace-nowrap">
                                    {% if inc.status == 'pending' %}
                                    <form action="/ack/{{ inc.id }}" method="POST" class="inline">
                                        <button class="px-3.5 py-1.5 rounded-lg bg-primary text-on-primary hover:bg-primary-container font-label-md text-label-md font-semibold transition-colors" type="submit">
                                            Tandai Ditangani
                                        </button>
                                    </form>
                                    {% else %}
                                    <span class="font-body-sm text-body-sm text-outline">Selesai</span>
                                    {% endif %}
                                </td>
                            </tr>
                            {% else %}
                            <tr>
                                <td colspan="5" class="p-10 text-center bg-surface-container-low rounded-xl">
                                    <div class="flex flex-col items-center gap-2">
                                        <div class="w-12 h-12 rounded-full bg-surface-container flex items-center justify-center">
                                            <span class="material-symbols-outlined text-outline text-[24px]">verified</span>
                                        </div>
                                        <span class="font-label-lg text-label-lg text-on-surface-variant">Belum ada kejadian tercatat</span>
                                        <span class="font-body-sm text-body-sm text-outline">Sistem berjalan normal dan memantau seluruh zona.</span>
                                    </div>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </section>
        </div>
    </main>
</div>

<script>
    // Simpan status saat ini di JavaScript
    let currentStatus = "{{ 'active' if active else 'safe' }}";
    let currentRevision = {{ revision }};

    // Fungsi untuk mengecek status ke backend secara diam-diam (tanpa reload halaman)
    function checkStatus() {
        fetch('/status')
            .then(response => response.json())
            .then(data => {
                const newStatus = data.active ? "active" : "safe";

                // Reload kalau status alarm berubah ATAU riwayat kejadian bertambah/berubah
                if (newStatus !== currentStatus || data.revision !== currentRevision) {
                    location.reload();
                }
            })
            .catch(err => console.log("Menunggu koneksi..."));
    }

    // Cek status setiap 2 detik di latar belakang
    setInterval(checkStatus, 2000);
</script>

</body>
</html>
"""

@app.route("/")
def index():
    today = datetime.date.today()
    with state_lock:
        rows = [dict(i) for i in incidents]
        active = current_alarm["active"]
        button = current_alarm["button"]
        location_text = current_alarm["location_text"]
        timestamp = current_alarm["timestamp"]
        muted = current_alarm["muted"]
        rev = revision

    today_count = sum(1 for i in rows if i["at"].date() == today)
    pending_count = sum(1 for i in rows if i["status"] == "pending")
    handled_count = len(rows) - pending_count

    return render_template_string(HTML,
        active=active,
        button=button,
        location_text=location_text,
        timestamp=timestamp,
        muted=muted,
        incidents=rows,
        today_count=today_count,
        pending_count=pending_count,
        handled_count=handled_count,
        revision=rev)

# ============ ENDPOINT BARU UNTUK CEK STATUS ============
@app.route("/status")
def get_status():
    # active + revision dipakai JavaScript untuk tahu kapan harus reload.
    with state_lock:
        return jsonify({
            "active": current_alarm["active"],
            "revision": revision,
            "pending": sum(1 for i in incidents if i["status"] == "pending"),
            "total": len(incidents),
        })

@app.route("/ack", methods=["POST"])
def acknowledge():
    """Selesaikan semua alarm yang masih menunggu tindakan."""
    global revision
    now = datetime.datetime.now()
    with state_lock:
        changed = False
        for inc in incidents:
            if inc["status"] == "pending":
                inc["status"] = "resolved"
                inc["resolved_at"] = now.strftime("%H:%M:%S")
                inc["resolved_label"] = _time_label(now)
                changed = True
        _refresh_banner()
        if changed:
            revision += 1

    _publish_cmd("OFF", "Alarm dimatikan.")
    _save_history()
    return redirect(url_for('index'))

@app.route("/ack/<int:incident_id>", methods=["POST"])
def acknowledge_one(incident_id):
    """Tandai satu kejadian tertentu sebagai sudah ditangani."""
    global revision
    now = datetime.datetime.now()
    with state_lock:
        target = next((i for i in incidents if i["id"] == incident_id), None)
        handled_now = bool(target and target["status"] == "pending")
        if handled_now:
            target["status"] = "resolved"
            target["resolved_at"] = now.strftime("%H:%M:%S")
            target["resolved_label"] = _time_label(now)
            _refresh_banner()
            revision += 1

        no_more_pending = not current_alarm["active"]

    # Sirine baru dimatikan kalau sudah tidak ada alarm yang menunggu.
    if handled_now and no_more_pending:
        _publish_cmd("OFF", "Semua alarm selesai, sirine dimatikan.")

    if handled_now:
        _save_history()
    return redirect(url_for('index'))

@app.route("/mute", methods=["POST"])
def mute_alarm():
    global revision
    with state_lock:
        pending = [i for i in incidents if i["status"] == "pending"]
        if pending:
            for inc in pending:
                inc["muted"] = True
            _refresh_banner()
            revision += 1

    if pending:
        _publish_cmd("MUTE", "Sirine dibungkam sementara.")
        _save_history()
    return redirect(url_for('index'))

if __name__ == "__main__":
    print("=========================================")
    print(" Dashboard berjalan di: http://localhost:5000")
    print(f" Riwayat kejadian   : {DATA_FILE}")
    print("=========================================")
    # use_reloader=False: tanpa ini Flask menjalankan 2 proses yang dua-duanya
    # subscribe MQTT, sehingga 1 alarm tercatat dobel.
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
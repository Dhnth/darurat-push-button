# SafeSchool AIoT - Pusat Kendali Dashboard

Sistem peringatan dini indikasi darurat berbasis IoT untuk lingkungan sekolah. Dashboard ini berfungsi sebagai pusat pemantauan bagi petugas keamanan (Satpam/BK) untuk menerima sinyal darurat secara *real-time*, mematikan sirine jarak jauh, dan mencatat riwayat kejadian.

Proyek ini dikembangkan oleh **Tim 400 Bad Habit** (SMK Negeri 1 Banjar) untuk Samsung Innovation Campus Batch 8.

## ✨ Fitur Utama

- **Pemantauan Real-time:** Menerima sinyal darurat dari tombol fisik (ESP32) via protokol MQTT (HiveMQ).
- **Kontrol Jarak Jauh:** Mematikan sirine/buzzer dan mereset status alarm langsung dari web tanpa perlu mendatangi lokasi tombol.
- **Mode Mute Sementara:** Membungkam sirine jika dirasa terlalu berisik, namun status darurat tetap terlihat di layar.
- **Log Riwayat Kejadian:** Mencatat setiap pemicuan tombol, lengkap dengan waktu, lokasi, dan status penyelesaian (Selesai/Menunggu).
- **Penyimpanan Lokal (Persisten):** Riwayat kejadian disimpan dalam file `incident_history.json`. Data tidak hilang meskipun server Python dimatikan atau di-restart.
- **Auto-Refresh Cerdas (Anti-Flicker):** Halaman web akan memperbarui diri secara otomatis *hanya jika* ada perubahan status, tanpa membuat layar berkedip (menggunakan Fetch API).

## 🛠️ Prasyarat (Requirements)

Pastikan sistem Anda (CachyOS/Arch Linux) sudah terinstall:
1. **Python 3.9+**
2. **UV** (Sangat direkomendasikan) atau `pip` biasa.

## 🚀 Cara Menjalankan

### Opsi 1: Menggunakan `uv` (Sangat Cepat)
Karena skrip ini sudah dilengkapi dengan komentar PEP 723 (inline script metadata), Anda **tidak perlu** membuat virtual environment atau menginstall library secara manual. Cukup jalankan:

```bash
uv run dashboard.py
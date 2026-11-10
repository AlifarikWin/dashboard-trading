# Dashboard Trading BEI

*Dashboard* web untuk dua masalah utama trader ritel: **masuk di waktu yang salah** dan **rugi yang dibiarkan membesar**. Dirancang untuk HP dan alur *auto order* Stockbit. Setiap kartu saham punya tombol **📈 Chart** yang membuka *chart* TradingView.

> Tidak ada indikator yang bisa memastikan saham akan naik. Informasi "sebelum naik" dari orang dalam adalah *insider trading* dan ilegal. *Dashboard* ini menyaring peluang yang lebih baik secara statistik dan membatasi rugi. Ini bukan rekomendasi investasi.

## Halaman utama: Daftar Pagi Day Trade

Dirancang untuk *day trader* yang bekerja 08.00–17.00. Semua keputusan dibuat **sebelum pasar buka**, dan eksekusinya lewat Auto Order Stockbit.

**Sumber data:** ringkasan perdagangan harian resmi BEI (`GetStockSummary`) untuk **seluruh saham**, termasuk *foreign buy/sell*. Data hari ini terbit setelah pasar tutup. Sekitar 90 hari bursa terakhir disimpan di `cache/idx/`, sehingga setelah unduhan pertama hanya hari baru yang diambil.

**Skor 0–100 per saham** (dihitung dari hari bursa terakhir):

| Komponen | Poin |
|---|---|
| Asing *net buy* di hari terakhir | 10 |
| Asing *net buy* ≥ 3 hari berturut-turut | 15 |
| Akumulasi asing 5 hari ≥ 2% nilai transaksi | 10 |
| Ditutup dekat harga tertinggi hari itu (≥ 75% *range*) | 15 |
| Naik, tetapi ≤ 7% (belum terlalu tinggi) | 10 |
| Volume ≥ 1,5× rata-rata 20 hari | 15 |
| Di atas MA20 dan MA20 > MA50 | 15 |
| OBV 10 hari naik | 10 |

**Filter wajib:** harga ≥ Rp150, rata-rata nilai transaksi 20 hari ≥ Rp10 miliar, kenaikan < 15%, dan di atas MA20. Daftar menampilkan maksimal 5 saham dengan skor ≥ 60.

**Rencana per saham:**
- **Beli hanya jika harga menembus *high* kemarin** (Auto Buy, *trigger* = *high* + 1 *tick*). Ini menghindari "beli lalu langsung turun", karena saham yang lemah sejak pagi tidak akan terbeli.
- TP +3%, SL −2%, dibulatkan ke fraksi harga.
- Sisa posisi ditutup paling lambat 15.45.
- Lot dihitung agar rugi saat kena SL ≈ 1% modal. Lot menjadi 0 jika IHSG TURUN atau rem rugi aktif, dan dipotong setengah jika IHSG WASPADA.

**Status hari ini** (belum terpicu / terpicu / kena TP / kena SL) ditampilkan dari harga Yahoo yang dapat tertunda. Karena urutan harga di dalam hari tidak diketahui, status ini hanyalah perkiraan.

**Penyetelan otomatis (walk-forward).** Setiap kali data BEI baru masuk, sistem mencoba 27 kombinasi setelan: skor minimum 60/70/80, TP +2/+3/+4%, dan SL −1,5/−2/−3%. Prosesnya:
1. Setelan terbaik dipilih dari **60% data awal** (periode latih).
2. Setelan itu diuji pada **40% data terakhir** yang tidak dipakai untuk memilih (periode uji).
3. Statusnya ditentukan dari hasil periode uji:
   - **Layak:** hasil periode uji positif setelah *fee* dengan ≥ 8 transaksi. Setelan dipakai otomatis.
   - **Tidak layak:** hasil periode uji negatif. Saran lot menjadi 0, dan daftar hanya untuk dipantau.
   - **Data kurang:** setelan bawaan dipakai dengan lot dipotong setengah.

Pemisahan latih dan uji ini mencegah setelan yang hanya "kebetulan cocok" dengan data lama. Pada data demo yang acak, hasilnya selalu "tidak layak", dan memang begitulah seharusnya.

**Uji historis:** tombol "Jalankan uji" menampilkan hasil rencana aktif pada seluruh histori (±120 hari bursa). **Jangan pakai uang sungguhan jika hasilnya negatif.**

***Broker summary*** tidak tersedia gratis lewat API resmi, jadi dicek manual di Stockbit. Panduan singkatnya ada di halaman.

**Alarm 15.00:** jika alert aktif dan masih ada posisi tercatat, *dashboard* berbunyi agar posisi ditutup. Alarm memakai jam perangkat, jadi pastikan zona waktunya WIB.

Parameter day trade diatur di `config.json` bagian `daytrade`. Isi `"auto_tune": false` untuk mematikan penyetelan otomatis.

## Menu lain
- **Posisi:** pantau saham yang dipegang, dengan status SELL saat kena SL/TP atau muncul sinyal teknikal SELL.
- **Cek Saham:** daftar cek sebelum membeli dan kondisi posisi yang sedang dipegang.
- **Jurnal & rem rugi:** statistik kesalahan dan penghentian *entry* saat batas rugi mingguan tercapai.
- **Lainnya:** Radar Akumulasi (*swing*), Sinyal BUY/SELL *watchlist*, Backtest *swing*, Pengaturan & Kondisi Pasar, dan Panduan.

### Radar akumulasi: enam ciri
1. **Tren naik** (20 poin): harga di atas MA50, dan MA50 menanjak.
2. **Dekat puncak** (15 poin): harga paling jauh 8% di bawah harga tertinggi 60 hari.
3. **Pergerakan menyempit** (20 poin): volatilitas turun.
4. **Volume mengering** (15 poin): volume 10 hari < 85% rata-rata 50 hari, tanda penjual mulai habis.
5. **OBV naik saat harga datar** (20 poin; 10 poin jika OBV naik saja): indikasi akumulasi diam-diam.
6. **Volume hari naik > volume hari turun** (10 poin).

Saham menjadi kandidat jika skornya ≥ 60, trennya naik, belum menembus *resistance*, dan likuid. Radar dihitung dari *bar* harian yang sudah lengkap, supaya volume parsial hari ini tidak memengaruhi skor.

### Pengaman otomatis
- **IHSG TURUN:** saran lot menjadi 0 dan Cek Saham menjawab "JANGAN MASUK DULU".
- **IHSG WASPADA:** saran lot dipotong setengah.
- **Rem kerugian:** jika rugi minggu ini (dari Jurnal) mencapai batas, bawaannya 3% modal, semua saran lot menjadi 0 sampai Senin berikutnya.
- **Ukuran posisi:** lot dihitung agar rugi saat kena SL ≈ risiko per transaksi (bawaan 1% modal), dan satu posisi maksimal 25% modal.

## Cara kerja hosting (gratis, tanpa kartu)

*Dashboard* berjalan sebagai **situs statis di GitHub Pages**. Semua perhitungan dikerjakan **GitHub Actions** (`.github/workflows/dashboard.yml`) sesuai jadwal:

| Waktu (WIB) | Isi pembaruan |
|---|---|
| 06.30, Senin–Jumat | Daftar Pagi siap sebelum bursa buka |
| Tiap 15 menit, 09.00–15.45 | Harga, sinyal, radar, dan status saham Daftar Pagi (terpicu / TP / SL) |
| 17.30, Senin–Jumat | Data penutupan BEI, penyetelan otomatis, dan backtest swing |

Setiap *push* ke `main` dan tombol **Run workflow** di tab Actions juga memicu pembaruan.

Catatan hosting:
- Jadwal GitHub Actions bisa terlambat beberapa menit. *Dashboard* menampilkan peringatan jika data lebih dari 40 menit belum diperbarui saat bursa buka.
- GitHub Pages gratis hanya untuk repositori publik, jadi situs ini **tanpa password**. Posisi, jurnal, dan pengaturan tetap tersimpan di *browser* Anda sendiri, tidak di situs.
- Cek Saham hanya untuk saham *watchlist* dan saham di Daftar Pagi, karena tidak ada server yang bisa dipanggil sewaktu-waktu.
- GitHub bisa menonaktifkan jadwal Actions pada repositori yang tidak ada aktivitasnya selama 60 hari. Jika itu terjadi, aktifkan lagi dari tab Actions.

### Menjalankan di komputer sendiri (opsional)
```bash
pip install -r requirements.txt
DEMO=1 python build.py --out site --heavy && python -m http.server -d site 8000   # versi statis, data demo
uvicorn app:app --port 8000                                                       # server lokal, diperbarui otomatis
```

## Pengaturan
- **Modal, risiko per transaksi, batas posisi, dan batas rugi mingguan:** atur di Beranda. Disimpan per perangkat.
- **`config.json`:** berisi `watchlist` (contoh saham likuid, **bukan rekomendasi**), parameter strategi, *fee broker*, interval *refresh*, dan jam sesi BEI.

## Batasan
- **Data BEI diambil dari endpoint situs idx.co.id, bukan API resmi yang terdokumentasi.** *Server cloud* bisa ditolak oleh proteksi situs, dan format datanya bisa berubah sewaktu-waktu. Jika gagal, Daftar Pagi menampilkan pesan error. Endpoint ini belum diuji dari *server* produksi.
- **Data Yahoo Finance gratis tetapi dapat tertunda.** Setiap kartu sinyal menampilkan jam "harga per". Cocokkan dengan Stockbit sebelum order.
- **Stockbit tidak menyediakan API order untuk nasabah**, jadi eksekusi dilakukan lewat Auto Buy / Auto Sell / Trailing Stop di aplikasi Stockbit.
- **Data tersimpan per perangkat:** posisi, jurnal, dan pengaturan disimpan di *browser* (`localStorage`), jadi tidak tersinkron antarperangkat.
- **Cakupan backtest:** memakai data harian dan tidak memperhitungkan ARA/ARB, suspensi, atau antrean *bid/offer*. Hasil masa lalu tidak menjamin hasil ke depan.
- **Batas *cut loss* di Cek Saham bersifat teknikal:** *support* terdekat di bawah harga. Keputusan tetap di tangan Anda.

## Uji
```bash
python -m pytest -q tests
```

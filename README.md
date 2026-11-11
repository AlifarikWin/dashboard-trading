# Dashboard Trading BEI

*Dashboard* web untuk dua masalah utama trader ritel: **masuk di waktu yang salah** dan **rugi yang dibiarkan membesar**. Dirancang untuk HP dan alur *auto order* Stockbit. Setiap kartu saham punya tombol **📈 Chart** yang membuka *chart* TradingView.

> Tidak ada indikator yang bisa memastikan saham akan naik. Informasi "sebelum naik" dari orang dalam adalah *insider trading* dan ilegal. *Dashboard* ini menyaring peluang yang lebih baik secara statistik dan membatasi rugi. Ini bukan rekomendasi investasi.

## Halaman utama: Daftar Pagi Day Trade

Dirancang untuk *day trader* yang bekerja 08.00–17.00. Semua keputusan dibuat **sebelum pasar buka**, dan eksekusinya lewat Auto Order Stockbit.

**Sumber data:** ringkasan perdagangan harian resmi BEI (`GetStockSummary`) untuk **seluruh saham**, termasuk *foreign buy/sell*. Situs BEI menolak *server cloud* (HTTP 403), jadi data ini diambil oleh **laptop Anda** lewat *self-hosted runner* (lihat "Data BEI lewat laptop"). Selama data laptop belum ada, Daftar Pagi memakai data Yahoo untuk saham *watchlist* (tanpa data asing).

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
- **Posisi Saya:** pantau saham yang dipegang, dengan status JUAL saat kena SL/TP atau muncul sinyal teknikal SELL.
- **Cek Saham:** daftar cek sebelum membeli. Jawabannya otomatis "JANGAN MASUK DULU" jika saham punya penanda risiko BEI.
- **Jurnal:** catatan transaksi beserta kondisi saat masuk (tenang, FOMO, balas dendam, dan sebagainya) dan laporan 7 hari terakhir per kondisi.
- **Analisis Saham:** *chart* dengan area beli/jual, level tembus/jebol, batas ARA/ARB besok, kalender peristiwa, berita terbaru (ditandai titik di *chart*), teknikal, dan fundamental.
- **Stream Peristiwa:** berita dan keterbukaan informasi BEI untuk saham yang dipantau, lengkap dengan reaksi harga sejak berita terbit dan statistik reaksi per jenis peristiwa.
- **Track Record:** Daftar Pagi disimpan sebelum hari perdagangan, lalu dinilai dengan harga sebenarnya.
- **Lainnya:** Sinyal *watchlist*, Radar *swing*, Hasil Uji Strategi, Pengaturan, Panduan Harian, dan Fungsi Menu (berisi tombol tur singkat).

Setiap istilah teknis punya tombol ⓘ untuk penjelasan singkat. *Dashboard* bisa dipasang di layar utama HP (menu *browser* → *Add to Home screen*) dan tetap bisa dibuka saat offline dengan data terakhir.

### Penanda risiko dari BEI
Diambil dari data *screener* dan suspensi BEI (lewat laptop). Penanda yang **memblokir** saham dari Daftar Pagi dan mengubah BUY menjadi WATCH:
- notasi khusus apa pun (termasuk sistem notasi baru sejak 3 Agustus 2026 dan Papan Pemantauan Khusus/X),
- UMA dalam 14 hari terakhir,
- status pencatatan tidak aktif,
- suspensi dalam 7 hari terakhir (kecuali pengumuman pembukaan suspensi).

Aksi korporasi dan peristiwa dalam 2 hari bursa ditampilkan sebagai peringatan tanpa memblokir.

### Batas ARA/ARB
Dihitung untuk hari bursa berikutnya dari harga penutupan, dengan aturan yang berlaku sesuai tanggal:
- **20 Oktober–31 Desember 2026:** Rp1–10: Rp1; Rp11–14: ARA 35%, ARB Rp1; Rp15–200: ARA 35%, ARB 7%; >Rp200–5.000: ARA 25%, ARB 7%; >Rp5.000: ARA 20%, ARB 7%.
- **Mulai 1 Januari 2027:** simetris 35%/25%/20% sesuai rentang harga.

Kartu Daftar Pagi memberi peringatan jika TP di atas ARA atau SL di bawah ARB. Aturan Papan Akselerasi dan Papan Pemantauan Khusus berbeda dan belum dihitung; saham di papan tersebut sudah diblokir oleh penanda notasi. Periksa kembali pengumuman BEI bila ada perubahan aturan.

### Pengaman tambahan
- **Tren mingguan:** BUY harian diturunkan menjadi WATCH jika tren mingguan turun. Perhitungannya hanya memakai data yang sudah ada pada hari itu (tanpa melihat masa depan).
- **Kekuatan relatif:** setiap saham dibandingkan dengan IHSG (20 dan 60 hari) dan rata-rata sektornya. Penyetelan otomatis juga menguji apakah menyaring saham yang lebih lemah dari IHSG memperbaiki hasil.
- **Validasi data:** baris harga nol/kosong dibuang; lonjakan >40% dalam 90 hari terakhir atau data basi >3 hari bursa membuat saham dikeluarkan dari perhitungan hari itu.
- **Gerbang sebelum beli:** saat mencatat posisi, *dashboard* menampilkan aturan yang dilanggar (SL kosong, IHSG turun, lot melebihi saran, penanda risiko, batas harian). Posisi tetap bisa dicatat dengan centang khusus, dan pelanggarannya tercatat di jurnal.
- **Batas harian:** maksimal transaksi per hari (bawaan 3) dan berhenti setelah sejumlah rugi di hari yang sama (bawaan 2). Bisa diubah di Pengaturan.

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
| 06.41, Senin–Jumat | Daftar Pagi siap sebelum bursa buka |
| Tiap 15 menit, 09.07–15.52 | Harga, sinyal, radar, dan status saham Daftar Pagi (terpicu / TP / SL) |
| 17.43, Senin–Jumat | Data penutupan, penyetelan otomatis, dan backtest swing |

Setiap *push* ke `main` dan tombol **Run workflow** di tab Actions juga memicu pembaruan.

Catatan hosting:
- Jadwal GitHub Actions bisa terlambat beberapa menit. *Dashboard* menampilkan peringatan jika data lebih dari 40 menit belum diperbarui saat bursa buka.
- GitHub Pages gratis hanya untuk repositori publik, jadi situs ini **tanpa password**. Posisi, jurnal, dan pengaturan tetap tersimpan di *browser* Anda sendiri, tidak di situs.
- Cek Saham hanya untuk saham *watchlist* dan saham di Daftar Pagi, karena tidak ada server yang bisa dipanggil sewaktu-waktu.
- GitHub bisa menonaktifkan jadwal Actions pada repositori yang tidak ada aktivitasnya selama 60 hari. Jika itu terjadi, aktifkan lagi dari tab Actions.

### Jadwal yang andal: picu dari cron-job.org (gratis)
Jadwal bawaan GitHub Actions sering terlambat atau dilewati, terutama di repositori baru. Untuk pembaruan tiap 15 menit yang konsisten, picu *workflow* dari layanan cron eksternal:

1. **Buat token GitHub** di github.com/settings/personal-access-tokens/new (*Fine-grained token*):
   - *Repository access:* **Only select repositories** → `dashboard-trading`.
   - *Permissions → Repository permissions → Actions:* **Read and write**. Izin lain tidak perlu.
   - Tetapkan masa berlaku (misalnya 1 tahun), lalu salin tokennya.
2. **Buat akun di cron-job.org**, lalu klik *Create cronjob*:
   - *COMMON*: URL `https://api.github.com/repos/AlifarikWin/dashboard-trading/actions/workflows/dashboard.yml/dispatches`, *Execution schedule* **Every 15 minutes**.
   - *ADVANCED*: *Request method* **POST**. Isi *headers*:
     - `Authorization: Bearer <token>`
     - `Accept: application/vnd.github+json`
     - `X-GitHub-Api-Version: 2022-11-28`
   - *Request body*: `{"ref":"main","inputs":{"dari":"cron"}}`

Cukup satu *cronjob*. *Workflow* sendiri yang memilih kapan benar-benar dijalankan: Senin–Jumat pukul 06.30–16.14 WIB dan 17.30–17.59 WIB (data penutupan dan uji strategi). Pemicu di luar jam itu langsung dilewati dalam beberapa detik.

Respons sukses dari GitHub adalah **HTTP 204**.

### Notifikasi HP lewat ntfy (gratis, tanpa akun)
Notifikasi dikirim saat Daftar Pagi siap, saat saham Daftar Pagi terpicu/kena TP/kena SL, saat data laptop lebih dari 2 hari tidak masuk, dan saat pembaruan gagal.
1. Pasang aplikasi **ntfy** (Play Store/App Store).
2. Pilih nama topik yang sulit ditebak, misalnya `trading-ali-<angka acak 8 digit>`. Siapa pun yang tahu nama topik bisa membaca notifikasinya.
3. Di aplikasi ntfy, *Subscribe to topic* dengan nama tersebut.
4. Di GitHub: *Settings → Secrets and variables → Actions → New repository secret*. Nama: `NTFY_TOPIC`, isi: nama topik.

### Data BEI lewat laptop (self-hosted runner)
Situs BEI menolak *server* GitHub, jadi data resmi BEI (ringkasan harian, asing, notasi, suspensi, kalender, keterbukaan informasi) diambil dari laptop Anda, lalu disimpan ke *branch* `records`.
1. Pasang **Python 3.12** dan **Git** di laptop.
2. Di GitHub: *Settings → Actions → Runners → New self-hosted runner*. Pilih OS laptop, lalu jalankan perintah yang ditampilkan (unduh, `config`, lalu `run`).
3. Agar berjalan otomatis setelah laptop menyala, pasang sebagai *service*: Windows: jawab **Y** saat `config.cmd` menanyakan *run as service*; Linux/macOS: `sudo ./svc.sh install && sudo ./svc.sh start`.
4. Uji sekali: tab *Actions → data-bei-laptop → Run workflow*.

Jadwalnya 17.52 WIB Senin–Jumat. Jika laptop mati pada jam itu, *job* menunggu dan berjalan begitu laptop menyala (maksimal 24 jam). Setelah data masuk, *workflow* `dashboard` dipicu otomatis.

### Penyimpanan permanen
Data yang perlu bertahan antar-*run* (Track Record, arsip berita, kalender, fundamental, hasil penyetelan, data BEI dari laptop) disimpan di *branch* `records`. Jangan hapus *branch* ini.

### Menjalankan di komputer sendiri (opsional)
```bash
pip install -r requirements.txt
DEMO=1 python build.py --out site --heavy && python -m http.server -d site 8000   # versi statis, data demo
uvicorn app:app --port 8000                                                       # server lokal, diperbarui otomatis
```

## Pengaturan
- **Modal, risiko per transaksi, batas rugi mingguan, batas harian, dan batas posisi:** atur di menu Pengaturan. Disimpan per perangkat.
- **Pindah perangkat:** Pengaturan → *Unduh cadangan* menghasilkan file JSON berisi posisi, jurnal, dan pengaturan; buka di perangkat lain lalu pilih *Pulihkan*.
- **`config.json`:** berisi `watchlist` (contoh saham likuid, **bukan rekomendasi**), parameter strategi, *fee broker*, interval *refresh*, dan jam sesi BEI.

## Batasan
- **Data BEI diambil dari endpoint situs idx.co.id, bukan API resmi yang terdokumentasi.** Formatnya bisa berubah sewaktu-waktu, dan pengambilannya bergantung pada laptop yang menyala. *Dashboard* memberi tahu jika data laptop sudah lebih dari 2 hari.
- **Berita** diambil dari Google News RSS dan keterbukaan informasi BEI. Hanya judul, sumber, waktu, dan tautan yang ditampilkan. Jenis dan arah berita ditentukan dengan aturan kata kunci, sehingga bisa keliru. Statistik reaksi baru muncul setelah ada minimal 10 kejadian per jenis.
- **Track Record** baru bermakna setelah beberapa minggu. Status hari berjalan memakai data harian, sehingga urutan SL/TP di dalam hari tidak diketahui; jika keduanya tersentuh, hasilnya dihitung sebagai SL (asumsi terburuk).
- **Data Yahoo Finance gratis tetapi dapat tertunda.** Setiap kartu sinyal menampilkan jam "harga per". Cocokkan dengan Stockbit sebelum order.
- **Stockbit tidak menyediakan API order untuk nasabah**, jadi eksekusi dilakukan lewat Auto Buy / Auto Sell / Trailing Stop di aplikasi Stockbit.
- **Data tersimpan per perangkat:** posisi, jurnal, dan pengaturan disimpan di *browser* (`localStorage`). Gunakan cadangan untuk memindahkannya.
- **Cakupan backtest:** memakai data harian dan tidak memperhitungkan antrean *bid/offer*. Hasil masa lalu tidak menjamin hasil ke depan.
- **Batas *cut loss* di Cek Saham bersifat teknikal:** *support* terdekat di bawah harga. Keputusan tetap di tangan Anda.

## Uji
```bash
python -m pytest -q tests
```

# Dashboard Trading BEI

*Dashboard* web untuk dua masalah utama trader ritel: **masuk di waktu yang salah** dan **rugi yang dibiarkan membesar**. Dirancang untuk HP dan alur *auto order* Stockbit. Setiap kartu saham punya tombol **📈 Chart** yang membuka *chart* TradingView.

> Tidak ada indikator yang bisa memastikan saham akan naik. Informasi "sebelum naik" dari orang dalam adalah *insider trading* dan ilegal. *Dashboard* ini menyaring peluang yang lebih baik secara statistik dan membatasi rugi. Ini bukan rekomendasi investasi.

## Halaman utama: Daftar Pagi Day Trade

Dirancang untuk *day trader* yang bekerja 08.00–17.00. Semua keputusan dibuat **sebelum pasar buka**, dan eksekusinya lewat Auto Order Stockbit.

**Sumber data:** harga harian **seluruh emiten BEI (±800 saham)** dari Yahoo Finance. Situs BEI memasang proteksi Cloudflare yang memblokir akses otomatis (juga dari laptop rumah), dan proteksi itu sengaja tidak ditembus. Akibatnya data asing harian tidak tersedia: komponen skor asing (35 poin) dihilangkan dan skor sisanya diskalakan ke 0–100.

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
- **Semua Saham:** seluruh emiten yang bisa disaring (tren naik, dekat level tembus, di area beli, lebih kuat dari IHSG, volume melonjak, fundamental kuat, di bawah nilai wajar, pola dividen) dan diurutkan. Saham dengan penanda risiko disembunyikan secara bawaan.
- **Posisi Saya:** pantau saham yang dipegang, dengan status JUAL saat kena SL/TP atau muncul sinyal teknikal SELL.
- **Cek Saham:** daftar cek sebelum membeli, untuk emiten mana pun. Jawabannya otomatis "JANGAN MASUK DULU" jika saham punya penanda risiko.
- **Jurnal:** catatan transaksi beserta kondisi saat masuk (tenang, FOMO, balas dendam, dan sebagainya), laporan 7 hari terakhir, dan **skor kepatuhan**: untuk posisi yang dicatat lewat "Sudah beli" di Daftar Pagi, dashboard menilai apakah Anda membeli di rentang *trigger*–*limit*, memakai lot sesuai saran, dan keluar di TP/SL/tutup sore, lalu membandingkan hasil Anda dengan hasil rencana yang sama di Track Record.
- **Analisis Saham:** cari emiten mana pun. Isinya kesimpulan satu baris, *chart* dengan area beli/jual dan level tembus/jebol, empat kotak level, lima baris teknikal, empat baris fundamental, pola dividen, serta berita (*watchlist*). Angka lengkap ada di "Lihat detail". Saham *watchlist* dan Daftar Pagi diperbarui tiap 15 menit; emiten lain sekali sehari setelah bursa tutup.
- **Stream Peristiwa:** berita untuk saham yang dipantau, lengkap dengan reaksi harga sejak berita terbit dan statistik reaksi per jenis peristiwa.
- **Track Record:** Daftar Pagi disimpan sebelum hari perdagangan, lalu dinilai dengan harga sebenarnya.
- **Lainnya:** Sinyal *watchlist*, Radar *swing*, Hasil Uji Strategi, Pengaturan, Panduan Harian, dan Fungsi Menu (berisi tombol tur singkat).

Setiap istilah teknis punya tombol ⓘ untuk penjelasan singkat. *Dashboard* bisa dipasang di layar utama HP (menu *browser* → *Add to Home screen*) dan tetap bisa dibuka saat offline dengan data terakhir.

### Penanda risiko (perkiraan dari pola harga)
Notasi khusus, UMA, dan suspensi resmi BEI tidak bisa diambil otomatis, jadi dashboard memakai perkiraan dari data harga. Penanda yang **memblokir** saham dari Daftar Pagi dan mengubah BUY menjadi WATCH:
- tidak ada transaksi lebih dari 3 hari bursa (kemungkinan suspensi),
- pergerakan ekstrem 10 hari: ≥ 2 hari naik mendekati ARA, atau naik ≥ 50% / turun ≥ 40% (rawan UMA).

Peringatan tanpa memblokir: nilai transaksi rata-rata < Rp1 miliar/hari dan harga ≤ Rp50. Peristiwa dalam 2 hari bursa juga ditampilkan sebagai peringatan. **Notasi khusus tetap perlu dicek di Stockbit sebelum membeli.**

### Pola dividen
Dari riwayat dividen 5 tahun (Yahoo), untuk setiap saham dihitung berapa kali harga naik dalam 10 hari bursa sebelum *cum date* (hari bursa sebelum *ex date*), rata-rata kenaikannya, dan rata-rata penurunan harga pembukaan saat *ex date*. Ditampilkan di Analisis Saham, kalender Stream, dan saringan Semua Saham. Diperbarui seminggu sekali.

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
Notifikasi dikirim saat Daftar Pagi siap, saat saham Daftar Pagi terpicu/kena TP/kena SL, saat pembaruan gagal, dan **setiap Senin pagi** berupa laporan mingguan: hasil Track Record minggu lalu, status uji strategi dan IHSG, serta peristiwa terjadwal minggu ini. Skor kepatuhan tidak ikut dikirim karena jurnal hanya tersimpan di perangkat Anda.
1. Pasang aplikasi **ntfy** (Play Store/App Store).
2. Pilih nama topik yang sulit ditebak, misalnya `trading-ali-<angka acak 8 digit>`. Siapa pun yang tahu nama topik bisa membaca notifikasinya.
3. Di aplikasi ntfy, *Subscribe to topic* dengan nama tersebut.
4. Di GitHub: *Settings → Secrets and variables → Actions → New repository secret*. Nama: `NTFY_TOPIC`, isi: nama topik.

### Data seluruh emiten
Daftar emiten diambil dari *screener* Yahoo (bursa JKT) dan diperbarui seminggu sekali. Harga 1 tahun seluruh emiten diunduh sekali per hari bursa (setelah bursa tutup), lalu analisis per saham disimpan di *cache* GitHub Actions. *Run* tiap 15 menit cukup memulihkan *cache* tersebut, sehingga tetap cepat. Fundamental rinci (pertumbuhan, margin, DER) diperbarui bergiliran 150 saham per sore; sebelum gilirannya, fundamental memakai data ringkas *screener* (PER, PBV, ROE = EPS ÷ nilai buku, *dividend yield*).

### Runner laptop (tidak aktif)
*Workflow* `data-bei-laptop` disiapkan untuk mengambil data resmi BEI dari laptop, tetapi situs BEI juga memblokir akses dari laptop, sehingga jadwalnya dimatikan. *Runner* yang sudah terpasang boleh dibiarkan; *workflow* hanya berjalan bila dipicu manual.

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
- **Tanpa data resmi BEI:** tidak ada data asing harian, notasi khusus, UMA, dan keterbukaan informasi BEI. Jalur resmi untuk data tersebut adalah langganan berbayar (Layanan Data BEI atau vendor data).
- **Saham yang tercatat kurang dari 60 hari bursa** belum dianalisis.
- **Berita** diambil dari Google News RSS, hanya untuk saham *watchlist* dan Daftar Pagi. Hanya judul, sumber, waktu, dan tautan yang ditampilkan. Jenis dan arah berita ditentukan dengan aturan kata kunci, sehingga bisa keliru. Statistik reaksi baru muncul setelah ada minimal 10 kejadian per jenis.
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

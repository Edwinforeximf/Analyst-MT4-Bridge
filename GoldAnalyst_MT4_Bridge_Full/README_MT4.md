# Gold Analyst Pro — MT4 Live Feed Bridge

**Bridge read-only**: MetaTrader 4 (Windows desktop/VPS) -> HTTPS -> Railway API -> Gold Analyst Pro dashboard.
EA tidak memuat fungsi trading dan tidak mengirim akun, saldo, maupun identitas nasabah. Enam simbol: XAUUSD, XAGUSD, EURUSD, GBPUSD, USDJPY, USOIL.

## Langkah dari PC Windows / VPS

1. Gunakan terminal **MT4 desktop Windows** yang terhubung ke server broker (bisa akun demo untuk tes). HP Android **tidak dapat menjalankan EA MQL4**; dashboard tetap dapat diakses dari HP.
2. Buka menu **File -> Open Data Folder -> MQL4 -> Experts**. Salin `mt4/GoldAnalyst_MT4_Feed.mq4` ke sana.
3. Di MetaEditor buka EA tersebut, tekan **Compile (F7)**. File `.ex4` baru akan dibuat saat berhasil. Kode `.mq4` belum dikompilasi dalam paket ini.
4. Di MT4 tekan **Ctrl+M (Market Watch)**, tampilkan keenam simbol. Cek ejaan sebenarnya, misalnya `XAUUSDm`, `EURUSD.pro`, atau `WTI`. Untuk simbol yang tidak didukung broker, kosongkan mapping agar dilewati.
5. Buka **Tools -> Options -> Expert Advisors**. Centang **Allow WebRequest for listed URL** dan tambahkan **`https://analyst-multiasset-production.up.railway.app`** (domain, *tanpa path*). Di terminal pastikan pengaturan Expert Advisors mengizinkan EA beroperasi. EA ini tidak akan mengirim order walau pengaturan AutoTrading aktif.
6. Tempel EA pada **SATU** chart MT4 apa pun. Isi `BridgeURL` sesuai domain Railway + `/api/mt4/ingest`, `BridgeToken` sama persis dengan variabel Railway `MT4_BRIDGE_TOKEN`. Gunakan password acak minimal 32 karakter; **jangan** pakai `APP_SECRET`, `SETUP_TOKEN`, atau password nasabah.
7. Atur mapping simbol, tekan OK, lalu periksa tab **Experts**. Setelah muncul tick **baru**, kiriman pertama diproses. Pertama kali kadang perlu membuka chart D1, H4, H1 agar histori terunduh; setidaknya 45 candle masing-masing timeframe.
8. Jangan jalankan EA duplikat dengan token yang sama pada banyak terminal untuk simbol identik; pengiriman yang lebih lama akan ditolak. Pastikan waktu Windows/VPS sinkron dengan server waktu (NTP/automatic time).

## Railway Variables

Di service **analyst-multiasset** -> Variables, tambahkan:

```ini
LIVE_PROVIDER=MT4
MT4_BRIDGE_TOKEN=isi_secret_acak_minimal_32_karakter
MT4_FEED_TTL_SECONDS=150
MT4_MAX_TICK_AGE_SECONDS=120
COOKIE_SECURE=1
STORAGE_DIR=/data
```

Pertahankan `APP_SECRET`, `SETUP_TOKEN`, `GEMINI_API_KEY` dan volume `/data` yang sudah ada. Saat `LIVE_PROVIDER=MT4`, Twelve Data tidak diperlukan untuk mode Live. Mode Screenshot + Gemini tetap tersedia. Jangan masukkan token ke file GitHub atau screenshot.

## Instalasi kode Railway

**Opsi mudah:** dari ZIP utama, gunakan folder web lengkap yang sudah diintegrasikan dengan `mt4_feed.py`. Jangan mengganti project Railway dengan repo kosong; **pertahankan volume dan APP_SECRET**.

**Opsi upgrade:** salin `mt4_feed.py` dan `app.py` baru, plus `market_feed.py`, `live_engine.py`, `static/index.html` ke folder root code dari GoldAnalyst Live V2. Pastikan `Dockerfile` menyalin `mt4_feed.py`. Repo versi screenshot yang belum memiliki `live_engine.py` perlu memakai paket lengkap.

## Verifikasi

- Railway **Deploy Logs** harus menunjukkan Uvicorn startup berhasil.
- Saat belum ada data, tombol **Cek Harga** akan menampilkan "Belum ada feed MT4" (normal).
- Sesudah EA mengirim, tombol **Cek Harga** menampilkan Bid/Ask/Spread/simbol dan waktu dari server broker.
- **Generate Live** menghasilkan candle chart, risk/reward, artikel, infografis dan DOCX. Jika feed kedaluwarsa/mati, sistem memblokir analisis Live.
- Feed diterima per instrumen menggunakan endpoint `POST /api/mt4/ingest`, body JSON, header `X-MT4-Token`. Endpoint dilindungi token dan validasi timestamp; jangan mencobanya melalui browser tanpa token.

## Persyaratan operasional dan risiko

Harga MT4 adalah harga dari server broker yang akun terminal itu terhubung. Meskipun berasal dari broker sendiri, **tidak dijamin sama dengan harga transaksi/fill**, termasuk spread dan kondisi liquidity/slippage. Harga saat akhir pekan dan saat server sedang terputus harus ditolak sebagai data basi. Angka entry/SL/TP adalah skenario berdasarkan data histori; *bias BUY/SELL* bukan instruksi otomatis. Reviewer wajib memvalidasi dan mengungkapkan afiliasi komersial perusahaan broker sebelum publikasi. Pastikan kebijakan penyedia likuiditas dan izin distribusi data broker.

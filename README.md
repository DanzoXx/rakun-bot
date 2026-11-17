# Rakun Bot — @RakunAswBot

Bot Telegram untuk grup, ditulis Python (tanpa framework bot, langsung pakai Telegram Bot API).

## Fitur

- **Reaksi emoji** — 1 reaksi emoji acak di tiap pesan Taro.
- **Trigger kata** — pesan berisi `novan` dibalas stiker Novan; pesan berisi `rakun` dibalas stiker rakun (stiker diatur Taro lewat chat pribadi).
- **/search `<keyword>`** — cari gambar (Safebooru, Wikipedia, Wikimedia Commons, SerpAPI Google Images, Openverse, Bing).
- **/youtube `<link>` & /tiktok `<link>`** — download video (maks 50 MB, batas Bot API Telegram).
- **/dice & /balance** — main dadu pakai koin.
- **Game monster** — `/mon`, `/catch`, `/toko`, `/hadiah`, `/team`, `/battle`, `/heal` (tangkap & battle monster orisinal).
- **/cuaca `[kota]`** — cuaca kota di Indonesia + cuaca pagi otomatis tiap jam 07:00 WIB.
- **Livescore** — notif gol (ESPN & LiveScore API), termasuk gol dibatalkan (VAR).
- **/setnovan** — (chat pribadi, Taro saja) ganti stiker Novan: kirim `/setnovan`, lalu kirim stikernya.

## Menjalankan

```bash
pip install -r requirements.txt
python bot.py
```

## Environment variables

| Variabel | Wajib | Keterangan |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Ya | Token bot dari @BotFather |
| `SERPAPI_API_KEY` | Tidak | SerpAPI Google Images (gratis 250/bulan), untuk /search |
| `YOUTUBE_API_KEY` | Tidak | API download YouTube, untuk /youtube |

API key lain (Pexels, Pixabay, Unsplash, Brave) opsional dan dibaca dari environment juga; kalau tidak diset, sumber itu dilewati.

**Jangan tulis token/key di kode atau commit ke repo.** Isi lewat environment variables di layanan hosting.

## Data

Saldo koin, monster, batas cuaca, dan stiker disimpan di file JSON/txt di folder yang sama (`balances.json`, `monsters.json`, `usernames.json`, `cuaca_limits.json`, `last_sticker.txt`, `novan_sticker.txt`). File-file itu dibuat otomatis kalau belum ada, dan tidak perlu di-commit.

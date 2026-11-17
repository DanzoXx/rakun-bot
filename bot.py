import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN not set")

BASE = f"https://api.telegram.org/bot{TOKEN}"

# Hanya bereaksi ke user ini saja, meskipun bot ada di grup.
ALLOWED_USER_ID = 6265140021

# Kalau ada yang kirim pesan berisi ini, bot reply dengan stiker Novan.
# Berlaku untuk siapa saja, bukan cuma Taro. Kalau stiker Novan belum
# diset, bot reply 🐶 dulu.
TRIGGER_PHRASE = "novan"
DOG_EMOJI = "🐶"

# Stiker custom buat trigger "novan". Diambil dari novan_sticker.txt,
# yang terisi saat Taro kirim stiker ke bot di chat pribadi setelah
# perintah /setnovan.
# Fallback: stiker Novan 😝 dari set "omskwit" (dikirim Taro 2026-11-17
# ~20:04 WIB). Dipakai kalau novan_sticker.txt hilang — mis. setelah
# redeploy di Infrlo, yang filesystem-nya ephemeral dan menghapus
# file runtime.
NOVAN_STICKER_FILE = os.path.join(os.path.dirname(__file__), "novan_sticker.txt")
DEFAULT_NOVAN_STICKER_FILE_ID = "CAACAgUAAxkBAANMavu2xuJQYICF3fwzocWaJdPYzaMAAs8gAAKJBgFX4XtryP8ik5M9BA"
AWAITING_NOVAN_STICKER = set()

def get_novan_sticker_file_id():
    try:
        with open(NOVAN_STICKER_FILE) as f:
            saved = f.read().strip()
            if saved:
                return saved
    except Exception:
        pass
    return DEFAULT_NOVAN_STICKER_FILE_ID

# Kalau ada yang kirim pesan berisi "rakun", bot reply dengan stiker custom.
# Berlaku untuk siapa saja. Stikernya diambil dari last_sticker.txt,
# yang terisi hanya saat Taro sendiri yang kirim stiker ke bot
# (stiker dari orang lain di grup tidak boleh menimpa stiker Taro).
# Fallback: stiker rakun habemuscoon 😋 (disimpan 2026-11-15). Dipakai
# kalau last_sticker.txt hilang — mis. setelah redeploy di Infrlo, yang
# filesystem-nya ephemeral dan menghapus file runtime.
TRIGGER_RAKUN = "rakun"
STICKER_FILE = os.path.join(os.path.dirname(__file__), "last_sticker.txt")
DEFAULT_RAKUN_STICKER_FILE_ID = "CAACAgEAAxkBAAMpavm1s8REkbAqOJ5Ly0Vq5xvrS-EAApIFAAK7TWIULqXmpg2qfvE9BA"

def get_sticker_file_id():
    try:
        with open(STICKER_FILE) as f:
            saved = f.read().strip()
            if saved:
                return saved
    except Exception:
        pass
    return DEFAULT_RAKUN_STICKER_FILE_ID

# Download video TikTok tanpa watermark.
# yt-dlp mengambil file video aslinya dari TikTok, jadi tidak ada watermark.
# Berlaku untuk siapa saja yang kirim link TikTok, batas file 50 MB
# (batas kirim video Bot API Telegram).
TIKTOK_RE = re.compile(r"https?://(?:www\.|vt\.|vm\.)?tiktok\.com/\S+", re.IGNORECASE)
MAX_VIDEO_BYTES = 49 * 1024 * 1024

def find_tiktok_url(text):
    m = TIKTOK_RE.search(text or "")
    return m.group(0).rstrip(").,") if m else None

# Download video YouTube (termasuk Shorts) lewat API giftdealshub.
# API mengembalikan link download langsung (googlevideo.com) beserta
# ukuran file, jadi bot tidak perlu yt-dlp untuk YouTube. Berlaku untuk
# siapa saja yang kirim link YouTube, batas file 50 MB (batas kirim
# video Bot API Telegram). Kuota API: 300 request/bulan.
YOUTUBE_RE = re.compile(r"https?://(?:www\.|music\.)?(?:youtube\.com/(?:watch\?[^ \s]*|shorts/[^ \s]*|live/[^ \s]*)|youtu\.be/[^ \s]+)", re.IGNORECASE)
YOUTUBE_API_URL = "https://otp.giftdealshub.in/omniApi/api/v1/youtube.php"

def find_youtube_url(text):
    m = YOUTUBE_RE.search(text or "")
    return m.group(0).rstrip(").,") if m else None

# /search <keyword>: cari gambar di Pinterest (lewat pencarian gambar,
# Pinterest langsung sering memblokir bot jadi hasilnya diambil dari
# Bing Images dengan kata kunci Pinterest, diutamakan yang dari pinimg.com).
# Berlaku untuk siapa saja. Tanpa token Muse AI, cuma program biasa.
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}
SEARCH_BAD_HOSTS = ("dreamstime", "123rf", "shutterstock", "alamy", "istockphoto", "gettyimages")
MAX_PHOTO_BYTES = 9 * 1024 * 1024

# API key untuk sumber gambar berbayar/gratis (Pexels, Pixabay, Unsplash,
# Brave Search API). Dibaca dari environment variable; kalau tidak diset,
# sumber itu dilewati diam-diam (bot tetap jalan pakai sumber gratis lain).
# Key TIDAK boleh ditulis di kode — isi lewat environment/service, atau
# minta Taro masukkan lewat Secure Vault. Daftar gratis:
#   Pexels:   https://www.pexels.com/api/ (gratis, 200 req/jam)
#   Pixabay:  https://pixabay.com/api/docs/ (gratis, 100 req/menit)
#   Unsplash: https://unsplash.com/developers (gratis, 50 req/jam)
#   Brave:    https://brave.com/search/api/ (gratis, 2000 req/bulan)
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

# Terjemahan kata umum Indonesia -> Inggris supaya pencarian gambar lebih
# akurat (mesin pencari gambar lebih paham kata Inggris). Kata yang tidak
# ada di kamus dibiarkan apa adanya.
ID_EN_WORDS = {
    "merah": "red", "biru": "blue", "hijau": "green", "kuning": "yellow",
    "putih": "white", "hitam": "black", "pink": "pink", "ungu": "purple",
    "oranye": "orange", "jingga": "orange", "coklat": "brown", "cokelat": "brown",
    "abu": "grey", "abu-abu": "grey", "emas": "gold", "perak": "silver",
    "berbaju": "dress", "baju": "dress", "gaun": "dress", "pakaian": "clothes",
    "pohon": "tree", "kucing": "cat", "anjing": "dog", "bunga": "flower",
    "gunung": "mountain", "pantai": "beach", "laut": "sea", "langit": "sky",
    "bulan": "moon", "matahari": "sun", "mobil": "car", "motor": "motorcycle",
    "rumah": "house", "makanan": "food", "buah": "fruit", "hewan": "animal",
    "burung": "bird", "ikan": "fish", "hujan": "rain", "salju": "snow",
    "kota": "city", "desa": "village", "hutan": "forest", "sungai": "river",
    "danau": "lake", "jalan": "road", "jembatan": "bridge", "sekolah": "school",
    "anak": "child", "perempuan": "woman", "laki-laki": "man", "pria": "man",
    "wanita": "woman", "gadis": "girl", "pemuda": "boy",
    "pesawat": "airplane", "kapal": "ship", "kereta": "train",
    "sepeda": "bicycle", "bintang": "star", "awan": "cloud",
    "api": "fire", "air": "water", "daun": "leaf", "rumput": "grass",
    "mawar": "rose", "cantik": "beautiful", "lucu": "cute",
    "besar": "big", "kecil": "small", "malam": "night", "pagi": "morning",
    "senyum": "smile", "hati": "heart", "cinta": "love",
    "kuda": "horse", "sapi": "cow", "ayam": "chicken", "bebek": "duck",
    "ular": "snake", "monyet": "monkey", "singa": "lion",
    "harimau": "tiger", "beruang": "bear", "serigala": "wolf",
    "rubah": "fox", "kelinci": "rabbit", "tikus": "mouse",
    "gajah": "elephant", "jerapah": "giraffe", "paus": "whale",
    "hiu": "shark", "kura": "turtle", "katak": "frog", "elang": "eagle",
    "penguin": "penguin", "merak": "peacock", "angsa": "swan",
    "taman": "garden", "pulau": "island", "pelangi": "rainbow",
    "badai": "storm", "petir": "lightning", "angin": "wind",
    "pasir": "sand", "batu": "stone", "es": "ice",
    "pintu": "door", "jendela": "window", "kursi": "chair",
    "meja": "table", "lampu": "lamp", "cermin": "mirror",
    "naga": "dragon", "hantu": "ghost", "monster": "monster",
    "robot": "robot", "alien": "alien", "dokter": "doctor",
    "guru": "teacher", "raja": "king", "ratu": "queen",
    "pangeran": "prince", "putri": "princess", "bayi": "baby",
    "ayah": "father", "ibu": "mother", "teman": "friend",
    "keluarga": "family", "mimpi": "dream", "sihir": "magic",
}

COLOR_WORDS = {
    "merah": "red", "red": "red",
    "biru": "blue", "blue": "blue",
    "hijau": "green", "green": "green",
    "kuning": "yellow", "yellow": "yellow",
    "putih": "white", "white": "white",
    "hitam": "black", "black": "black",
    "pink": "pink",
    "ungu": "purple", "purple": "purple",
    "oranye": "orange", "jingga": "orange", "orange": "orange",
    "coklat": "brown", "cokelat": "brown", "brown": "brown",
    "abu": "grey", "abu-abu": "grey", "grey": "grey", "gray": "grey",
    "emas": "gold", "gold": "gold",
}

def translate_keyword(keyword):
    # Bedain bahasa dulu (biar translate nggak ngaco):
    # - Kalau keyword mengandung kata Indonesia yang JELAS (ada di kamus dan
    #   bukan kata Inggris), anggap keyword itu Bahasa Indonesia -> translate.
    # - Kalau tidak ada kata Indonesia yang jelas, anggap Bahasa Inggris ->
    #   pakai apa adanya, JANGAN translate (biar kata Inggris yang kebetulan
    #   sama ejaannya dengan kata Indonesia nggak salah arti).
    # Contoh masalah yang diperbaiki: "air" (Inggris=udara, Indonesia=air/water)
    # dan "api" (Inggris=API teknologi, Indonesia=api/fire). Kalau ketik
    # "/search air" (maksudnya udara) tadinya jadi "water" (salah).
    # Tapi "/search air laut" tetap ditranslate karena ada "laut" (jelas Indonesia).
    words = keyword.split()
    lowered = [w.lower() for w in words]
    is_indonesian = any(
        (w in ID_EN_WORDS and w not in AMBIGUOUS_ID_WORDS) for w in lowered
    )
    if not is_indonesian:
        return keyword
    return " ".join(ID_EN_WORDS.get(w, w) for w in lowered)

# Kata yang ejaannya sama di Inggris dan Indonesia tapi artinya beda:
# cuma ditranslate kalau keyword-nya sudah ketahuan Bahasa Indonesia.
AMBIGUOUS_ID_WORDS = {"air", "api"}

def find_color_word(keyword):
    for w in keyword.lower().split():
        if w in COLOR_WORDS:
            return COLOR_WORDS[w]
    return None

def color_fraction(data, color):
    # Hitung seberapa banyak warna tertentu di gambar (0.0 - 1.0).
    # Dipakai untuk memilih gambar yang warnanya paling sesuai keyword,
    # misal "berbaju merah" -> pilih gambar yang paling banyak warna merahnya.
    # Pencarian teks saja tidak bisa membedakan baju merah dan baju putih.
    try:
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(data)).convert("RGB")
        im = im.resize((40, 40))
        px = list(im.getdata())
        if not px:
            return 0.0
        if color == "red":
            c = sum(1 for r, g, b in px if r > 120 and r > g + 40 and r > b + 40)
        elif color == "blue":
            c = sum(1 for r, g, b in px if b > 100 and b > r + 30 and b > g + 30)
        elif color == "green":
            c = sum(1 for r, g, b in px if g > 100 and g > r + 30 and g > b + 30)
        elif color == "yellow":
            c = sum(1 for r, g, b in px if r > 150 and g > 150 and b < 130 and r > b + 40 and g > b + 40)
        elif color == "white":
            c = sum(1 for r, g, b in px if r > 200 and g > 200 and b > 200)
        elif color == "black":
            c = sum(1 for r, g, b in px if r < 50 and g < 50 and b < 50)
        elif color == "pink":
            c = sum(1 for r, g, b in px if r > 180 and 100 < g < 200 and 120 < b < 220 and r > g + 40)
        elif color == "purple":
            c = sum(1 for r, g, b in px if r > 80 and b > 120 and b > r and g < 150 and r > g + 20)
        elif color == "orange":
            c = sum(1 for r, g, b in px if r > 180 and 80 < g < 180 and b < 100)
        elif color == "brown":
            c = sum(1 for r, g, b in px if 80 < r < 170 and 40 < g < 120 and b < 80 and r > g + 20)
        elif color == "grey":
            c = sum(1 for r, g, b in px if abs(r - g) < 20 and abs(g - b) < 20 and 80 < r < 200)
        elif color == "gold":
            c = sum(1 for r, g, b in px if r > 180 and g > 140 and b < 100)
        else:
            return 0.0
        return c / len(px)
    except Exception:
        return 0.0

# Safebooru: API gambar anime berbasis tag, gratis, tanpa API key,
# isinya aman (SFW) jadi cocok buat grup. Tag jauh lebih akurat dari
# pencarian teks: "berbaju merah" -> red_dress, hasilnya beneran
# gambar yang bajunya merah. Danbooru diblokir dari komputer ini,
# jadi Safebooru yang dipakai. Dipakai untuk keyword anime DAN untuk
# nama karakter anime ("kurumi" -> gambar Tokisaki Kurumi), karena
# pencarian teks biasa untuk nama karakter malah mengembalikan foto
# orang sungguhan. Jikan (MyAnimeList) dan AniList diblokir dari
# komputer ini, Konachan mengembalikan kosong untuk "kurumi",
# jadi Safebooru satu-satunya sumber anime yang bisa dipakai.
SAFEBOORU_WORD_TAGS = {
    "kucing": "cat", "anjing": "dog", "burung": "bird", "ikan": "fish",
    "pohon": "tree", "bunga": "flower", "gunung": "mountain",
    "pantai": "beach", "laut": "sea", "langit": "sky", "bulan": "moon",
    "matahari": "sun", "salju": "snow", "hujan": "rain", "hutan": "forest",
    "sekolah": "school_uniform", "seragam": "school_uniform",
    "pedang": "sword", "mahkota": "crown", "topi": "hat",
    "kacamata": "glasses", "sayap": "wings", "kimono": "kimono",
    "maid": "maid", "penyihir": "witch", "peri": "fairy",
}

# Kata umum (hasil terjemahan ID_EN_WORDS + warna): kalau SEMUA kata
# keyword adalah kata umum ("anjing" -> dog, "pohon" -> tree), yang
# dicari hampir pasti foto beneran, jadi jangan coba Safebooru.
# Kalau ada kata yang bukan kata umum ("kurumi", "rem", "mikasa"),
# kemungkinan itu nama karakter anime -> coba Safebooru.
GENERAL_WORDS = (set(ID_EN_WORDS.values()) | set(COLOR_WORDS.values()) | {
    "sunset", "sunrise", "wallpaper", "background", "photo", "picture",
    "art", "drawing", "logo", "poster", "cover", "band", "song",
    "film", "movie", "game", "anime", "manga", "cosplay",
    # Kata ambigu (Inggris & Indonesia, beda arti) diperlakukan sebagai
    # kata umum supaya tidak dikira nama karakter anime oleh Safebooru:
    # "air" (Inggris=udara) dan "api" (Inggris=API) bukan nama anime.
    "air", "api",
})

def safebooru_posts(tag_queries):
    # Coba tiap tag satu-satu sampai ada yang mengembalikan hasil.
    # Mengembalikan list post pertama yang tidak kosong (atau []).
    for tags in tag_queries:
        if not tags:
            continue
        try:
            r = requests.get(
                "https://safebooru.org/index.php",
                params={"page": "dapi", "s": "post", "q": "index",
                        "tags": tags, "limit": 20, "json": 1},
                headers=SEARCH_HEADERS,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                return data
        except Exception as e:
            print(f"Safebooru search failed for '{tags}': {e}", flush=True)
    return []

def keyword_to_safebooru_tags(keyword):
    words = keyword.lower().split()
    tags = []
    i = 0
    while i < len(words):
        w = words[i]
        # "berbaju/gaun <warna>" -> <warna>_dress, "rambut <warna>" -> <warna>_hair
        if w in ("berbaju", "gaun", "baju", "pakaian") and i + 1 < len(words):
            color = COLOR_WORDS.get(words[i + 1])
            if color:
                tags.append(f"{color}_dress")
                i += 2
                continue
        if w == "rambut" and i + 1 < len(words):
            nxt = words[i + 1]
            if nxt == "panjang":
                tags.append("long_hair")
                i += 2
                continue
            if nxt == "pendek":
                tags.append("short_hair")
                i += 2
                continue
            color = COLOR_WORDS.get(nxt)
            if color:
                tags.append(f"{color}_hair")
                i += 2
                continue
        if w in SAFEBOORU_WORD_TAGS:
            tags.append(SAFEBOORU_WORD_TAGS[w])
        i += 1
    return tags

def _words(text):
    # Pecah teks jadi kata utuh (huruf/angka saja, huruf kecil).
    # Dipakai supaya "gta" hanya cocok dengan kata "gta", bukan
    # substring di dalam kata/tag lain.
    return re.findall(r"[a-z0-9]+", (text or "").lower())

def relevance_score(title, tags, query_words, require_all=False):
    # Seberapa cocok judul/tag gambar dengan keyword (0.0 - 1.0).
    # require_all=True (untuk nama orang): skor 0 kalau ada satu kata
    # keyword yang tidak muncul di judul maupun tag. Ini mencegah
    # "emi" saja (EMI keuangan) lolos untuk "emi fukada".
    # Ini penyaring utama hasil ngaco:
    # - Openverse mencocokkan "anjing" secara fuzzy, "ANJ" cocok dengan
    #   nomor registrasi pesawat (VT-ANJ, YU-ANJ) sehingga mengembalikan
    #   foto pesawat.
    # - "gta 6" mengembalikan foto bunga: tag foto bunga itu mengandung
    #   substring "gta" entah di mana, dan skor lama pakai substring
    #   sehingga bunga itu lolos dengan skor 1.0.
    # Perbaikan: kata harus cocok UTUH (bukan substring), dan judul
    # lebih penting dari tag (70/30). Foto yang kata kuncinya cuma ada
    # di tag acak jadi skornya kecil dan kalah dari yang judulnya cocok.
    if not query_words:
        return 0.0
    title_words = set(_words(title))
    tag_words = set(_words(tags))
    if require_all:
        all_words = title_words | tag_words
        if any(w not in all_words for w in query_words):
            return 0.0
    title_hits = sum(1 for w in query_words if w in title_words)
    tag_hits = sum(1 for w in query_words if w in tag_words)
    return 0.7 * (title_hits / len(query_words)) + 0.3 * (tag_hits / len(query_words))

def wikipedia_image_url(query):
    # Gambar utama artikel Wikipedia untuk topiknya. Wikipedia tahu
    # "gta 6" itu game Grand Theft Auto VI, jadi gambarnya cover game,
    # bukan mobil Alfa Romeo GTA atau bunga yang tag-nya kebetulan cocok.
    # Gratis, tanpa API key. Mengembalikan None kalau tidak ada artikel
    # atau artikelnya tidak punya gambar.
    try:
        from urllib.parse import quote
        s = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query,
                    "srlimit": 1, "format": "json"},
            headers={**SEARCH_HEADERS,
                     "User-Agent": "RakunAswBot/1.0 (Telegram bot)"},
            timeout=30,
        ).json()
        res = s.get("query", {}).get("search", [])
        if not res:
            return None
        title = res[0]["title"]
        p = requests.get(
            "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title, safe=""),
            headers={"User-Agent": "RakunAswBot/1.0 (Telegram bot)"},
            timeout=30,
        ).json()
        # Jangan pakai gambar artikel yang ternyata judul serial TV/film,
        # misal "sunset beach" -> artikel serial TV "Sunset Beach",
        # padahal yang dicari foto pantai saat sunset. Untuk nama serial/
        # film terkenal, Openverse/Bing tetap mengembalikan posternya,
        # jadi melewatkan gambar Wikipedia di sini tidak merugikan.
        # Video game tidak dilewatkan: "gta 6" memang maunya cover game.
        desc = (p.get("description") or "").lower()
        if any(k in desc for k in ("television series", "television show",
                                    "soap opera", " film")):
            return None
        img = (p.get("originalimage") or {}).get("source")
        if img:
            return img.split("?")[0]  # buang ?utm_source=...
        return None
    except Exception as e:
        print(f"Wikipedia search failed for '{query}': {e}", flush=True)
        return None

def commons_image_urls(translated, query_words, require_all=False):
    # Wikimedia Commons: perpustakaan foto gratis (100 juta+ file),
    # tanpa API key. Cari di namespace File (6), ambil thumbnail 800px.
    # Judul file dipakai untuk skor relevansi, misal
    # "File:Sleeping cat on her back.jpg" -> "Sleeping cat on her back".
    # Mengembalikan list (skor, url).
    found = []
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={"action": "query", "generator": "search",
                    "gsrsearch": translated, "gsrnamespace": 6,
                    "gsrlimit": 20, "prop": "imageinfo",
                    "iiprop": "url", "iiurlwidth": 800,
                    "format": "json", "formatversion": 2},
            headers={**SEARCH_HEADERS,
                     "Api-User-Agent": "RakunAswBot/1.0 (Telegram bot)"},
            timeout=30,
        )
        r.raise_for_status()
        for p in r.json().get("query", {}).get("pages", []):
            title = p.get("title", "")
            name = title[5:] if title.startswith("File:") else title
            name = re.sub(r"\.[a-zA-Z0-9]+$", "", name).replace("_", " ")
            ii = (p.get("imageinfo") or [{}])[0]
            url = ii.get("thumburl") or ii.get("url")
            if not url:
                continue
            score = relevance_score(name, "", query_words, require_all)
            found.append((score, url))
    except Exception as e:
        print(f"Commons search failed for '{translated}': {e}", flush=True)
    return found

def pexels_image_urls(translated, query_words, require_all=False):
    # Pexels: foto stok gratis berkualitas, tag rapi. Butuh API key gratis.
    # Mengembalikan list (skor, url). Skor = relevansi alt text, dengan
    # Tanpa lantai skor: foto harus relevan beneran (alt mengandung
    # kata keyword), supaya nama orang tidak dapat foto stok generik.
    found = []
    if not PEXELS_API_KEY:
        return found
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": translated, "per_page": 15},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=30,
        )
        r.raise_for_status()
        for p in r.json().get("photos", []):
            alt = p.get("alt") or ""
            src = (p.get("src") or {}).get("large") or (p.get("src") or {}).get("original")
            if not src:
                continue
            score = relevance_score(alt, "", query_words, require_all)
            found.append((score, src))
    except Exception as e:
        print(f"Pexels search failed for '{translated}': {e}", flush=True)
    return found

def pixabay_image_urls(translated, query_words, require_all=False):
    # Pixabay: foto stok gratis, safesearch=true biar tetap SFW.
    # Butuh API key gratis. Mengembalikan list (skor, url).
    found = []
    if not PIXABAY_API_KEY:
        return found
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={"key": PIXABAY_API_KEY, "q": translated,
                    "image_type": "photo", "per_page": 15,
                    "safesearch": "true"},
            timeout=30,
        )
        r.raise_for_status()
        for h in r.json().get("hits", []):
            tags = (h.get("tags") or "").replace(",", " ")
            url = h.get("largeImageURL") or h.get("webformatURL")
            if not url:
                continue
            score = relevance_score("", tags, query_words, require_all)
            found.append((score, url))
    except Exception as e:
        print(f"Pixabay search failed for '{translated}': {e}", flush=True)
    return found

def unsplash_image_urls(translated, query_words, require_all=False):
    # Unsplash: foto stok gratis berkualitas. Butuh access key gratis.
    # Mengembalikan list (skor, url).
    found = []
    if not UNSPLASH_ACCESS_KEY:
        return found
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": translated, "per_page": 15},
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            timeout=30,
        )
        r.raise_for_status()
        for it in r.json().get("results", []):
            alt = (it.get("alt_description") or "") + " " + (it.get("description") or "")
            tags = " ".join((t or {}).get("title", "") for t in it.get("tags", []))
            url = ((it.get("urls") or {}).get("regular")
                   or (it.get("urls") or {}).get("full"))
            if not url:
                continue
            score = relevance_score(alt, tags, query_words, require_all)
            found.append((score, url))
    except Exception as e:
        print(f"Unsplash search failed for '{translated}': {e}", flush=True)
    return found

def brave_image_urls(translated, query_words, require_all=False):
    # Brave Search API: pencarian gambar web umum (pengganti Bing yang
    # lebih rapi). Butuh API key gratis. Skor murni relevansi (tanpa
    # lantai) karena hasilnya gambar web umum, bukan stok terkurasi.
    # Mengembalikan list (skor, url).
    found = []
    if not BRAVE_API_KEY:
        return found
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/images/search",
            params={"q": translated, "count": 15},
            headers={"X-Subscription-Token": BRAVE_API_KEY,
                     "Accept": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        for it in r.json().get("results", []):
            title = it.get("title") or ""
            props = it.get("properties") or {}
            url = props.get("url") or (it.get("thumbnail") or {}).get("src")
            if not url:
                continue
            score = relevance_score(title, "", query_words, require_all)
            found.append((score, url))
    except Exception as e:
        print(f"Brave search failed for '{translated}': {e}", flush=True)
    return found

def serpapi_image_urls(translated, query_words, require_all=False):
    # SerpAPI Google Images: hasil Google Images beneran sebagai JSON.
    # Butuh API key gratis (250 pencarian/bulan, maks 50/jam). Key dibaca
    # dari env SERPAPI_API_KEY. Skor murni relevansi judul (tanpa lantai),
    # supaya foto yang tidak relevan tidak lolos. Mengembalikan list
    # (skor, url). Pakai "original" (URL asli ukuran penuh) kalau ada,
    # kalau tidak pakai "thumbnail".
    found = []
    if not SERPAPI_API_KEY:
        return found
    try:
        r = requests.get(
            "https://serpapi.com/search",
            params={"engine": "google_images", "q": translated,
                    "api_key": SERPAPI_API_KEY, "num": 15},
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()
        if "error" in d:
            print(f"SerpAPI error for '{translated}': {d['error']}", flush=True)
            return found
        for it in d.get("images_results", []):
            title = it.get("title") or ""
            url = it.get("original") or it.get("thumbnail")
            if not url:
                continue
            score = relevance_score(title, "", query_words, require_all)
            found.append((score, url))
    except Exception as e:
        print(f"SerpAPI search failed for '{translated}': {e}", flush=True)
    return found

def search_image_urls(keyword, limit=10):
    # ATURAN AKURASI (pelajaran dari 4x hasil ngaco):
    # 1. Jangan pernah cari pakai keyword Indonesia mentah. Openverse
    #    cocok fuzzy ("anjing" -> pesawat bernomor ANJ), Bing ngaco
    #    ("pohon" -> poster film / pistol). Selalu pakai keyword
    #    terjemahan Inggris.
    # 2. Setiap kandidat dinilai dari judul/tag-nya. Skor 0 = dibuang,
    #    jangan pernah dikirim. Kata harus cocok UTUH, bukan substring,
    #    dan judul lebih penting dari tag (70/30). Ini yang bikin foto
    #    bunga lolos untuk "gta 6": tag-nya mengandung substring "gta".
    # 3. Safebooru (tag) paling akurat, lalu Wikipedia (gambar utama
    #    artikel topiknya, misal "gta 6" -> cover Grand Theft Auto VI),
    #    lalu Wikimedia Commons (banyak foto beneran), lalu pinimg,
    #    lalu sisanya. Safebooru juga dipakai untuk nama karakter anime
    #    ("kurumi" -> gambar Tokisaki Kurumi), karena pencarian teks
    #    biasa untuk nama karakter malah mengembalikan foto orang.
    # 4. Angka ikut dihitung ("gta 6" beda dengan "gta 5").
    # 5. Kalau tidak ada satu pun yang cocok, kembalikan daftar kosong
    #    supaya bot menjawab "nggak ketemu", bukan mengirim gambar acak.
    translated = translate_keyword(keyword)
    query_words = [w for w in translated.lower().split()
                   if len(w) > 2 or w.isdigit()]
    # Nama orang (misal "emi fukada", "kurumi"): semua kata keyword harus
    # muncul di judul/tag kandidat, supaya "emi" saja (EMI keuangan) tidak
    # lolos dan foto generik tidak lolos. Syaratnya sama dengan probe
    # Safebooru: ada kata, tanpa angka, dan tidak semua kata umum.
    _tw = translated.lower().split()
    require_all = bool(_tw) and not any(w.isdigit() for w in _tw) \
        and not all(w in GENERAL_WORDS for w in _tw)
    scored = []      # (skor, url) untuk semua kandidat
    seen = set()

    def add(url, score):
        url = (url or "").strip().replace("&amp;", "&")
        if not url.startswith("http") or url in seen:
            return
        if any(b in url.lower() for b in SEARCH_BAD_HOSTS):
            return
        seen.add(url)
        scored.append((score, url))

    # 0) Safebooru dulu, karena hasil tag sudah pasti sesuai, skor 1.10.
    #    Dua kasus:
    #    a) keyword mengandung "anime" -> pakai tag yang dipetakan
    #       ("berbaju merah" -> red_dress). Kalau tidak ada yang bisa
    #       dipetakan, pakai kata sisanya sebagai satu tag
    #       ("anime kurumi" -> tag kurumi).
    #    b) keyword berupa nama, bukan kata umum ("kurumi", "rem",
    #       "tokisaki kurumi") -> coba sebagai satu tag (gabung pakai
    #       underscore). Kalau bukan nama karakter anime, Safebooru
    #       mengembalikan kosong, jadi hasil umum tidak rusak.
    #       Dilewatkan kalau ada angka di keyword ("gta 6" itu game)
    #       atau kalau semua katanya kata umum ("anjing" -> dog).
    sb_queries = []
    if "anime" in keyword.lower():
        tags = keyword_to_safebooru_tags(keyword)
        if tags:
            sb_queries = [" ".join(tags)]
        else:
            rest = [w for w in translated.lower().split() if w != "anime"]
            if rest:
                sb_queries = ["_".join(rest)]
    else:
        words = translated.lower().split()
        if words and not any(w.isdigit() for w in words) \
                and not all(w in GENERAL_WORDS for w in words):
            sb_queries = ["_".join(words)]
    for it in safebooru_posts(sb_queries):
        add(it.get("file_url"), 1.10)
    # 0.5) Wikipedia: gambar utama artikel topiknya. Skor 1.05, di bawah
    #      Safebooru tapi di atas Openverse/Bing, karena gambarnya sudah
    #      dikurasi jadi gambar topik itu sendiri.
    wiki_url = wikipedia_image_url(translated)
    if wiki_url:
        add(wiki_url, 1.05)
    # 0.6) Wikimedia Commons: banyak foto beneran yang terdeskripsi baik.
    for score, url in commons_image_urls(translated, query_words, require_all):
        add(url, score)
    # 0.7) SerpAPI Google Images: hasil Google Images beneran. Ditaruh
    #      di sini (setelah Commons, sebelum Openverse/Bing) karena
    #      hasilnya paling relevan untuk keyword umum dan nama orang.
    #      Gratis 250 pencarian/bulan; kalau kuota habis atau key belum
    #      diset, fungsi ini mengembalikan kosong dan sumber lain tetap
    #      jalan, jadi /search tidak rusak.
    for score, url in serpapi_image_urls(translated, query_words, require_all):
        add(url, score)
    # 0.7b) Foto stok gratis (Pexels, Pixabay, Unsplash) dan Brave Search
    #      API DINONAKTIFKAN 2026-11-16 atas permintaan Taro ("hapus dulu").
    #      Fungsi-fungsinya tetap ada di bawah (pexels/pixabay/unsplash/
    #      brave_image_urls) supaya gampang diaktifkan lagi. Catatan:
    #      DuckDuckGo dan Yandex sempat dites sebagai pengganti, tapi
    #      keduanya tidak bisa dipakai dari komputer ini (DDG i.js kena
    #      bot-challenge, Yandex kena captcha + koneksi putus).
    # for score, url in pexels_image_urls(translated, query_words, require_all):
    #     add(url, score)
    # for score, url in pixabay_image_urls(translated, query_words, require_all):
    #     add(url, score)
    # for score, url in unsplash_image_urls(translated, query_words, require_all):
    #     add(url, score)
    # for score, url in brave_image_urls(translated, query_words, require_all):
    #     add(url, score)
    # 1) Openverse, HANYA dengan keyword terjemahan Inggris.
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": translated, "page_size": 20},
            timeout=30,
        )
        r.raise_for_status()
        for it in r.json().get("results", []):
            title = it.get("title") or ""
            tags = " ".join(t.get("name", "") for t in it.get("tags", []))
            score = relevance_score(title, tags, query_words, require_all)
            add(it.get("url"), score)
    except Exception as e:
        print(f"Openverse search failed for '{translated}': {e}", flush=True)
    # 2) Cadangan Bing, juga hanya dengan keyword terjemahan Inggris.
    if len([s for s, _ in scored if s > 0]) < 3:
        try:
            r = requests.get(
                "https://www.bing.com/images/search",
                params={"q": translated},
                headers=SEARCH_HEADERS,
                timeout=30,
            )
            r.raise_for_status()
            murls = re.findall(r'murl&quot;:&quot;(https://[^&]+)', r.text)
            titles = re.findall(r'&quot;t&quot;:&quot;([^&]{0,120})', r.text)
            for i, u in enumerate(murls):
                title = titles[i] if i < len(titles) else ""
                score = relevance_score(title, "", query_words, require_all)
                add(u, score)
        except Exception as e:
            print(f"Bing search failed for '{translated}': {e}", flush=True)
    # Urutkan berdasarkan skor (bonus kecil untuk pinimg), acak di dalam
    # skor yang sama supaya hasilnya bervariasi tapi tetap relevan.
    def sort_key(item):
        score, url = item
        if "pinimg.com" in url:
            score += 0.05
        return score

    good = [(s, u) for s, u in scored if s > 0]
    good.sort(key=sort_key, reverse=True)
    ranked = []
    i = 0
    while i < len(good):
        j = i
        while j < len(good) and good[j][0] == good[i][0]:
            j += 1
        group = good[i:j]
        random.shuffle(group)
        ranked.extend(group)
        i = j
    if ranked:
        return [u for _, u in ranked[:limit]]
    # Tidak ada yang cocok: kembalikan kosong, bot akan menjawab
    # "nggak ketemu" daripada mengirim gambar acak yang salah.
    return []

def api_search_image_urls(keyword, limit=10):
    # Cari lewat API lokal (rakun-image-search, port 8000) supaya /search
    # di bot memakai API yang sama. Kalau API mati/tidak menjawab,
    # kembalikan None supaya do_search jatuh kembali ke pencarian langsung.
    # Kalau API menjawab tapi tidak ada hasil, kembalikan [] (bukan None).
    try:
        r = requests.get(
            "http://127.0.0.1:8000/search",
            params={"q": keyword, "limit": limit},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("results") or []
    except Exception as e:
        print(f"Search API lokal gagal untuk '{keyword}': {e}", flush=True)
        return None

def do_search(chat_id, message_id, keyword, sender_id):
    status_msg_id = None
    try:
        status = api(
            "sendMessage",
            chat_id=chat_id,
            text=f"Lagi cari gambar {keyword}, bentar ya...",
            reply_to_message_id=message_id,
        )
        status_msg_id = status.get("message_id")
        # Coba API lokal dulu, kalau API-nya mati pakai pencarian langsung.
        urls = api_search_image_urls(keyword)
        if urls is None:
            print(f"Search '{keyword}': API lokal tidak menjawab, pakai pencarian langsung", flush=True)
            urls = search_image_urls(keyword)
        else:
            print(f"Search '{keyword}': lewat API lokal, {len(urls)} hasil", flush=True)
        if not urls:
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Nggak ketemu gambarnya, coba keyword lain ya.",
                reply_to_message_id=message_id,
            )
            return
        # Kalau keyword mengandung warna (misal "berbaju merah"), bot tidak
        # langsung kirim gambar pertama. Bot download beberapa kandidat lalu
        # pilih yang warna merahnya paling banyak, supaya hasilnya akurat.
        # Kalau tidak ada warna di keyword, kirim gambar pertama yang berhasil.
        color = find_color_word(keyword)
        best = None  # (score, data, ext, ctype, url)
        checked = 0
        for url in urls:
            if color and checked >= 5:
                break
            # Download gambar. Kalau kena 429 (rate limit, misal Wikimedia
            # Commons membatasi IP ini), tunggu sebentar lalu coba lagi
            # URL yang sama sampai 3x sebelum pindah ke URL berikut.
            # Ini penting karena untuk nama orang kadang cuma ada 1 kandidat
            # yang benar, dan kalau download-nya gagal sekali bot langsung
            # bilang "gagal download" padahal coba lagi bisa berhasil.
            resp = None
            for attempt in range(3):
                try:
                    resp = requests.get(url, headers=SEARCH_HEADERS, timeout=30)
                    resp.raise_for_status()
                    break
                except Exception as e:
                    status_code = getattr(getattr(e, "response", None), "status_code", None)
                    if status_code == 429 and attempt < 2:
                        wait = 3 * (attempt + 1)
                        print(f"Search 429 untuk {url[:80]}, tunggu {wait} dtk lalu coba lagi", flush=True)
                        time.sleep(wait)
                        continue
                    print(f"Search image try failed for {url[:100]}: {e}", flush=True)
                    resp = None
                    break
            if resp is None:
                continue
            try:
                ctype = resp.headers.get("content-type", "")
                if "image" not in ctype:
                    continue
                data = resp.content
                if not data or len(data) > MAX_PHOTO_BYTES:
                    continue
                ext = "jpg"
                if "png" in ctype:
                    ext = "png"
                elif "webp" in ctype:
                    ext = "webp"
                elif "gif" in ctype:
                    ext = "gif"
                if color:
                    score = color_fraction(data, color)
                    checked += 1
                    print(f"Search color check '{keyword}' ({color}={score:.2f}) {url[:100]}", flush=True)
                    if best is None or score > best[0]:
                        best = (score, data, ext, ctype, url)
                    # Warnanya sudah dominan, langsung pakai gambar ini
                    if score >= 0.30:
                        break
                else:
                    best = (1.0, data, ext, ctype, url)
                    break
            except Exception as e:
                print(f"Search image try failed for {url[:100]}: {e}", flush=True)
                continue
        if best is not None:
            _, data, ext, ctype, url = best
            photo_resp = requests.post(
                f"{BASE}/sendPhoto",
                data={"chat_id": chat_id, "reply_to_message_id": message_id, "caption": keyword},
                files={"photo": (f"search.{ext}", data, ctype)},
                timeout=120,
            )
            photo_resp.raise_for_status()
            pdata = photo_resp.json()
            if not pdata.get("ok"):
                raise RuntimeError(f"sendPhoto not ok: {pdata}")
            print(f"Sent search photo for '{keyword}' from {url[:100]} to message {message_id} from {sender_id}", flush=True)
            # Fotonya udah muncul, hapus pesan "Lagi cari gambar..." tadi
            if status_msg_id:
                try:
                    api("deleteMessage", chat_id=chat_id, message_id=status_msg_id)
                except Exception as e:
                    print(f"Failed to delete search status message: {e}", flush=True)
            return
        # Kalau gagal, hapus juga pesan statusnya biar nggak numpuk
        if status_msg_id:
            try:
                api("deleteMessage", chat_id=chat_id, message_id=status_msg_id)
            except Exception as e:
                print(f"Failed to delete search status message: {e}", flush=True)
        api(
            "sendMessage",
            chat_id=chat_id,
            text="Gagal download gambarnya, coba keyword lain ya.",
            reply_to_message_id=message_id,
        )
    except Exception as e:
        print(f"Search error for '{keyword}' message {message_id}: {e}", flush=True)
        if status_msg_id:
            try:
                api("deleteMessage", chat_id=chat_id, message_id=status_msg_id)
            except Exception:
                pass
        try:
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Gagal cari gambarnya, coba lagi nanti ya.",
                reply_to_message_id=message_id,
            )
        except Exception:
            pass

# Game dadu /dice: berlaku untuk siapa saja di grup. Tiap user punya
# saldo koin sendiri (disimpan di balances.json, saldo awal 1000).
# /dice mengirim 1 dadu animasi asli Telegram (sendDice, 🎲 berputar
# lalu berhenti sendiri), nilainya dibaca dari hasil kirimnya. Dadu
# kedua diacak bot, jadi total tetap 2 dadu (2-12).
# Total 2-6 kalah, 7 seri, 8-12 menang. Jumlah menang/kalah = (total - 7) x 50
# koin, jadi total 12 = +250, total 2 = -250. Kalau saldo habis (<= 0),
# bot kasih modal baru 1000 koin. /balance untuk cek saldo.
BALANCE_FILE = os.path.join(os.path.dirname(__file__), "balances.json")
BALANCE_LOCK = threading.Lock()
START_BALANCE = 1000
DICE_FACES = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]

def _load_balances():
    try:
        with open(BALANCE_FILE) as f:
            data = json.load(f)
            return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_balances(balances):
    try:
        with open(BALANCE_FILE, "w") as f:
            json.dump(balances, f)
    except Exception as e:
        print(f"Failed to save balances: {e}", flush=True)

def get_balance(user_id):
    with BALANCE_LOCK:
        return _load_balances().get(str(user_id), START_BALANCE)

def change_balance(user_id, delta):
    with BALANCE_LOCK:
        balances = _load_balances()
        new = balances.get(str(user_id), START_BALANCE) + delta
        balances[str(user_id)] = new
        _save_balances(balances)
        return new

def set_balance(user_id, value):
    with BALANCE_LOCK:
        balances = _load_balances()
        balances[str(user_id)] = value
        _save_balances(balances)

# Game monster (mirip Pokemon) di bot Telegram.
# Awal game: pemain TIDAK punya monster. Tiap pemain baru dikasih 1 Pokeball.
# Monster dicari sendiri (/catch) dan ditangkap pakai Pokeball.
# Makin bagus Pokeball, makin bagus monster yang bisa ditangkap:
# tiap ball punya batas rarity yang bisa ditangkap + peluang lebih tinggi.
# Koin (dari /dice & menang battle) bisa buat beli Pokeball di /toko.
# /hadiah kasih 1 Pokeball gratis tiap hari supaya tidak kehabisan.
# Battle PvP: /battle (reply ke pesan lawan, atau /battle @username).
MONSTER_FILE = os.path.join(os.path.dirname(__file__), "monsters.json")
MONSTER_LOCK = threading.Lock()
BATTLE_LOCK = threading.Lock()
USERNAME_FILE = os.path.join(os.path.dirname(__file__), "usernames.json")
MONSTER_MAX_TEAM = 8

RARITY_ORDER = ["Common", "Uncommon", "Rare", "Epic"]
RARITY_EMOJI = {"Common": "⚪", "Uncommon": "🟢", "Rare": "🔵", "Epic": "🟣"}

BALLS = {
    "pokeball": {"name": "Pokeball", "emoji": "🔴", "price": 50, "max_rarity": "Uncommon", "mult": 1.0},
    "greatball": {"name": "Great Ball", "emoji": "🔵", "price": 150, "max_rarity": "Rare", "mult": 1.5},
    "ultraball": {"name": "Ultra Ball", "emoji": "🟡", "price": 400, "max_rarity": "Epic", "mult": 2.0},
    "masterball": {"name": "Master Ball", "emoji": "🟣", "price": 2000, "max_rarity": "Epic", "mult": 99.0},
}
BALL_ORDER = ["pokeball", "greatball", "ultraball", "masterball"]

CATCH_BASE = {"Common": 0.75, "Uncommon": 0.60, "Rare": 0.40, "Epic": 0.20}

TYPE_CHART = {
    ("Api", "Daun"): 2.0, ("Api", "Air"): 0.5, ("Api", "Api"): 0.5,
    ("Air", "Api"): 2.0, ("Air", "Daun"): 0.5, ("Air", "Air"): 0.5, ("Air", "Listrik"): 0.5,
    ("Daun", "Air"): 2.0, ("Daun", "Api"): 0.5, ("Daun", "Daun"): 0.5,
    ("Listrik", "Air"): 2.0, ("Listrik", "Daun"): 0.5, ("Listrik", "Listrik"): 0.5,
}

MOVES = {
    "Tackle": ("Normal", 35),
    "Cakar": ("Normal", 40),
    "Tubruk": ("Normal", 55),
    "Ember": ("Api", 45),
    "Semburan Api": ("Api", 65),
    "Gelembung": ("Air", 45),
    "Jet Air": ("Air", 65),
    "Daun Tajam": ("Daun", 45),
    "Cambuk Daun": ("Daun", 65),
    "Percikan": ("Listrik", 45),
    "Sengat Listrik": ("Listrik", 65),
}

SPECIES = {
    "puffball": {"name": "Puffball", "type": "Normal", "rarity": "Common", "hp": 66, "atk": 10, "def": 12, "moves": ["Tackle", "Cakar"], "evo": (12, "pufflord")},
    "sparkit": {"name": "Sparkit", "type": "Listrik", "rarity": "Common", "hp": 60, "atk": 12, "def": 9, "moves": ["Percikan", "Tackle"], "evo": (10, "voltax")},
    "flametail": {"name": "Flametail", "type": "Api", "rarity": "Common", "hp": 62, "atk": 12, "def": 9, "moves": ["Ember", "Cakar"], "evo": (12, "blazetail")},
    "splashlet": {"name": "Splashlet", "type": "Air", "rarity": "Common", "hp": 64, "atk": 11, "def": 10, "moves": ["Gelembung", "Tackle"], "evo": (12, "splashking")},
    "emberfox": {"name": "Emberfox", "type": "Api", "rarity": "Uncommon", "hp": 64, "atk": 13, "def": 10, "moves": ["Ember", "Cakar"], "evo": (10, "pyrofox")},
    "aquafin": {"name": "Aquafin", "type": "Air", "rarity": "Uncommon", "hp": 68, "atk": 12, "def": 11, "moves": ["Gelembung", "Tackle"], "evo": (10, "hydrafin")},
    "leafrog": {"name": "Leafrog", "type": "Daun", "rarity": "Uncommon", "hp": 66, "atk": 12, "def": 11, "moves": ["Daun Tajam", "Tackle"], "evo": (10, "thornfrog")},
    "rocklet": {"name": "Rocklet", "type": "Normal", "rarity": "Uncommon", "hp": 68, "atk": 13, "def": 13, "moves": ["Tackle", "Cakar"], "evo": (12, "boulderax")},
    "pyrofox": {"name": "Pyrofox", "type": "Api", "rarity": "Rare", "hp": 88, "atk": 18, "def": 13, "moves": ["Semburan Api", "Cakar"], "evo": None},
    "hydrafin": {"name": "Hydrafin", "type": "Air", "rarity": "Rare", "hp": 92, "atk": 17, "def": 15, "moves": ["Jet Air", "Tackle"], "evo": None},
    "thornfrog": {"name": "Thornfrog", "type": "Daun", "rarity": "Rare", "hp": 90, "atk": 17, "def": 14, "moves": ["Cambuk Daun", "Tackle"], "evo": None},
    "voltax": {"name": "Voltax", "type": "Listrik", "rarity": "Rare", "hp": 86, "atk": 18, "def": 12, "moves": ["Sengat Listrik", "Tackle"], "evo": None},
    "pufflord": {"name": "Pufflord", "type": "Normal", "rarity": "Rare", "hp": 98, "atk": 15, "def": 17, "moves": ["Tubruk", "Cakar"], "evo": None},
    "blazetail": {"name": "Blazetail", "type": "Api", "rarity": "Rare", "hp": 90, "atk": 18, "def": 13, "moves": ["Semburan Api", "Cakar"], "evo": None},
    "splashking": {"name": "Splashking", "type": "Air", "rarity": "Rare", "hp": 94, "atk": 17, "def": 15, "moves": ["Jet Air", "Tackle"], "evo": None},
    "boulderax": {"name": "Boulderax", "type": "Normal", "rarity": "Rare", "hp": 96, "atk": 17, "def": 18, "moves": ["Tubruk", "Cakar"], "evo": None},
    "lunadrake": {"name": "Lunadrake", "type": "Listrik", "rarity": "Epic", "hp": 105, "atk": 20, "def": 16, "moves": ["Sengat Listrik", "Percikan"], "evo": None},
}

# Monster liar yang sedang menunggu ditangkap, per user (di memori saja).
WILD = {}
# Battle yang sedang berjalan, key = battle id pendek.
BATTLES = {}
# username (huruf kecil) -> user id, supaya /battle @username bisa dicari.
USERNAME_MAP = {}

def mon_load():
    try:
        with open(MONSTER_FILE) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def mon_save(data):
    try:
        with open(MONSTER_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Failed to save monsters: {e}", flush=True)

def mon_get_user(data, user_id):
    uid = str(user_id)
    u = data.get(uid)
    if not u:
        u = {"balls": {"pokeball": 1}, "team": [], "active": None, "last_daily": "", "last_catch": 0}
        data[uid] = u
    u.setdefault("balls", {})
    for b in BALL_ORDER:
        u["balls"].setdefault(b, 0)
    u.setdefault("team", [])
    return u

def mon_max_hp(species_key, level):
    return SPECIES[species_key]["hp"] + (level - 1) * 3

def mon_atk(species_key, level):
    return SPECIES[species_key]["atk"] + (level - 1)

def mon_def(species_key, level):
    return SPECIES[species_key]["def"] + (level - 1)

def mon_xp_to_next(level):
    return level * 50

def mon_add_xp(mon, amount):
    events = []
    mon["xp"] = mon.get("xp", 0) + amount
    while mon["xp"] >= mon_xp_to_next(mon["level"]):
        mon["xp"] -= mon_xp_to_next(mon["level"])
        mon["level"] += 1
        events.append(f"Naik ke Lv {mon['level']}!")
        mon["hp"] = mon_max_hp(mon["species"], mon["level"])
        sp = SPECIES[mon["species"]]
        evo = sp.get("evo")
        if evo and mon["level"] >= evo[0]:
            new_species = evo[1]
            mon["species"] = new_species
            mon["hp"] = mon_max_hp(new_species, mon["level"])
            events.append(f"BEREVOLUSI menjadi {SPECIES[new_species]['name']}!")
    return events

def mon_active(data, user_id):
    u = data.get(str(user_id))
    if not u or not u.get("team"):
        return None, None
    idx = u.get("active")
    if idx is None or idx >= len(u["team"]):
        idx = 0
        u["active"] = 0
    return u["team"][idx], idx

def mon_hp_bar(hp, max_hp):
    if max_hp <= 0:
        return "▓▓▓▓▓▓▓▓▓▓"
    filled = int(round(hp / max_hp * 10))
    filled = max(0, min(10, filled))
    return "🟩" * filled + "⬜" * (10 - filled)

def mon_desc(mon):
    sp = SPECIES[mon["species"]]
    max_hp = mon_max_hp(mon["species"], mon["level"])
    return f"{RARITY_EMOJI[sp['rarity']]} {sp['name']} (Lv {mon['level']}, {sp['type']}, {sp['rarity']}) HP {mon.get('hp', max_hp)}/{max_hp}"

def mon_ball_can_catch(ball_key, rarity):
    max_r = BALLS[ball_key]["max_rarity"]
    return RARITY_ORDER.index(rarity) <= RARITY_ORDER.index(max_r)

def mon_catch_chance(rarity, ball_key, wild_level):
    if ball_key == "masterball":
        return 1.0
    base = CATCH_BASE[rarity]
    mult = BALLS[ball_key]["mult"]
    lvl_adj = 0.02 * (5 - wild_level)
    return max(0.05, min(0.95, base * mult + lvl_adj))

def mon_wild_level(user_id):
    data = mon_load()
    u = data.get(str(user_id), {})
    best = 0
    for m in u.get("team", []):
        best = max(best, m.get("level", 1))
    if best <= 0:
        return random.randint(1, 5)
    return max(1, min(20, best + random.randint(-2, 3)))

def mon_spawn_wild(user_id):
    r = random.random()
    if r < 0.55:
        rarity = "Common"
    elif r < 0.85:
        rarity = "Uncommon"
    elif r < 0.98:
        rarity = "Rare"
    else:
        rarity = "Epic"
    pool = [k for k, v in SPECIES.items() if v["rarity"] == rarity]
    species = random.choice(pool)
    level = mon_wild_level(user_id)
    return {"species": species, "level": level, "rarity": rarity, "time": time.time()}

def mon_calc_damage(atk_species, atk_level, move_name, def_species, def_level):
    mtype, power = MOVES[move_name]
    atk = mon_atk(atk_species, atk_level)
    dfn = mon_def(def_species, def_level)
    dftype = SPECIES[def_species]["type"]
    mult = TYPE_CHART.get((mtype, dftype), 1.0)
    dmg = int(power * 0.45 * (atk / max(1, dfn)) * mult * random.uniform(0.85, 1.0))
    return max(1, dmg), mult

def mon_balls_text(balls):
    parts = []
    for b in BALL_ORDER:
        n = balls.get(b, 0)
        if n > 0:
            parts.append(f"{BALLS[b]['emoji']} {BALLS[b]['name']} x{n}")
    return ", ".join(parts) if parts else "tidak ada Pokeball"

def mon_send(chat_id, text, reply_to=None, markup=None):
    kwargs = {"chat_id": chat_id, "text": text}
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to
    if markup:
        kwargs["reply_markup"] = markup
    return api("sendMessage", **kwargs)

def mon_kb(rows):
    return {"inline_keyboard": rows}

def mon_btn(text, data):
    return {"text": text, "callback_data": data}

def do_mon(chat_id, message_id, sender_id, sender_name):
    with MONSTER_LOCK:
        data = mon_load()
        u = mon_get_user(data, sender_id)
        mon_save(data)
    team = u.get("team", [])
    balls = u.get("balls", {})
    if not team:
        text = (
            f"🎒 {sender_name}, kamu belum punya monster!\n"
            f"Pokeball kamu: {mon_balls_text(balls)}\n\n"
            f"Ketik /catch buat cari monster liar, terus tangkap pakai Pokeball.\n"
            f"Makin bagus Pokeball-nya, makin bagus monster yang bisa ditangkap.\n"
            f"Beli Pokeball di /toko, klaim gratis harian di /hadiah."
        )
        mon_send(chat_id, text, reply_to=message_id)
        return
    active, idx = mon_active(data, sender_id)
    lines = [f"🎒 Monster {sender_name}:"]
    for i, m in enumerate(team):
        mark = "⭐ " if i == idx else ""
        lines.append(f"{mark}{mon_desc(m)}")
    lines.append(f"\nPokeball: {mon_balls_text(balls)}")
    lines.append(f"\nAktif: {SPECIES[active['species']]['name']} (Lv {active['level']})")
    lines.append("Perintah: /catch cari monster, /team ganti monster, /battle tantang orang, /heal pulihkan HP, /toko beli Pokeball.")
    mon_send(chat_id, "\n".join(lines), reply_to=message_id)

def do_catch(chat_id, message_id, sender_id, sender_name):
    now = time.time()
    with MONSTER_LOCK:
        data = mon_load()
        u = mon_get_user(data, sender_id)
        if now - u.get("last_catch", 0) < 20:
            sisa = int(20 - (now - u.get("last_catch", 0)))
            mon_send(chat_id, f"Tunggu dulu, coba /catch lagi {sisa} detik lagi.", reply_to=message_id)
            return
        u["last_catch"] = now
        mon_save(data)
    wild = WILD.get(sender_id)
    if wild:
        sp = SPECIES[wild["species"]]
        text = (
            f"🌿 Monster liar masih menunggu!\n"
            f"{RARITY_EMOJI[wild['rarity']]} {sp['name']} (Lv {wild['level']}, {sp['type']}, {wild['rarity']})\n"
            f"Pilih Pokeball buat menangkapnya:"
        )
    else:
        wild = mon_spawn_wild(sender_id)
        WILD[sender_id] = wild
        sp = SPECIES[wild["species"]]
        text = (
            f"🌿 Monster liar muncul!\n"
            f"{RARITY_EMOJI[wild['rarity']]} {sp['name']} (Lv {wild['level']}, {sp['type']}, {wild['rarity']})\n"
            f"Jurus: {', '.join(sp['moves'])}\n"
            f"Pilih Pokeball buat menangkapnya:"
        )
    with MONSTER_LOCK:
        data = mon_load()
        u = mon_get_user(data, sender_id)
        balls = u.get("balls", {})
    rows = []
    row = []
    for b in BALL_ORDER:
        n = balls.get(b, 0)
        if n > 0:
            row.append(mon_btn(f"{BALLS[b]['emoji']} {BALLS[b]['name']} x{n}", f"mon:ct:{b}"))
            if len(row) == 2:
                rows.append(row)
                row = []
    if row:
        rows.append(row)
    rows.append([mon_btn("🏃 Kabur", "mon:flee")])
    if not any(balls.get(b, 0) > 0 for b in BALL_ORDER):
        text += "\n\nKamu tidak punya Pokeball! Beli di /toko atau klaim gratis di /hadiah."
        mon_send(chat_id, text, reply_to=message_id)
        return
    mon_send(chat_id, text, reply_to=message_id, markup=mon_kb(rows))

def do_toko(chat_id, message_id, sender_id, sender_name):
    with MONSTER_LOCK:
        data = mon_load()
        u = mon_get_user(data, sender_id)
        balls = u.get("balls", {})
        mon_save(data)
    coins = get_balance(sender_id)
    lines = [f"🏪 Toko Pokeball — Saldo kamu: {coins} koin", "", "Pokeball kamu: " + mon_balls_text(balls), ""]
    for b in BALL_ORDER:
        info = BALLS[b]
        lines.append(f"{info['emoji']} {info['name']} — {info['price']} koin (tangkap sampai {info['max_rarity']})")
    lines.append("\nKetuk tombol buat beli:")
    rows = []
    row = []
    for b in BALL_ORDER:
        row.append(mon_btn(f"{BALLS[b]['emoji']} {BALLS[b]['name']} {BALLS[b]['price']}", f"mon:buy:{b}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    mon_send(chat_id, "\n".join(lines), reply_to=message_id, markup=mon_kb(rows))

def do_hadiah(chat_id, message_id, sender_id, sender_name):
    today = time.strftime("%Y-%m-%d")
    with MONSTER_LOCK:
        data = mon_load()
        u = mon_get_user(data, sender_id)
        if u.get("last_daily") == today:
            mon_send(chat_id, "Hadiah harian udah diklaim hari ini. Balik lagi besok ya!", reply_to=message_id)
            return
        u["last_daily"] = today
        u["balls"]["pokeball"] = u["balls"].get("pokeball", 0) + 1
        mon_save(data)
    mon_send(chat_id, "🎁 Hadiah harian: kamu dapat 1 🔴 Pokeball! Ketik /catch buat cari monster.", reply_to=message_id)

def do_team(chat_id, message_id, sender_id, sender_name):
    with MONSTER_LOCK:
        data = mon_load()
        u = mon_get_user(data, sender_id)
        mon_save(data)
    team = u.get("team", [])
    if not team:
        mon_send(chat_id, "Kamu belum punya monster. Ketik /catch buat cari monster liar!", reply_to=message_id)
        return
    active, idx = mon_active(data, sender_id)
    lines = ["🎒 Tim kamu (ketuk buat ganti monster aktif):"]
    rows = []
    for i, m in enumerate(team):
        mark = "⭐ " if i == idx else ""
        lines.append(f"{mark}{mon_desc(m)}")
        rows.append([mon_btn(f"{'⭐ ' if i == idx else ''}{SPECIES[m['species']]['name']} Lv {m['level']}", f"mon:tm:{i}")])
    mon_send(chat_id, "\n".join(lines), reply_to=message_id, markup=mon_kb(rows))

def do_heal(chat_id, message_id, sender_id, sender_name):
    with MONSTER_LOCK:
        data = mon_load()
        u = mon_get_user(data, sender_id)
        active, idx = mon_active(data, sender_id)
        if not active:
            mon_send(chat_id, "Kamu belum punya monster. Ketik /catch dulu!", reply_to=message_id)
            return
        max_hp = mon_max_hp(active["species"], active["level"])
        if active.get("hp", max_hp) >= max_hp:
            mon_send(chat_id, f"{SPECIES[active['species']]['name']} udah full HP!", reply_to=message_id)
            return
        active["hp"] = max_hp
        mon_save(data)
    mon_send(chat_id, f"💚 {SPECIES[active['species']]['name']} pulih full HP ({max_hp}/{max_hp})!", reply_to=message_id)

def mon_new_battle_id():
    return "%04x" % random.randint(0, 0xFFFF)

def mon_user_in_battle(user_id):
    with BATTLE_LOCK:
        for b in BATTLES.values():
            if b["state"] in ("waiting_accept", "active") and user_id in (b["p1"], b["p2"]):
                return b
    return None

def do_battle(chat_id, message_id, sender_id, sender_name, msg):
    target_id = None
    target_name = None
    reply = msg.get("reply_to_message")
    if reply and reply.get("from", {}).get("id"):
        target_id = reply["from"]["id"]
        target_name = reply["from"].get("first_name", "lawan")
    else:
        text = msg.get("text", "")
        parts = text.split()
        if len(parts) >= 2:
            uname = parts[1].lstrip("@").lower()
            target_id = USERNAME_MAP.get(uname)
            target_name = parts[1]
    if not target_id:
        mon_send(chat_id, "Tantang battle dengan reply ke pesan lawan, atau ketik /battle @username.", reply_to=message_id)
        return
    if target_id == sender_id:
        mon_send(chat_id, "Tidak bisa battle melawan diri sendiri!", reply_to=message_id)
        return
    with MONSTER_LOCK:
        data = mon_load()
        u1 = mon_get_user(data, sender_id)
        u2 = mon_get_user(data, target_id)
        mon_save(data)
    m1, _ = mon_active(data, sender_id)
    m2, _ = mon_active(data, target_id)
    if not m1:
        mon_send(chat_id, "Kamu belum punya monster! Ketik /catch dulu buat cari monster.", reply_to=message_id)
        return
    if not m2:
        mon_send(chat_id, f"{target_name} belum punya monster, jadi belum bisa di-battle.", reply_to=message_id)
        return
    if mon_user_in_battle(sender_id):
        mon_send(chat_id, "Kamu lagi ada battle yang belum selesai!", reply_to=message_id)
        return
    if mon_user_in_battle(target_id):
        mon_send(chat_id, f"{target_name} lagi ada battle yang belum selesai!", reply_to=message_id)
        return
    bid = mon_new_battle_id()
    with BATTLE_LOCK:
        BATTLES[bid] = {
            "id": bid, "chat_id": chat_id, "state": "waiting_accept",
            "p1": sender_id, "p2": target_id,
            "p1_name": sender_name, "p2_name": target_name,
            "m1": {"species": m1["species"], "level": m1["level"]},
            "m2": {"species": m2["species"], "level": m2["level"]},
            "hp1": mon_max_hp(m1["species"], m1["level"]),
            "hp2": mon_max_hp(m2["species"], m2["level"]),
            "pick1": None, "pick2": None, "round": 1,
            "last_action": time.time(), "message_id": None,
        }
    text = (
        f"⚔️ TANTANGAN BATTLE!\n"
        f"{sender_name} ({SPECIES[m1['species']]['name']} Lv {m1['level']}) menantang {target_name} ({SPECIES[m2['species']]['name']} Lv {m2['level']})!\n"
        f"{target_name}, terima tantangannya?"
    )
    markup = mon_kb([[mon_btn("✅ Terima", f"mon:acc:{bid}"), mon_btn("❌ Tolak", f"mon:dec:{bid}")]])
    sent = mon_send(chat_id, text, reply_to=message_id, markup=markup)
    with BATTLE_LOCK:
        if bid in BATTLES:
            BATTLES[bid]["message_id"] = sent.get("message_id")

def mon_battle_text(b):
    m1 = b["m1"]
    m2 = b["m2"]
    max1 = mon_max_hp(m1["species"], m1["level"])
    max2 = mon_max_hp(m2["species"], m2["level"])
    s1 = SPECIES[m1["species"]]
    s2 = SPECIES[m2["species"]]
    lines = [
        f"⚔️ BATTLE — Ronde {b['round']}",
        f"{b['p1_name']}: {s1['name']} Lv {m1['level']} ({s1['type']})",
        f"HP {b['hp1']}/{max1} {mon_hp_bar(b['hp1'], max1)}",
        f"{b['p2_name']}: {s2['name']} Lv {m2['level']} ({s2['type']})",
        f"HP {b['hp2']}/{max2} {mon_hp_bar(b['hp2'], max2)}",
        "",
    ]
    if b.get("log"):
        lines.append(b["log"])
        lines.append("")
    lines.append("Pilih jurus kamu:")
    return "\n".join(lines)

def mon_battle_kb(b):
    rows = []
    for player, mon in ((1, b["m1"]), (2, b["m2"])):
        sp = SPECIES[mon["species"]]
        row = []
        for i, mv in enumerate(sp["moves"]):
            row.append(mon_btn(f"{b['p1_name'] if player == 1 else b['p2_name']}: {mv}", f"mon:mv:{b['id']}:{player}:{i}"))
        rows.append(row)
    return mon_kb(rows)

def mon_end_battle(b, winner):
    loser = 2 if winner == 1 else 1
    w_id = b["p1"] if winner == 1 else b["p2"]
    l_id = b["p2"] if winner == 1 else b["p1"]
    w_name = b["p1_name"] if winner == 1 else b["p2_name"]
    l_name = b["p2_name"] if winner == 1 else b["p1_name"]
    w_mon = b["m1"] if winner == 1 else b["m2"]
    l_mon = b["m2"] if winner == 1 else b["m1"]
    events_w = []
    events_l = []
    with MONSTER_LOCK:
        data = mon_load()
        for uid, mon, amount in ((w_id, w_mon, 60), (l_id, l_mon, 25)):
            u = mon_get_user(data, uid)
            for m in u.get("team", []):
                if m["species"] == mon["species"] and m["level"] == mon["level"]:
                    ev = mon_add_xp(m, amount)
                    if uid == w_id:
                        events_w = ev
                    else:
                        events_l = ev
                    m["hp"] = mon_max_hp(m["species"], m["level"])
                    break
        mon_save(data)
    new_coins = change_balance(w_id, 100)
    w_sp = SPECIES[w_mon["species"]]
    l_sp = SPECIES[l_mon["species"]]
    lines = [
        f"🏆 {w_name} MENANG!",
        f"{w_sp['name']} (Lv {w_mon['level']}) mengalahkan {l_sp['name']} (Lv {l_mon['level']})!",
        f"{w_name} dapat +60 XP & +100 koin (saldo: {new_coins}). {l_name} dapat +25 XP.",
    ]
    for ev in events_w:
        lines.append(f"⭐ {w_sp['name']}: {ev}")
    for ev in events_l:
        lines.append(f"⭐ {l_sp['name']}: {ev}")
    lines.append("HP kedua monster sudah pulih penuh.")
    text = "\n".join(lines)
    try:
        api("editMessageText", chat_id=b["chat_id"], message_id=b["message_id"], text=text)
    except Exception:
        try:
            mon_send(b["chat_id"], text)
        except Exception:
            pass
    with BATTLE_LOCK:
        BATTLES.pop(b["id"], None)

def mon_resolve_round(b):
    order = []
    if b["m1"]["level"] >= b["m2"]["level"]:
        order = [1, 2]
    else:
        order = [2, 1]
    logs = []
    for attacker in order:
        if b["hp1"] <= 0 or b["hp2"] <= 0:
            break
        if attacker == 1:
            atk_mon, def_mon = b["m1"], b["m2"]
            atk_name, def_name = b["p1_name"], b["p2_name"]
            pick = b["pick1"]
            hp_key = "hp2"
        else:
            atk_mon, def_mon = b["m2"], b["m1"]
            atk_name, def_name = b["p2_name"], b["p1_name"]
            pick = b["pick2"]
            hp_key = "hp1"
        if not pick:
            continue
        dmg, mult = mon_calc_damage(atk_mon["species"], atk_mon["level"], pick, def_mon["species"], def_mon["level"])
        b[hp_key] = max(0, b[hp_key] - dmg)
        eff = ""
        if mult >= 2.0:
            eff = " Sangat efektif!"
        elif mult <= 0.5:
            eff = " Tidak begitu efektif..."
        logs.append(f"{atk_name} pakai {pick}, {def_name} -{dmg} HP.{eff}")
    b["pick1"] = None
    b["pick2"] = None
    if b["hp1"] <= 0 or b["hp2"] <= 0:
        b["log"] = "\n".join(logs)
        winner = 1 if b["hp2"] <= 0 else 2
        mon_end_battle(b, winner)
        return
    b["round"] += 1
    b["log"] = "\n".join(logs)
    b["last_action"] = time.time()
    try:
        api("editMessageText", chat_id=b["chat_id"], message_id=b["message_id"], text=mon_battle_text(b), reply_markup=mon_battle_kb(b))
    except Exception as e:
        print(f"Failed to update battle message: {e}", flush=True)

def mon_handle_callback(cq):
    try:
        data = cq.get("data", "")
        sender_id = cq.get("from", {}).get("id")
        chat_id = cq.get("message", {}).get("chat", {}).get("id")
        message_id = cq.get("message", {}).get("message_id")
        cq_id = cq.get("id")
        if not data.startswith("mon:"):
            return
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        def answer(text, alert=False):
            try:
                api("answerCallbackQuery", callback_query_id=cq_id, text=text, show_alert=alert)
            except Exception:
                pass
        if action == "buy":
            ball = parts[2] if len(parts) > 2 else ""
            if ball not in BALLS:
                return
            price = BALLS[ball]["price"]
            coins = get_balance(sender_id)
            if coins < price:
                answer(f"Koin tidak cukup! {BALLS[ball]['name']} harganya {price} koin, saldo kamu {coins}.", alert=True)
                return
            change_balance(sender_id, -price)
            with MONSTER_LOCK:
                mdata = mon_load()
                u = mon_get_user(mdata, sender_id)
                u["balls"][ball] = u["balls"].get(ball, 0) + 1
                mon_save(mdata)
            answer(f"Kamu beli 1 {BALLS[ball]['emoji']} {BALLS[ball]['name']}! Sisa koin: {get_balance(sender_id)}.")
            return
        if action == "ct":
            ball = parts[2] if len(parts) > 2 else ""
            wild = WILD.get(sender_id)
            if not wild:
                answer("Monster liarnya udah nggak ada. Ketik /catch lagi.", alert=True)
                return
            if ball not in BALLS:
                return
            sp = SPECIES[wild["species"]]
            if not mon_ball_can_catch(ball, wild["rarity"]):
                answer(f"{BALLS[ball]['name']} tidak bisa menangkap monster {wild['rarity']}! Pakai ball yang lebih bagus.", alert=True)
                return
            with MONSTER_LOCK:
                mdata = mon_load()
                u = mon_get_user(mdata, sender_id)
                if u["balls"].get(ball, 0) <= 0:
                    answer("Kamu tidak punya ball itu!", alert=True)
                    return
                u["balls"][ball] -= 1
                mon_save(mdata)
            chance = mon_catch_chance(wild["rarity"], ball, wild["level"])
            if random.random() < chance:
                with MONSTER_LOCK:
                    mdata = mon_load()
                    u = mon_get_user(mdata, sender_id)
                    if len(u["team"]) >= MONSTER_MAX_TEAM:
                        answer("Tim kamu penuh (8 monster)! Tidak bisa menangkap lagi.", alert=True)
                        u["balls"][ball] = u["balls"].get(ball, 0) + 1
                        mon_save(mdata)
                        return
                    new_mon = {"species": wild["species"], "level": wild["level"], "xp": 0, "hp": mon_max_hp(wild["species"], wild["level"])}
                    u["team"].append(new_mon)
                    if len(u["team"]) == 1:
                        u["active"] = 0
                    mon_save(mdata)
                WILD.pop(sender_id, None)
                try:
                    api("editMessageText", chat_id=chat_id, message_id=message_id,
                        text=f"🎉 BERHASIL! Kamu menangkap {RARITY_EMOJI[wild['rarity']]} {sp['name']} (Lv {wild['level']}, {sp['type']}, {wild['rarity']})!\nMonster ini masuk ke tim kamu. Ketik /team buat lihat tim.")
                except Exception:
                    pass
                answer(f"Kamu menangkap {sp['name']}!")
            else:
                try:
                    api("editMessageText", chat_id=chat_id, message_id=message_id,
                        text=f"💨 {sp['name']} lepas dari {BALLS[ball]['emoji']} {BALLS[ball]['name']}! Coba lagi pakai ball lain.\n{RARITY_EMOJI[wild['rarity']]} {sp['name']} (Lv {wild['level']}, {sp['type']}, {wild['rarity']})",
                        reply_markup=mon_kb([[mon_btn(f"{BALLS[bb]['emoji']} {BALLS[bb]['name']}", f"mon:ct:{bb}") for bb in BALL_ORDER if True]]))
                except Exception:
                    pass
                answer(f"{sp['name']} lepas! Coba lagi.")
            return
        if action == "flee":
            wild = WILD.pop(sender_id, None)
            try:
                api("editMessageText", chat_id=chat_id, message_id=message_id, text="🏃 Monster liar kabur. Ketik /catch buat cari lagi.")
            except Exception:
                pass
            answer("Kamu kabur.")
            return
        if action == "tm":
            try:
                idx = int(parts[2])
            except Exception:
                return
            with MONSTER_LOCK:
                mdata = mon_load()
                u = mon_get_user(mdata, sender_id)
                if 0 <= idx < len(u.get("team", [])):
                    u["active"] = idx
                    mon_save(mdata)
                    m = u["team"][idx]
                    answer(f"Monster aktif: {SPECIES[m['species']]['name']} Lv {m['level']}.")
                else:
                    answer("Monster tidak ditemukan.", alert=True)
            return
        if action in ("acc", "dec"):
            bid = parts[2] if len(parts) > 2 else ""
            with BATTLE_LOCK:
                b = BATTLES.get(bid)
            if not b or b["state"] != "waiting_accept":
                answer("Tantangan ini sudah tidak berlaku.", alert=True)
                return
            if sender_id != b["p2"]:
                answer("Tantangan ini bukan buat kamu!", alert=True)
                return
            if action == "dec":
                with BATTLE_LOCK:
                    BATTLES.pop(bid, None)
                try:
                    api("editMessageText", chat_id=b["chat_id"], message_id=b["message_id"], text=f"❌ {b['p2_name']} menolak tantangan battle dari {b['p1_name']}.")
                except Exception:
                    pass
                answer("Kamu menolak tantangan.")
                return
            with BATTLE_LOCK:
                b["state"] = "active"
                b["last_action"] = time.time()
            try:
                api("editMessageText", chat_id=b["chat_id"], message_id=b["message_id"], text=mon_battle_text(b), reply_markup=mon_battle_kb(b))
            except Exception as e:
                print(f"Failed to start battle message: {e}", flush=True)
            answer("Battle dimulai! Pilih jurus kamu.")
            return
        if action == "mv":
            bid = parts[2] if len(parts) > 2 else ""
            try:
                player = int(parts[3])
                move_idx = int(parts[4])
            except Exception:
                return
            with BATTLE_LOCK:
                b = BATTLES.get(bid)
            if not b or b["state"] != "active":
                answer("Battle ini sudah selesai / tidak berlaku.", alert=True)
                return
            expected = b["p1"] if player == 1 else b["p2"]
            if sender_id != expected:
                answer("Itu jurus lawan! Pilih jurus kamu sendiri.", alert=True)
                return
            mon = b["m1"] if player == 1 else b["m2"]
            sp = SPECIES[mon["species"]]
            if move_idx < 0 or move_idx >= len(sp["moves"]):
                return
            move = sp["moves"][move_idx]
            with BATTLE_LOCK:
                if player == 1:
                    b["pick1"] = move
                else:
                    b["pick2"] = move
                b["last_action"] = time.time()
                both = b["pick1"] is not None and b["pick2"] is not None
            if both:
                mon_resolve_round(b)
            else:
                answer(f"Kamu memilih {move}! Menunggu lawan...")
            return
    except Exception as e:
        print(f"Monster callback error: {e}", flush=True)

def mon_sweeper():
    while True:
        try:
            time.sleep(30)
            now = time.time()
            with BATTLE_LOCK:
                stale = [bid for bid, b in BATTLES.items() if now - b.get("last_action", now) > 150]
                for bid in stale:
                    b = BATTLES.pop(bid, None)
                    if b:
                        try:
                            api("editMessageText", chat_id=b["chat_id"], message_id=b["message_id"], text="⏰ Battle dibatalkan karena tidak ada yang bergerak.")
                        except Exception:
                            pass
            old_wild = [uid for uid, w in WILD.items() if now - w.get("time", now) > 600]
            for uid in old_wild:
                WILD.pop(uid, None)
        except Exception as e:
            print(f"Monster sweeper error: {e}", flush=True)

# /cuaca: cuaca kota random di Indonesia (berlaku untuk siapa saja).
# Sumber utama wttr.in (gratis, tanpa key, format JSON); kalau gagal,
# fallback ke Open-Meteo (gratis, tanpa key) pakai lat/lon kota.
# /cuaca = kota random, /cuaca <nama kota> = kota itu (kalau ada di daftar).
CUACA_CITIES = {
    "Jakarta": (-6.2088, 106.8456),
    "Bandung": (-6.9175, 107.6191),
    "Surabaya": (-7.2575, 112.7521),
    "Medan": (3.5952, 98.6722),
    "Makassar": (-5.1477, 119.4327),
    "Yogyakarta": (-7.7956, 110.3695),
    "Semarang": (-6.9667, 110.4167),
    "Palembang": (-2.9910, 104.7565),
    "Denpasar": (-8.6705, 115.2126),
    "Balikpapan": (-1.2379, 116.8526),
    "Pontianak": (-0.0263, 109.3425),
    "Banjarmasin": (-3.3186, 114.5944),
    "Manado": (1.4748, 124.8426),
    "Jayapura": (-2.5337, 140.7181),
    "Padang": (-0.9471, 100.4172),
    "Pekanbaru": (0.5071, 101.4478),
    "Bandar Lampung": (-5.4294, 105.2616),
    "Malang": (-7.9666, 112.6326),
    "Solo": (-7.5755, 110.8243),
    "Batam": (1.0456, 104.0305),
    "Bogor": (-6.5971, 106.8060),
    "Bekasi": (-6.2383, 106.9756),
    "Tangerang": (-6.1702, 106.6406),
    "Depok": (-6.4025, 106.7942),
    "Cirebon": (-6.7320, 108.5523),
    "Ambon": (-3.6553, 128.1965),
    "Kupang": (-10.1772, 123.6070),
    "Mataram": (-8.5833, 116.1167),
    "Jambi": (-1.6102, 103.6131),
    "Bengkulu": (-3.7928, 102.2608),
}

# Kode cuaca wttr.in -> (emoji, deskripsi Indonesia)
WTTR_CODE_ID = {
    "113": ("☀️", "Cerah"),
    "116": ("⛅", "Berawan sebagian"),
    "119": ("☁️", "Berawan"),
    "122": ("☁️", "Mendung"),
    "143": ("🌫️", "Kabut tipis"),
    "149": ("🌫️", "Berasap"),
    "176": ("🌦️", "Hujan rintik"),
    "179": ("🌧️", "Hujan rintik"),
    "182": ("🌧️", "Hujan es rintik"),
    "185": ("🌧️", "Hujan es rintik"),
    "200": ("⛈️", "Hujan petir"),
    "227": ("🌨️", "Salju"),
    "230": ("🌨️", "Badai salju"),
    "248": ("🌫️", "Kabut tebal"),
    "260": ("🌫️", "Kabut"),
    "263": ("🌦️", "Gerimis ringan"),
    "266": ("🌦️", "Gerimis"),
    "281": ("🌧️", "Hujan beku"),
    "284": ("🌧️", "Hujan beku"),
    "293": ("🌦️", "Hujan rintik ringan"),
    "296": ("🌧️", "Hujan ringan"),
    "299": ("🌧️", "Hujan sedang"),
    "302": ("🌧️", "Hujan sedang"),
    "305": ("🌧️", "Hujan deras"),
    "308": ("🌧️", "Hujan deras"),
    "311": ("🌧️", "Hujan beku ringan"),
    "314": ("🌧️", "Hujan beku"),
    "317": ("🌧️", "Hujan es"),
    "320": ("🌧️", "Hujan es"),
    "323": ("🌨️", "Salju ringan"),
    "326": ("🌨️", "Salju ringan"),
    "329": ("🌨️", "Salju sedang"),
    "332": ("🌨️", "Salju sedang"),
    "335": ("🌨️", "Salju lebat"),
    "338": ("🌨️", "Salju lebat"),
    "350": ("🌧️", "Hujan es"),
    "353": ("🌦️", "Hujan rintik ringan"),
    "356": ("🌧️", "Hujan sedang"),
    "359": ("🌧️", "Hujan deras"),
    "362": ("🌧️", "Hujan es ringan"),
    "365": ("🌧️", "Hujan es"),
    "368": ("🌨️", "Salju ringan"),
    "371": ("🌨️", "Salju sedang"),
    "374": ("🌧️", "Hujan es ringan"),
    "377": ("🌧️", "Hujan es"),
    "386": ("⛈️", "Hujan petir ringan"),
    "389": ("⛈️", "Hujan petir"),
    "392": ("⛈️", "Petir + salju ringan"),
    "395": ("⛈️", "Petir + salju"),
}

# Kode cuaca WMO (Open-Meteo) -> (emoji, deskripsi Indonesia)
WMO_CODE_ID = {
    0: ("☀️", "Cerah"),
    1: ("🌤️", "Cerah berawan"),
    2: ("⛅", "Berawan sebagian"),
    3: ("☁️", "Mendung"),
    45: ("🌫️", "Kabut"),
    48: ("🌫️", "Kabut"),
    51: ("🌦️", "Gerimis ringan"),
    53: ("🌦️", "Gerimis"),
    55: ("🌦️", "Gerimis deras"),
    56: ("🌧️", "Gerimis beku ringan"),
    57: ("🌧️", "Gerimis beku"),
    61: ("🌧️", "Hujan ringan"),
    63: ("🌧️", "Hujan sedang"),
    65: ("🌧️", "Hujan deras"),
    66: ("🌧️", "Hujan beku ringan"),
    67: ("🌧️", "Hujan beku"),
    71: ("🌨️", "Salju ringan"),
    73: ("🌨️", "Salju sedang"),
    75: ("🌨️", "Salju lebat"),
    77: ("🌨️", "Butiran salju"),
    80: ("🌦️", "Hujan rintik ringan"),
    81: ("🌧️", "Hujan rintik sedang"),
    82: ("🌧️", "Hujan rintik deras"),
    85: ("🌨️", "Hujan salju ringan"),
    86: ("🌨️", "Hujan salju lebat"),
    95: ("⛈️", "Badai petir"),
    96: ("⛈️", "Badai petir + hujan es ringan"),
    99: ("⛈️", "Badai petir + hujan es"),
}

def cuaca_find_city(arg):
    if not arg:
        return None
    key = arg.strip().lower()
    for name in CUACA_CITIES:
        if name.lower() == key:
            return name
    for name in CUACA_CITIES:
        if key in name.lower() or name.lower() in key:
            return name
    return None

def cuaca_obs_to_wib(obs):
    # wttr.in mengembalikan observation_time dalam UTC format 12 jam,
    # misal "11:23 PM" -> ubah ke WIB (UTC+7) format 24 jam: "06:23 WIB".
    # Kalau formatnya tidak dikenal, kembalikan apa adanya.
    try:
        from datetime import datetime, timedelta
        dt = datetime.strptime(str(obs).strip(), "%I:%M %p")
        dt = dt + timedelta(hours=7)
        return dt.strftime("%H:%M") + " WIB"
    except Exception:
        return obs

def cuaca_from_wttr(city):
    import urllib.parse
    url = "https://wttr.in/" + urllib.parse.quote(city) + "?format=j1"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    d = r.json()
    c = d["current_condition"][0]
    code = str(c.get("weatherCode", ""))
    emoji, desc = WTTR_CODE_ID.get(code, ("🌡️", c.get("weatherDesc", [{}])[0].get("value", "Cuaca tidak diketahui")))
    return {
        "temp": c.get("temp_C"),
        "feels": c.get("FeelsLikeC"),
        "humidity": c.get("humidity"),
        "wind": c.get("windspeedKmph"),
        "winddir": c.get("winddir16Point"),
        "precip": c.get("precipMM"),
        "time": cuaca_obs_to_wib(c.get("observation_time")),
        "emoji": emoji,
        "desc": desc,
    }

def cuaca_from_openmeteo(city):
    lat, lon = CUACA_CITIES[city]
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation"
        "&timezone=Asia%2FJakarta"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    d = r.json()
    cur = d.get("current", {})
    if "temperature_2m" not in cur:
        raise RuntimeError("Open-Meteo tidak mengembalikan cuaca")
    code = cur.get("weather_code")
    emoji, desc = WMO_CODE_ID.get(code, ("🌡️", "Cuaca tidak diketahui"))
    return {
        "temp": cur.get("temperature_2m"),
        "feels": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "winddir": None,
        "precip": cur.get("precipitation"),
        "time": (cur.get("time", "")[-5:] + " WIB") if cur.get("time") else None,
        "emoji": emoji,
        "desc": desc,
    }

# Batas /cuaca: tiap orang di grup hanya boleh 2x per hari.
# Hitungan hanya berkurang kalau cuaca berhasil diambil (gagal fetch tidak makan jatah).
CUACA_LIMIT_FILE = os.path.join(os.path.dirname(__file__), "cuaca_limits.json")
CUACA_LIMIT_LOCK = threading.Lock()
CUACA_DAILY_LIMIT = 2

def _cuaca_load_limits():
    try:
        with open(CUACA_LIMIT_FILE) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _cuaca_save_limits(data):
    try:
        with open(CUACA_LIMIT_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Gagal simpan batas cuaca: {e}", flush=True)

def cuaca_remaining(sender_id):
    today = time.strftime("%Y-%m-%d")
    with CUACA_LIMIT_LOCK:
        data = _cuaca_load_limits()
        entry = data.get(str(sender_id), {})
        if entry.get("date") != today:
            return CUACA_DAILY_LIMIT
        return max(0, CUACA_DAILY_LIMIT - entry.get("count", 0))

def cuaca_consume(sender_id):
    today = time.strftime("%Y-%m-%d")
    with CUACA_LIMIT_LOCK:
        data = _cuaca_load_limits()
        entry = data.get(str(sender_id), {})
        if entry.get("date") != today:
            entry = {"date": today, "count": 0}
        entry["count"] = entry.get("count", 0) + 1
        data[str(sender_id)] = entry
        _cuaca_save_limits(data)
        return max(0, CUACA_DAILY_LIMIT - entry["count"])

def do_cuaca(chat_id, message_id, sender_id, arg):
    try:
        if cuaca_remaining(sender_id) <= 0:
            api(
                "sendMessage",
                chat_id=chat_id,
                text=f"Kamu sudah pakai /cuaca {CUACA_DAILY_LIMIT}x hari ini. Coba lagi besok ya!",
                reply_to_message_id=message_id,
            )
            return
        # /cuaca <nama kota> bisa untuk kota apa saja (tidak cuma 30 kota di daftar):
        # kalau namanya ada di daftar, pakai itu; kalau tidak ada, coba langsung
        # ke wttr.in dengan nama yang diketik (wttr.in bisa cari kota apa saja).
        # Kalau tidak ketik kota, pilih random dari daftar.
        city = None
        query_name = None
        display_name = None
        if arg:
            city = cuaca_find_city(arg)
            if city:
                query_name = city
                display_name = city
            else:
                query_name = arg.strip()
                display_name = arg.strip().title()
                city = None
        else:
            city = random.choice(list(CUACA_CITIES.keys()))
            query_name = city
            display_name = city
        info = None
        source = ""
        try:
            info = cuaca_from_wttr(query_name)
            source = "wttr.in"
        except Exception as e:
            print(f"wttr.in gagal untuk {query_name}: {e}", flush=True)
            if city is not None:
                try:
                    info = cuaca_from_openmeteo(city)
                    source = "Open-Meteo"
                except Exception as e2:
                    print(f"Open-Meteo gagal untuk {city}: {e2}", flush=True)
                    api(
                        "sendMessage",
                        chat_id=chat_id,
                        text="Maaf, cuaca lagi tidak bisa diambil. Coba lagi nanti ya.",
                        reply_to_message_id=message_id,
                    )
                    return
            else:
                api(
                    "sendMessage",
                    chat_id=chat_id,
                    text=f"Kota '{arg.strip()}' tidak ditemukan. Coba /cuaca <nama kota> lain, atau /cuaca buat kota random.",
                    reply_to_message_id=message_id,
                )
                return
        lines = [
            f"🌤️ Cuaca di {display_name}",
            f"{info['emoji']} {info['desc']}",
            f"🌡️ Suhu: {info['temp']}°C (terasa {info['feels']}°C)",
            f"💧 Kelembapan: {info['humidity']}%",
        ]
        if info.get("wind") is not None:
            wd = f" {info['winddir']}" if info.get("winddir") else ""
            lines.append(f"💨 Angin: {info['wind']} km/jam{wd}")
        if info.get("precip") is not None:
            lines.append(f"🌧️ Curah hujan: {info['precip']} mm")
        if info.get("time"):
            lines.append(f"🕐 Diamati: {info['time']}")
        sisa = cuaca_consume(sender_id)
        if sisa > 0:
            lines.append(f"\nSisa /cuaca kamu hari ini: {sisa}x. Ketik /cuaca buat kota random lain, atau /cuaca <nama kota>.")
        else:
            lines.append("\nItu /cuaca terakhir kamu hari ini. Coba lagi besok ya!")
        api(
            "sendMessage",
            chat_id=chat_id,
            text="\n".join(lines),
            reply_to_message_id=message_id,
        )
    except Exception as e:
        print(f"do_cuaca error: {e}", flush=True)
        try:
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Maaf, cuaca lagi tidak bisa diambil. Coba lagi nanti ya.",
                reply_to_message_id=message_id,
            )
        except Exception:
            pass

# Cuaca pagi otomatis: tiap hari jam 7 pagi WIB, bot kirim info cuaca
# Cuaca pagi otomatis: kirim cuaca 1 kota random Indonesia ke grup
# tiap hari jam 07:00 WIB. Jalan sebagai thread di dalam bot.py
# (program biasa, tanpa token Muse AI).
# HANYA jam 7 pagi, 1x sehari — TIDAK ada catch-up. Kalau bot restart
# setelah jam 7, jangan kirim lagi hari itu (tunggu jam 7 besok).
# Tanggal terakhir terkirim disimpan di cuaca_pagi_last.txt sebagai
# pengaman supaya tidak double-kirim di sekitar jam 7.
CUACA_PAGI_GROUP_ID = -1003337200559
CUACA_PAGI_HOUR = 7
CUACA_PAGI_LAST_FILE = os.path.join(os.path.dirname(__file__), "cuaca_pagi_last.txt")

def cuaca_pagi_kirim():
    # Kirim cuaca kota random ke grup. Return True kalau berhasil.
    city = random.choice(list(CUACA_CITIES.keys()))
    info = None
    try:
        info = cuaca_from_wttr(city)
    except Exception as e:
        print(f"Cuaca pagi wttr.in gagal untuk {city}: {e}", flush=True)
        try:
            info = cuaca_from_openmeteo(city)
        except Exception as e2:
            print(f"Cuaca pagi Open-Meteo gagal untuk {city}: {e2}", flush=True)
            return False
    lines = [
        f"🌤️ Selamat pagi! Cuaca hari ini di {city}",
        f"{info['emoji']} {info['desc']}",
        f"🌡️ Suhu: {info['temp']}°C (terasa {info['feels']}°C)",
        f"💧 Kelembapan: {info['humidity']}%",
    ]
    if info.get("wind") is not None:
        wd = f" {info['winddir']}" if info.get("winddir") else ""
        lines.append(f"💨 Angin: {info['wind']} km/jam{wd}")
    if info.get("precip") is not None:
        lines.append(f"🌧️ Curah hujan: {info['precip']} mm")
    lines.append("\nKetik /cuaca buat cek kota lain!")
    try:
        api("sendMessage", chat_id=CUACA_PAGI_GROUP_ID, text="\n".join(lines))
        print(f"Cuaca pagi terkirim ke grup: {city}", flush=True)
        return True
    except Exception as e:
        print(f"Cuaca pagi gagal kirim ke grup: {e}", flush=True)
        return False

def cuaca_pagi_thread():
    from zoneinfo import ZoneInfo
    from datetime import datetime, timedelta
    tz = ZoneInfo("Asia/Jakarta")

    def last_sent():
        try:
            with open(CUACA_PAGI_LAST_FILE) as f:
                return f.read().strip()
        except Exception:
            return ""

    def mark_sent(d):
        try:
            with open(CUACA_PAGI_LAST_FILE, "w") as f:
                f.write(d)
        except Exception as e:
            print(f"Gagal simpan tanggal cuaca pagi: {e}", flush=True)

    # TIDAK ADA catch-up: cuaca pagi HANYA dikirim pas jam 7 pagi.
    # Kalau bot restart/mati-hidup setelah jam 7, jangan kirim lagi hari
    # itu (tunggu jam 7 besok). Ini mencegah spam cuaca tiap restart,
    # karena di Infrlo file cuaca_pagi_last.txt tidak bertahan saat
    # redeploy (filesystem container di-reset), jadi catch-up selalu
    # mengira hari ini belum kekirim.
    while True:
        try:
            now = datetime.now(tz)
            # Hitung jam 7 pagi berikutnya (kalau sudah lewat jam 7 hari
            # ini, targetnya jam 7 besok — jangan kirim hari ini lagi).
            target = now.replace(hour=CUACA_PAGI_HOUR, minute=0, second=0, microsecond=0)
            if now >= target:
                target = target + timedelta(days=1)
            print(f"Cuaca pagi: kirim berikutnya {target.strftime('%Y-%m-%d %H:%M')} WIB", flush=True)
            while True:
                now = datetime.now(tz)
                wait = (target - now).total_seconds()
                if wait <= 0:
                    break
                time.sleep(min(60, wait))
            today = datetime.now(tz).strftime("%Y-%m-%d")
            if last_sent() == today:
                print(f"Cuaca pagi: {today} sudah terkirim, lewati.", flush=True)
                continue
            if cuaca_pagi_kirim():
                mark_sent(today)
            else:
                time.sleep(60)
        except Exception as e:
            print(f"Cuaca pagi error: {e}", flush=True)
            time.sleep(60)

def do_dice(chat_id, message_id, sender_id, sender_name):
    try:
        # Dadu animasi asli Telegram: kirim 2 dadu (🎲) yang berputar
        # lalu berhenti sendiri. Nilainya dipilih server Telegram dan
        # bot baca dari hasil kirimnya (message.dice.value).
        animated = False
        mid = None
        d1 = d2 = None
        dice_msg_ids = []
        try:
            # Cuma 1 dadu animasi yang dikirim; dadu kedua diacak bot.
            m1 = api("sendDice", chat_id=chat_id, reply_to_message_id=message_id, emoji="🎲")
            d1 = m1.get("dice", {}).get("value")
            if m1.get("message_id"):
                dice_msg_ids.append(m1["message_id"])
            animated = bool(d1)
        except Exception as e:
            print(f"sendDice failed, fallback to edit spin: {e}", flush=True)
        if animated:
            # Tunggu animasi dadunya selesai berputar (sekitar 3 detik)
            # sebelum umumkan hasil. Dadu kedua diacak bot.
            time.sleep(3)
            d2 = random.randint(1, 6)
        else:
            # Cadangan kalau sendDice gagal: putar dadu lewat edit pesan.
            status = api(
                "sendMessage",
                chat_id=chat_id,
                text="🎲🎲 Mengocok dadu...",
                reply_to_message_id=message_id,
            )
            mid = status.get("message_id")
            for _ in range(5):
                time.sleep(0.6)
                try:
                    api(
                        "editMessageText",
                        chat_id=chat_id,
                        message_id=mid,
                        text=f"🎲 {random.choice(DICE_FACES)} {random.choice(DICE_FACES)} 🎲",
                    )
                except Exception:
                    pass
            d1 = random.randint(1, 6)
            d2 = random.randint(1, 6)
        total = d1 + d2
        delta = (total - 7) * 50
        balance = change_balance(sender_id, delta)
        bankrupt = False
        if balance <= 0:
            set_balance(sender_id, START_BALANCE)
            balance = START_BALANCE
            bankrupt = True
        if delta > 0:
            hasil = f"Menang +{delta} koin! 🎉"
        elif delta < 0:
            hasil = f"Kalah {delta} koin 😢"
        else:
            hasil = "Seri, saldo tetap 😐"
        text = (
            f"🎲 {DICE_FACES[d1 - 1]} {DICE_FACES[d2 - 1]} 🎲\n"
            f"{sender_name} dapat total {total}!\n"
            f"{hasil}\n"
            f"💰 Saldo: {balance} koin"
        )
        if bankrupt:
            text += "\n💸 Bangkrut! Dikasih modal baru 1000 koin."
        if animated:
            api(
                "sendMessage",
                chat_id=chat_id,
                text=text,
                reply_to_message_id=message_id,
            )
            # Hasilnya sudah muncul, hapus pesan dadu animasinya.
            # Dihapus barengan (paralel), bukan satu-satu, supaya cepat:
            # tiap panggilan ke Telegram dari komputer ini butuh ~0,7 detik.
            def _delete_dice(dice_mid):
                try:
                    api("deleteMessage", chat_id=chat_id, message_id=dice_mid)
                except Exception as e:
                    print(f"Failed to delete dice message {dice_mid}: {e}", flush=True)

            del_threads = [
                threading.Thread(target=_delete_dice, args=(dm,), daemon=True)
                for dm in dice_msg_ids
            ]
            for t in del_threads:
                t.start()
            for t in del_threads:
                t.join()
        else:
            try:
                api("editMessageText", chat_id=chat_id, message_id=mid, text=text)
            except Exception:
                api(
                    "sendMessage",
                    chat_id=chat_id,
                    text=text,
                    reply_to_message_id=message_id,
                )
        print(f"Dice {sender_name} ({sender_id}): {d1}+{d2}={total} delta={delta} balance={balance}", flush=True)
    except Exception as e:
        print(f"Dice error for message {message_id}: {e}", flush=True)
        try:
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Dadunya gagal dikocok, coba lagi ya.",
                reply_to_message_id=message_id,
            )
        except Exception:
            pass

def download_tiktok(chat_id, message_id, url, sender_id):
    tmpdir = None
    status_id = None
    try:
        status_msg = api(
            "sendMessage",
            chat_id=chat_id,
            text="Lagi download videonya, bentar ya...",
            reply_to_message_id=message_id,
        )
        status_id = status_msg.get("message_id") if isinstance(status_msg, dict) else None
        dl_dir = os.path.join(os.path.dirname(__file__), "downloads")
        os.makedirs(dl_dir, exist_ok=True)
        tmpdir = tempfile.mkdtemp(prefix="tiktok_", dir=dl_dir)
        out_tmpl = os.path.join(tmpdir, "video.%(ext)s")
        cmd = [
            sys.executable, "-m", "yt_dlp",
            # Komputer Muse lewat proxy yang sertifikatnya tidak dikenali
            # yt-dlp, jadi verifikasi sertifikat dimatikan untuk download
            # video publik ini. Tanpa ini TikTok gagal dengan
            # CERTIFICATE_VERIFY_FAILED. curl_cffi (untuk impersonasi
            # TikTok) juga sudah dipasang via pip.
            "--no-check-certificates",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", out_tmpl,
            url,
        ]
        print(f"Downloading TikTok {url} for message {message_id} from {sender_id}", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            print(f"yt-dlp failed for {url}: {r.stderr[-500:]}", flush=True)
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Gagal download videonya, coba link lain ya.",
                reply_to_message_id=message_id,
            )
            return
        files = [p for p in glob.glob(os.path.join(tmpdir, "video.*")) if os.path.isfile(p)]
        if not files:
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Gagal download videonya, filenya nggak ketemu.",
                reply_to_message_id=message_id,
            )
            return
        path = files[0]
        size = os.path.getsize(path)
        if size > MAX_VIDEO_BYTES:
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Videonya kegedean, nggak bisa dikirim lewat bot. Maksimal 50 MB.",
                reply_to_message_id=message_id,
            )
            print(f"TikTok video too big ({size} bytes) for message {message_id}", flush=True)
            return
        with open(path, "rb") as f:
            resp = requests.post(
                f"{BASE}/sendVideo",
                data={"chat_id": chat_id, "reply_to_message_id": message_id},
                files={"video": (os.path.basename(path), f, "video/mp4")},
                timeout=180,
            )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"sendVideo not ok: {data}")
        print(f"Sent TikTok video ({size} bytes) for message {message_id} from {sender_id}", flush=True)
    except Exception as e:
        print(f"TikTok download error for message {message_id}: {e}", flush=True)
        try:
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Gagal download videonya, coba lagi nanti ya.",
                reply_to_message_id=message_id,
            )
        except Exception:
            pass
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        # Hapus pesan status "Lagi download videonya..." setelah video kekirim
        # (atau setelah gagal) biar grup nggak penuh pesan status.
        if status_id:
            try:
                api("deleteMessage", chat_id=chat_id, message_id=status_id)
            except Exception:
                pass

def download_youtube(chat_id, message_id, url, sender_id):
    tmpdir = None
    status_id = None
    try:
        if not YOUTUBE_API_KEY:
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Download YouTube lagi nggak aktif, API key-nya belum diset.",
                reply_to_message_id=message_id,
            )
            return
        status_msg = api(
            "sendMessage",
            chat_id=chat_id,
            text="Lagi download videonya, bentar ya...",
            reply_to_message_id=message_id,
        )
        status_id = status_msg.get("message_id") if isinstance(status_msg, dict) else None
        print(f"Requesting YouTube download {url} for message {message_id} from {sender_id}", flush=True)
        r = requests.get(
            YOUTUBE_API_URL,
            params={"api_key": YOUTUBE_API_KEY, "url": url},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            print(f"YouTube API error for {url}: {data}", flush=True)
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Gagal download videonya, coba link lain ya.",
                reply_to_message_id=message_id,
            )
            return
        formats = data.get("formats") or []
        # Pilih format yang ada audionya, paling besar yang masih di bawah batas.
        best = None
        for f in formats:
            if not f.get("has_audio") or not f.get("download_url"):
                continue
            size = f.get("bytes") or 0
            if size > MAX_VIDEO_BYTES:
                continue
            if best is None or size > (best.get("bytes") or 0):
                best = f
        if best is None:
            # Semua format kegedean (atau tidak ada yang ber-audio).
            if formats and all((f.get("bytes") or 0) > MAX_VIDEO_BYTES for f in formats if f.get("has_audio")):
                api(
                    "sendMessage",
                    chat_id=chat_id,
                    text="Videonya kegedean, nggak bisa dikirim lewat bot. Maksimal 50 MB.",
                    reply_to_message_id=message_id,
                )
            else:
                api(
                    "sendMessage",
                    chat_id=chat_id,
                    text="Gagal download videonya, coba link lain ya.",
                    reply_to_message_id=message_id,
                )
            print(f"YouTube no usable format for {url}: {data.get('formats')}", flush=True)
            return
        dl_url = best["download_url"]
        size = best.get("bytes") or 0
        print(f"Downloading YouTube {url} ({size} bytes, {best.get('quality')}) for message {message_id} from {sender_id}", flush=True)
        dl_dir = os.path.join(os.path.dirname(__file__), "downloads")
        os.makedirs(dl_dir, exist_ok=True)
        tmpdir = tempfile.mkdtemp(prefix="youtube_", dir=dl_dir)
        path = os.path.join(tmpdir, f"video.{best.get('ext', 'mp4').lower()}")
        with requests.get(dl_url, stream=True, timeout=180) as resp:
            resp.raise_for_status()
            with open(path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
        actual = os.path.getsize(path)
        if actual > MAX_VIDEO_BYTES:
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Videonya kegedean, nggak bisa dikirim lewat bot. Maksimal 50 MB.",
                reply_to_message_id=message_id,
            )
            print(f"YouTube video too big ({actual} bytes) for message {message_id}", flush=True)
            return
        with open(path, "rb") as f:
            resp = requests.post(
                f"{BASE}/sendVideo",
                data={"chat_id": chat_id, "reply_to_message_id": message_id},
                files={"video": (os.path.basename(path), f, "video/mp4")},
                timeout=180,
            )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"sendVideo not ok: {data}")
        print(f"Sent YouTube video ({actual} bytes) for message {message_id} from {sender_id}", flush=True)
    except Exception as e:
        print(f"YouTube download error for message {message_id}: {e}", flush=True)
        try:
            api(
                "sendMessage",
                chat_id=chat_id,
                text="Gagal download videonya, coba lagi nanti ya.",
                reply_to_message_id=message_id,
            )
        except Exception:
            pass
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        # Hapus pesan status "Lagi download videonya..." setelah video kekirim
        # (atau setelah gagal) biar grup nggak penuh pesan status.
        if status_id:
            try:
                api("deleteMessage", chat_id=chat_id, message_id=status_id)
            except Exception:
                pass

# Emoji yang diizinkan Telegram sebagai reaction.
# Catatan: Bot API hanya mengizinkan bot memasang 1 reaksi per pesan,
# jadi walau user minta 3, bot hanya bisa pasang 1 reaksi random.
REACTION_EMOJIS = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱",
    "🤬", "😢", "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡",
    "🥱", "🥴", "😍", "🐳", "🌚", "🌭", "💯", "🤣", "⚡", "🍌",
    "🏆", "💔", "🤨", "😐", "🍓", "🍾", "💋", "😈", "😴", "😭",
    "🤓", "😇", "🤝", "✍️", "🤗", "🫡", "💅", "🤪", "🗿", "💘",
    "🙉", "🦄", "😘", "💊", "🙈", "😎", "😡",
]

def api(method, **kwargs):
    r = requests.post(f"{BASE}/{method}", json=kwargs, timeout=35)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error for {method}: {data}")
    return data["result"]

def react(chat_id, message_id):
    emoji = random.choice(REACTION_EMOJIS)
    try:
        api(
            "setMessageReaction",
            chat_id=chat_id,
            message_id=message_id,
            reaction=[{"type": "emoji", "emoji": emoji}],
            is_big=False,
        )
        print(f"Reacted {emoji} to message {message_id} in chat {chat_id}", flush=True)
        return True
    except Exception as e:
        print(f"setMessageReaction failed for {message_id}: {e}", flush=True)
        return False


# Thread latar: pantau gol Nations League A & B malam ini (ESPN, gratis,
# tanpa key). Kirim ke grup kalau ada gol, gol dibatalkan (VAR), half time,
# dan full time. Tanpa token Muse AI, cuma program biasa. Berhenti sendiri
# setelah semua pertandingan selesai.
LIVESCORE_GROUP_ID = -1003337200559
LIVESCORE_LEAGUE = "uefa.nations"
LIVESCORE_DATE = "20261116"
# event_id -> (tim kandang, tim tandang, liga "A"/"B", jam kickoff WIB)
LIVESCORE_MATCHES = {
    "401861185": ("Germany", "Netherlands", "A", "02:45"),
    "401861188": ("Greece", "Serbia", "A", "02:45"),
    "401861187": ("North Macedonia", "Slovenia", "B", "02:45"),
    "401861186": ("Switzerland", "Scotland", "B", "02:45"),
}
LIVESCORE_TEAM_ID = {
    "Germany": "Jerman", "Netherlands": "Belanda",
    "Greece": "Yunani", "Serbia": "Serbia",
    "North Macedonia": "Makedonia Utara", "Slovenia": "Slovenia",
    "Switzerland": "Swiss", "Scotland": "Skotlandia",
}
ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}

def livescore_get(url):
    try:
        r = requests.get(url, headers=ESPN_HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Livescore fetch gagal: {e}", flush=True)
        return None

def livescore_scorers(event_id):
    # Daftar gol urut waktu: {"name", "minute", "kind", "team"}.
    # kind: "" biasa, "own" gol bunuh diri, "pen" penalti.
    # Return None kalau fetch gagal (biar pemanggil tidak mengira gol hilang).
    data = livescore_get(
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{LIVESCORE_LEAGUE}/summary?event={event_id}"
    )
    if not data:
        return None
    out = []
    for ev in data.get("keyEvents", []):
        if not ev.get("scoringPlay"):
            continue
        parts = ev.get("participants", [])
        name = parts[0].get("athlete", {}).get("displayName", "") if parts else ""
        minute = (ev.get("clock") or {}).get("displayValue", "")
        text = (ev.get("text") or "").lower()
        kind = "own" if "own goal" in text else ("pen" if "penalty" in text else "")
        team = (ev.get("team") or {}).get("displayName", "")
        out.append({"name": name, "minute": minute, "kind": kind, "team": team})
    return out

def _scorer_key(g):
    return (g.get("name", ""), g.get("minute", ""), g.get("team", ""))

def livescore_format_scorers(scorers):
    if not scorers:
        return "Belum ada gol."
    lines = []
    for g in scorers:
        suffix = " (bunuh diri)" if g["kind"] == "own" else (" (penalti)" if g["kind"] == "pen" else "")
        lines.append(f"{g['name']} {g['minute']}{suffix}")
    return "⚽ Pencetak gol:\n" + "\n".join(lines)

def livescore_send(text):
    # Retry sekali kalau gagal (mis. Telegram sempat 400), biar notif penting
    # seperti HT/FT tidak hilang begitu saja.
    for attempt in range(2):
        try:
            api("sendMessage", chat_id=LIVESCORE_GROUP_ID, text=text)
            print(f"Livescore terkirim ke grup: {text.splitlines()[0]}", flush=True)
            return
        except Exception as e:
            print(f"Livescore kirim gagal (percobaan {attempt + 1}): {e}", flush=True)
            if attempt == 0:
                time.sleep(5)

def _ls_minute(eps):
    # "45+6'" -> 45, "50'" -> 50, "HT"/"FT"/"NS" -> None.
    if not isinstance(eps, str) or not eps.endswith("'"):
        return None
    try:
        return int(eps.split("+")[0].rstrip("'"))
    except Exception:
        return None

def _american_to_prob(ml):
    # Odds Amerika ("-115"/"+235") -> probabilitas tersirat (0-1).
    ml = int(ml)
    if ml < 0:
        return abs(ml) / (abs(ml) + 100)
    return 100 / (ml + 100)

def _pickcenter_odds(side):
    # side: {"close": {"odds": "-115", ...}, "open": {...}} -> ambil close, fallback open.
    if not isinstance(side, dict):
        return None
    for key in ("close", "open"):
        v = side.get(key)
        if isinstance(v, dict) and v.get("odds"):
            return v["odds"]
        if isinstance(v, str):
            return v
    return None

def livescore_match_probs(event_id):
    # Persentase menang kandang / seri / menang tandang dari odds DraftKings
    # di endpoint summary ESPN. Return (kandang%, seri%, tandang%) atau None
    # kalau odds tidak tersedia. Probabilitas dinormalisasi supaya total 100%.
    data = livescore_get(
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{LIVESCORE_LEAGUE}/summary?event={event_id}"
    )
    if not data:
        return None
    try:
        pc = (data.get("pickcenter") or [{}])[0]
        ml = pc.get("moneyline", {})
        h = _pickcenter_odds(ml.get("home"))
        d = _pickcenter_odds(ml.get("draw"))
        a = _pickcenter_odds(ml.get("away"))
        if not (h and d and a):
            return None
        ph = _american_to_prob(h)
        pd = _american_to_prob(d)
        pa = _american_to_prob(a)
        tot = ph + pd + pa
        ph, pd, pa = round(ph / tot * 100), round(pd / tot * 100), round(pa / tot * 100)
        # Koreksi pembulatan supaya total tepat 100%.
        diff = 100 - (ph + pd + pa)
        if diff:
            biggest = max(range(3), key=lambda i: (ph, pd, pa)[i])
            vals = [ph, pd, pa]
            vals[biggest] += diff
            ph, pd, pa = vals
        return (ph, pd, pa)
    except Exception as e:
        print(f"Livescore probs gagal untuk {event_id}: {e}", flush=True)
        return None

def livescore_run():
    print("Livescore thread started - Nations League A & B malam ini (4 pertandingan)", flush=True)
    lines = ["⚽ Pantau gol Nations League A & B malam ini:"]
    for eid, (h, a, lg, jam) in LIVESCORE_MATCHES.items():
        hn = LIVESCORE_TEAM_ID.get(h, h)
        an = LIVESCORE_TEAM_ID.get(a, a)
        lines.append(f"• {hn} vs {an} (Liga {lg}) — {jam} WIB")
        probs = livescore_match_probs(eid)
        if probs:
            ph, pd, pa = probs
            lines.append(f"  Menang {hn} {ph}% · Seri {pd}% · Menang {an} {pa}%")
    lines.append("Bot kirim notif kalau ada gol, gol dibatalkan (VAR), half time, dan full time.")
    livescore_send("\n".join(lines))
    state = {eid: {"home": 0, "away": 0, "goals": 0, "scorers": [], "ht": False, "ft": False} for eid in LIVESCORE_MATCHES}
    first_poll = True
    polls = 0
    while polls < 1200:
        polls += 1
        try:
            data = livescore_get(
                f"https://site.api.espn.com/apis/site/v2/sports/soccer/{LIVESCORE_LEAGUE}/scoreboard?dates={LIVESCORE_DATE}"
            )
            if data:
                for ev in data.get("events", []):
                    eid = str(ev.get("id"))
                    if eid not in state:
                        continue
                    st = state[eid]
                    if st["ft"]:
                        continue
                    comps = ev.get("competitions", [{}])[0]
                    clist = comps.get("competitors", [])
                    home = next((c for c in clist if c.get("homeAway") == "home"), None)
                    away = next((c for c in clist if c.get("homeAway") == "away"), None)
                    if not home or not away:
                        continue
                    hs = int(home.get("score") or 0)
                    as_ = int(away.get("score") or 0)
                    status = ev.get("status", {}).get("type", {}).get("name", "")
                    hshort = home.get("team", {}).get("shortDisplayName", "")
                    ashort = away.get("team", {}).get("shortDisplayName", "")
                    hn = LIVESCORE_TEAM_ID.get(hshort, hshort)
                    an = LIVESCORE_TEAM_ID.get(ashort, ashort)
                    _, _, league, _ = LIVESCORE_MATCHES[eid]
                    old_hs, old_as = st["home"], st["away"]
                    st["home"], st["away"] = hs, as_
                    if status == "STATUS_SCHEDULED":
                        continue
                    new_scorers = livescore_scorers(eid)
                    if new_scorers is None:
                        # Fetch gagal: pakai daftar gol terakhir, jangan
                        # sampai mengira gol hilang dan salah kirim notif.
                        new_scorers = st["scorers"]
                    if first_poll:
                        # Poll pertama cuma isi state awal, jangan kirim
                        # pesan buat gol yang sudah terjadi sebelum thread jalan.
                        st["goals"] = len(new_scorers)
                        st["scorers"] = new_scorers
                        if status == "STATUS_HALFTIME":
                            st["ht"] = True
                        if status in ("STATUS_FINAL", "STATUS_FULL_TIME"):
                            st["ft"] = True
                        continue
                    # Gol dibatalkan (VAR): skor turun, atau ada pencetak gol
                    # yang hilang dari daftar. Cari siapa yang dibatalkan.
                    cancelled = None
                    if hs < old_hs or as_ < old_as:
                        old_keys = {_scorer_key(g) for g in st["scorers"]}
                        gone = [g for g in st["scorers"] if _scorer_key(g) not in {_scorer_key(g) for g in new_scorers}]
                        cancelled = gone[0] if gone else {"name": "", "minute": "", "kind": "", "team": ""}
                    elif len(new_scorers) < len(st["scorers"]):
                        gone = [g for g in st["scorers"] if _scorer_key(g) not in {_scorer_key(g) for g in new_scorers}]
                        if gone:
                            cancelled = gone[0]
                    if cancelled is not None:
                        who = f"{cancelled['name']} {cancelled['minute']}".strip()
                        team_name = hn if hs < old_hs else an
                        extra = f"\nGol {who} ({team_name}) dibatalkan." if who else f"\nGol {team_name} dibatalkan (VAR)."
                        livescore_send(f"❌ GOL DIBATALKAN — Nations League {league}\n{hn} {hs} - {as_} {an}{extra}")
                    # Gol baru (bisa lebih dari satu dalam satu interval):
                    # putar ulang daftar gol untuk dapat skor berjalan tiap gol.
                    rh = ra = 0
                    for idx, g in enumerate(new_scorers):
                        if g["team"] == hshort:
                            rh += 1
                        elif g["team"] == ashort:
                            ra += 1
                        elif rh < hs:
                            rh += 1
                        else:
                            ra += 1
                        if idx >= st["goals"]:
                            suffix = " (bunuh diri)" if g["kind"] == "own" else (" (penalti)" if g["kind"] == "pen" else "")
                            livescore_send(f"⚽ GOL! — Nations League {league}\n{hn} {rh} - {ra} {an}\n{g['name']} {g['minute']}{suffix}")
                    st["goals"] = len(new_scorers)
                    st["scorers"] = new_scorers
                    if status == "STATUS_HALFTIME" and not st["ht"]:
                        st["ht"] = True
                        livescore_send(f"⏸ HALF TIME — Nations League {league}\n{hn} {hs} - {as_} {an}\n{livescore_format_scorers(new_scorers)}")
                    if status in ("STATUS_FINAL", "STATUS_FULL_TIME") and not st["ft"]:
                        st["ft"] = True
                        livescore_send(f"🏁 FULL TIME — Nations League {league}\n{hn} {hs} - {as_} {an}\n{livescore_format_scorers(new_scorers)}")
            first_poll = False
            if all(s["ft"] for s in state.values()):
                print("Livescore: semua pertandingan selesai, thread berhenti", flush=True)
                return
        except Exception as e:
            print(f"Livescore error: {e}", flush=True)
        time.sleep(60)
    print("Livescore: batas poll tercapai, thread berhenti", flush=True)


# Thread latar: pantau gol via LiveScore (untuk pertandingan yang tidak ada
# di ESPN, mis. U21 Friendly). Poll endpoint tanggal LiveScore tiap 60 detik,
# kirim ke grup kalau ada gol, gol dibatalkan (VAR), half time, dan full time.
# LiveScore tidak kasih nama pencetak gol di endpoint ini, jadi notif gol
# cuma bawa skor. Tanpa token Muse AI, cuma program biasa. Berhenti sendiri
# setelah semua pertandingan selesai.
LIVESCORE_LS_GROUP_ID = -1003337200559
LIVESCORE_LS_DATE = "20261116"
LIVESCORE_LS_URL = "https://prod-public-api.livescore.com/v1/api/app/date/soccer/20261116/0"
# event_id -> (tim kandang, tim tandang, label liga, jam kickoff WIB)
# Kosong untuk sekarang: tes Ario vs Saipa (Liga Iran) sudah selesai FT 0-1.
# Isi lagi kalau ada pertandingan tes baru yang tidak ada di ESPN.
LIVESCORE_LS_MATCHES = {
}
LIVESCORE_LS_TEAM_ID = {
    "Ario Islamshahr": "Ario Islamshahr",
    "Saipa": "Saipa",
}
LIVESCORE_LS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}

def livescore_ls_run():
    if not LIVESCORE_LS_MATCHES:
        print("Livescore-LS: tidak ada pertandingan, thread tidak jalan", flush=True)
        return
    print("Livescore-LS thread started - Liga Iran malam ini (1 pertandingan tes)", flush=True)
    lines = ["⚽ Pantau gol Liga Iran malam ini (tes):"]
    for eid, (h, a, lg, jam) in LIVESCORE_LS_MATCHES.items():
        hn = LIVESCORE_LS_TEAM_ID.get(h, h)
        an = LIVESCORE_LS_TEAM_ID.get(a, a)
        lines.append(f"• {hn} vs {an} — {jam} WIB")
    lines.append("Bot kirim notif kalau ada gol, gol dibatalkan (VAR), half time, dan full time.")
    livescore_send("\n".join(lines))
    state = {eid: {"home": 0, "away": 0, "ht": False, "ft": False, "last_minute": None} for eid in LIVESCORE_LS_MATCHES}
    first_poll = True
    polls = 0
    while polls < 300:
        polls += 1
        try:
            r = requests.get(LIVESCORE_LS_URL, headers=LIVESCORE_LS_HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
            for stg in data.get("Stages", []):
                for ev in stg.get("Events", []):
                    eid = str(ev.get("Eid"))
                    if eid not in state:
                        continue
                    st = state[eid]
                    if st["ft"]:
                        continue
                    t1 = (ev.get("T1") or [{}])[0]
                    t2 = (ev.get("T2") or [{}])[0]
                    hn = LIVESCORE_LS_TEAM_ID.get(t1.get("Nm", ""), t1.get("Nm", ""))
                    an = LIVESCORE_LS_TEAM_ID.get(t2.get("Nm", ""), t2.get("Nm", ""))
                    _, _, league, _ = LIVESCORE_LS_MATCHES[eid]
                    hs = int(ev.get("Tr1") or 0)
                    as_ = int(ev.get("Tr2") or 0)
                    status = ev.get("Eps", "")
                    minute = _ls_minute(status)
                    old_hs, old_as = st["home"], st["away"]
                    st["home"], st["away"] = hs, as_
                    if status in ("NS", ""):
                        continue
                    if first_poll:
                        if status == "HT" or (minute is not None and minute >= 46):
                            st["ht"] = True
                        if status == "FT":
                            st["ft"] = True
                        st["last_minute"] = minute
                        continue
                    if hs < old_hs or as_ < old_as:
                        team_name = hn if hs < old_hs else an
                        livescore_send(f"❌ GOL DIBATALKAN — {league}\n{hn} {hs} - {as_} {an}\nGol {team_name} dibatalkan (VAR).")
                    elif hs > old_hs or as_ > old_as:
                        livescore_send(f"⚽ GOL! — {league}\n{hn} {hs} - {as_} {an}")
                    if not st["ht"]:
                        if status == "HT":
                            st["ht"] = True
                            livescore_send(f"⏸ HALF TIME — {league}\n{hn} {hs} - {as_} {an}")
                        elif (minute is not None and minute >= 46
                                and st.get("last_minute") is not None and st["last_minute"] <= 45):
                            # Sudah babak kedua tapi "HT" tidak pernah terlihat
                            # (LiveScore kelewat) — kirim HT susulan biar tidak hilang.
                            st["ht"] = True
                            livescore_send(f"⏸ HALF TIME — {league}\n{hn} {hs} - {as_} {an}")
                    if minute is not None:
                        st["last_minute"] = minute
                    if status == "FT" and not st["ft"]:
                        st["ft"] = True
                        livescore_send(f"🏁 FULL TIME — {league}\n{hn} {hs} - {as_} {an}")
            first_poll = False
            if all(s["ft"] for s in state.values()):
                print("Livescore-LS: semua pertandingan selesai, thread berhenti", flush=True)
                return
        except Exception as e:
            print(f"Livescore-LS error: {e}", flush=True)
        time.sleep(60)
    print("Livescore-LS: batas poll tercapai, thread berhenti", flush=True)


def main():
    me = api("getMe")
    print(f"Bot online as @{me.get('username')} (id {me.get('id')}) - reaction mode, only user {ALLOWED_USER_ID}, novan -> sticker, rakun -> sticker, tiktok/youtube -> download, /search -> photo, /dice -> game, /mon -> monster battle", flush=True)
    try:
        with open(USERNAME_FILE) as f:
            USERNAME_MAP.update({str(k).lower(): int(v) for k, v in json.load(f).items()})
    except Exception:
        pass
    threading.Thread(target=mon_sweeper, daemon=True).start()
    threading.Thread(target=cuaca_pagi_thread, daemon=True).start()
    # Livescore DIMATIKAN 2026-11-17 atas permintaan Taro (tidak ada notif gol otomatis lagi).
    # threading.Thread(target=livescore_run, daemon=True).start()
    # threading.Thread(target=livescore_ls_run, daemon=True).start()
    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(f"{BASE}/getUpdates", params=params, timeout=35)
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                print(f"getUpdates not ok: {data}", flush=True)
                time.sleep(3)
                continue
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                cq = update.get("callback_query")
                if cq:
                    mon_handle_callback(cq)
                    continue
                msg = update.get("message")
                if not msg:
                    continue
                chat_id = msg["chat"]["id"]
                message_id = msg["message_id"]
                sender_id = msg.get("from", {}).get("id")
                sender_uname = msg.get("from", {}).get("username")
                if sender_id and sender_uname:
                    key = sender_uname.lower()
                    if USERNAME_MAP.get(key) != sender_id:
                        USERNAME_MAP[key] = sender_id
                        try:
                            with open(USERNAME_FILE, "w") as f:
                                json.dump(USERNAME_MAP, f)
                        except Exception:
                            pass
                text = msg.get("text", "")
                # Tangkap stiker yang dikirim ke bot, simpan file_id-nya
                # supaya bot bisa kirim stiker itu lagi nanti.
                sticker = msg.get("sticker")
                if sticker:
                    file_id = sticker.get("file_id")
                    s_emoji = sticker.get("emoji")
                    set_name = sticker.get("set_name")
                    # Hanya stiker dari Taro yang boleh jadi stiker trigger "rakun".
                    # Stiker dari orang lain diabaikan supaya tidak menimpa stiker Taro.
                    if sender_id != ALLOWED_USER_ID:
                        print(f"Ignored sticker from non-owner {sender_id} set={set_name} message {message_id} (not saved)", flush=True)
                        continue
                    # Hanya stiker yang dikirim di chat pribadi dengan bot yang
                    # disimpan sebagai stiker "rakun". Stiker yang dikirim Taro
                    # di grup (mis. stiker kucing buat seru-seruan) diabaikan
                    # supaya tidak menimpa stiker rakun.
                    if msg["chat"].get("type") == "private":
                        print(f"Sticker received file_id={file_id} emoji={s_emoji} set={set_name} message {message_id} from {sender_id}", flush=True)
                        if sender_id in AWAITING_NOVAN_STICKER:
                            # Taro habis kirim /setnovan: stiker ini jadi stiker Novan.
                            AWAITING_NOVAN_STICKER.discard(sender_id)
                            try:
                                with open(NOVAN_STICKER_FILE, "w") as f:
                                    f.write(file_id or "")
                                api(
                                    "sendMessage",
                                    chat_id=chat_id,
                                    text="Stiker Novan udah diganti.",
                                    reply_to_message_id=message_id,
                                )
                                print(f"Saved Novan sticker file_id={file_id} message {message_id}", flush=True)
                            except Exception as e:
                                print(f"Failed to save Novan sticker file_id: {e}", flush=True)
                        else:
                            try:
                                with open(os.path.join(os.path.dirname(__file__), "last_sticker.txt"), "w") as f:
                                    f.write(file_id or "")
                            except Exception as e:
                                print(f"Failed to save sticker file_id: {e}", flush=True)
                    else:
                        print(f"Ignored group sticker from owner set={set_name} message {message_id} (not saved as rakun sticker)", flush=True)
                    react(chat_id, message_id)
                    continue
                # Perintah /setnovan: Taro kirim di chat pribadi, lalu kirim
                # stiker yang mau dipakai buat reply trigger "novan".
                # Dicek sebelum trigger Novan karena "/setnovan" mengandung "novan".
                if text.startswith("/setnovan") and msg["chat"].get("type") == "private" and sender_id == ALLOWED_USER_ID:
                    AWAITING_NOVAN_STICKER.add(sender_id)
                    api(
                        "sendMessage",
                        chat_id=chat_id,
                        text="Oke, kirim stiker buat Novan sekarang.",
                        reply_to_message_id=message_id,
                    )
                    print(f"/setnovan from {sender_id}, awaiting Novan sticker", flush=True)
                    continue
                # Aturan Novan: berlaku untuk siapa saja, reply stiker Novan.
                # Kalau belum ada stiker atau kirim stiker gagal (mis. media
                # diblokir di grup), reply 🐶 dulu.
                if TRIGGER_PHRASE in text.lower():
                    novan_sticker = get_novan_sticker_file_id()
                    sent = False
                    if novan_sticker:
                        try:
                            api(
                                "sendSticker",
                                chat_id=chat_id,
                                sticker=novan_sticker,
                                reply_to_message_id=message_id,
                            )
                            sent = True
                            print(f"Replied Novan sticker to message {message_id} from {sender_id}", flush=True)
                        except Exception as e:
                            print(f"Failed to send Novan sticker for {message_id}: {e}", flush=True)
                    if not sent:
                        api(
                            "sendMessage",
                            chat_id=chat_id,
                            text=DOG_EMOJI,
                            reply_to_message_id=message_id,
                        )
                        print(f"Replied {DOG_EMOJI} to Novan message {message_id} from {sender_id}", flush=True)
                    # Reaksi juga untuk pesan Taro: handler trigger ini
                    # tadinya return awal sebelum kode reaksi, jadi pesan
                    # "novan" dari Taro cuma dapat stiker tanpa reaksi.
                    if sender_id == ALLOWED_USER_ID:
                        react(chat_id, message_id)
                    continue
                # Aturan rakun: berlaku untuk siapa saja, reply stiker custom
                if TRIGGER_RAKUN in text.lower():
                    sticker_id = get_sticker_file_id()
                    if sticker_id:
                        api(
                            "sendSticker",
                            chat_id=chat_id,
                            sticker=sticker_id,
                            reply_to_message_id=message_id,
                        )
                        print(f"Replied sticker to rakun message {message_id} from {sender_id}", flush=True)
                    else:
                        print(f"Rakun trigger but no sticker saved yet, message {message_id}", flush=True)
                    # Reaksi juga untuk pesan Taro: handler trigger ini
                    # tadinya return awal sebelum kode reaksi, jadi pesan
                    # "rakun" dari Taro cuma dapat stiker tanpa reaksi.
                    if sender_id == ALLOWED_USER_ID:
                        react(chat_id, message_id)
                    continue
                # Perintah /youtube <link>: download video YouTube (termasuk Shorts)
                # lewat API, berlaku untuk siapa saja.
                if text.startswith("/youtube"):
                    yt_arg = text[len("/youtube"):].strip()
                    yt_url = find_youtube_url(yt_arg) if yt_arg else None
                    if not yt_url:
                        api(
                            "sendMessage",
                            chat_id=chat_id,
                            text="Ketik /youtube <link YouTube>, contoh: /youtube https://youtu.be/xxxxx",
                            reply_to_message_id=message_id,
                        )
                    else:
                        threading.Thread(
                            target=download_youtube,
                            args=(chat_id, message_id, yt_url, sender_id),
                            daemon=True,
                        ).start()
                    continue
                # Perintah /tiktok <link>: download video TikTok tanpa watermark,
                # berlaku untuk siapa saja.
                if text.startswith("/tiktok"):
                    tt_arg = text[len("/tiktok"):].strip()
                    tt_url = find_tiktok_url(tt_arg) if tt_arg else None
                    if not tt_url:
                        api(
                            "sendMessage",
                            chat_id=chat_id,
                            text="Ketik /tiktok <link TikTok>, contoh: /tiktok https://vt.tiktok.com/xxxxx",
                            reply_to_message_id=message_id,
                        )
                    else:
                        threading.Thread(
                            target=download_tiktok,
                            args=(chat_id, message_id, tt_url, sender_id),
                            daemon=True,
                        ).start()
                    continue
                # Perintah /search <keyword>: berlaku untuk siapa saja,
                # cari gambar Pinterest lalu kirim fotonya.
                if text.startswith("/search"):
                    keyword = text[len("/search"):].strip()
                    if not keyword:
                        api(
                            "sendMessage",
                            chat_id=chat_id,
                            text="Ketik /search <keyword>, contoh: /search anime",
                            reply_to_message_id=message_id,
                        )
                    else:
                        threading.Thread(
                            target=do_search,
                            args=(chat_id, message_id, keyword, sender_id),
                            daemon=True,
                        ).start()
                    continue
                # Game dadu /dice dan cek saldo /balance: berlaku untuk siapa saja.
                if text.startswith("/dice"):
                    threading.Thread(
                        target=do_dice,
                        args=(chat_id, message_id, sender_id, msg.get("from", {}).get("first_name", "kamu")),
                        daemon=True,
                    ).start()
                    continue
                if text.startswith("/balance"):
                    api(
                        "sendMessage",
                        chat_id=chat_id,
                        text=f"💰 Saldo {msg.get('from', {}).get('first_name', 'kamu')}: {get_balance(sender_id)} koin",
                        reply_to_message_id=message_id,
                    )
                    continue
                # Game monster: berlaku untuk siapa saja.
                if text.startswith("/mon"):
                    do_mon(chat_id, message_id, sender_id, msg.get("from", {}).get("first_name", "kamu"))
                    continue
                if text.startswith("/catch") or text.startswith("/cari"):
                    do_catch(chat_id, message_id, sender_id, msg.get("from", {}).get("first_name", "kamu"))
                    continue
                if text.startswith("/toko"):
                    do_toko(chat_id, message_id, sender_id, msg.get("from", {}).get("first_name", "kamu"))
                    continue
                if text.startswith("/hadiah"):
                    do_hadiah(chat_id, message_id, sender_id, msg.get("from", {}).get("first_name", "kamu"))
                    continue
                if text.startswith("/team"):
                    do_team(chat_id, message_id, sender_id, msg.get("from", {}).get("first_name", "kamu"))
                    continue
                if text.startswith("/heal"):
                    do_heal(chat_id, message_id, sender_id, msg.get("from", {}).get("first_name", "kamu"))
                    continue
                if text.startswith("/battle"):
                    do_battle(chat_id, message_id, sender_id, msg.get("from", {}).get("first_name", "kamu"), msg)
                    continue
                # /cuaca: cuaca kota random di Indonesia, berlaku untuk siapa saja.
                if text.startswith("/cuaca"):
                    arg = text[len("/cuaca"):].strip()
                    threading.Thread(
                        target=do_cuaca,
                        args=(chat_id, message_id, sender_id, arg),
                        daemon=True,
                    ).start()
                    continue
                # Hanya Taro yang boleh dapat reaksi
                if sender_id != ALLOWED_USER_ID:
                    continue
                if text.startswith("/start"):
                    api(
                        "sendMessage",
                        chat_id=chat_id,
                        text="Halo! Sekarang aku nggak balas pesan, tapi aku kasih reaksi emoji random di pesan kamu. Coba kirim pesan apa aja.",
                    )
                    react(chat_id, message_id)
                elif text.startswith("/help"):
                    api(
                        "sendMessage",
                        chat_id=chat_id,
                        text="Kirim pesan apa aja, aku kasih 1 reaksi emoji random di pesan itu. Telegram cuma izinkan bot kasih 1 reaksi per pesan, jadi nggak bisa 3 sekaligus.\n\nPerintah lain:\n/search <keyword> - cari gambar Pinterest, contoh: /search anime\n/youtube <link> - download video YouTube, contoh: /youtube https://youtu.be/xxxxx\n/tiktok <link> - download video TikTok tanpa watermark\n/dice - main dadu, 2 dadu berputar lalu berhenti\n/balance - cek saldo koin kamu\n/cuaca - cuaca kota random di Indonesia (atau /cuaca <nama kota>)\n/setnovan - ganti stiker Novan (kirim di chat pribadi, lalu kirim stikernya)\n\nGame monster:\n/mon - lihat monster & Pokeball kamu (awal: 1 Pokeball, belum punya monster)\n/catch - cari monster liar & tangkap pakai Pokeball\n/toko - beli Pokeball pakai koin\n/hadiah - klaim 1 Pokeball gratis tiap hari\n/team - lihat tim & ganti monster aktif\n/battle - tantang battle (reply ke pesan lawan, atau /battle @username)\n/heal - pulihkan HP monster aktif",
                    )
                else:
                    react(chat_id, message_id)
        except Exception as e:
            print(f"Error: {e}", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()

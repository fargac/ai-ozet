import os
import json
import logging
import threading
import re
import concurrent.futures
from dataclasses import dataclass, asdict
from typing import Optional, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# --- 1. AYARLAR VE LOGLAMA ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

GAZETELER = [
    {"id": "manset_aksam",        "name": "Akşam",         "slug": "aksam",          "link": "https://www.aksam.com.tr"},
    {"id": "manset_aydinlik",     "name": "Aydınlık",      "slug": "aydinlik",        "link": "https://www.aydinlik.com.tr"},
    {"id": "manset_dirilis_postasi","name":"Diriliş P.",   "slug": "dirilis-postasi", "link": "https://www.dirilispostasi.com"},
    {"id": "manset_dogru-haber",  "name": "Doğru Haber",   "slug": "dogru-haber",     "link": "https://dogruhaber.com.tr"},
    {"id": "manset_dunya",        "name": "Dünya",         "slug": "dunya",           "link": "https://www.dunya.com"},
    {"id": "manset_hurriyet",     "name": "Hürriyet",      "slug": "hurriyet",        "link": "https://www.hurriyet.com.tr"},
    {"id": "manset_milat",        "name": "Milat",         "slug": "milat",           "link": "https://www.milatgazetesi.com"},
    {"id": "manset_milli_gazete", "name": "Milli Gazete",  "slug": "milli-gazete",    "link": "https://www.milligazete.com.tr"},
    {"id": "manset_milliyet",     "name": "Milliyet",      "slug": "milliyet",        "link": "https://www.milliyet.com.tr"},
    {"id": "manset_sabah",        "name": "Sabah",         "slug": "sabah",           "link": "https://www.sabah.com.tr"},
    {"id": "manset_takvim",       "name": "Takvim",        "slug": "takvim-gazetesi", "link": "https://www.takvim.com.tr"},
    {"id": "manset_turkgun",      "name": "Türkgün",       "slug": "turkgun",         "link": "https://www.turkgun.com"},
    {"id": "manset_turkiye",      "name": "Türkiye",       "slug": "turkiye",         "link": "https://www.turkiyegazetesi.com.tr"},
    {"id": "manset_yeni_akit",    "name": "Yeni Akit",     "slug": "yeni-akit",       "link": "https://www.yeniakit.com.tr"},
    {"id": "manset_yeni_birlik",  "name": "Yeni Birlik",   "slug": "yeni-birlik",     "link": "https://www.gazetebirlik.com"},
    {"id": "manset_yeni_cag",     "name": "Yeniçağ",       "slug": "yenicag",         "link": "https://www.yenicaggazetesi.com.tr"},
    {"id": "manset_yeni_safak",   "name": "Yeni Şafak",    "slug": "yeni-safak",      "link": "https://www.yenisafak.com"},
    {"id": "manset_fotomac",      "name": "Fotomaç",       "slug": "fotomac",         "link": "https://www.fotomac.com.tr"},
    {"id": "manset_fanatik",      "name": "Fanatik",       "slug": "fanatik",         "link": "https://www.fanatik.com.tr"},
]

# ✅ DÜZELTME 1: User-Agent KESİNLİKLE masaüstü olarak sabitlendi.
# Sunucu UA'ya göre farklı HTML dönüyor:
#   - Masaüstü: <img src="https://.../big_DDMMYY_HHMM.jpg">        ← scraper bunu bulur
#   - Mobil:    <img data-lazy="https://.../small_big_DDMMYY_HHMM.jpg"> ← farklı format, src boş
# Requests kütüphanesi varsayılan olarak "python-requests/x.x.x" UA'sı gönderir.
# Haber7 bunu mobil olarak algılayıp mobil HTML verebilir. Sabit masaüstü UA zorunlu.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    # ✅ DÜZELTME 2: Accept-Language ve Sec- header'ları eklendi.
    # Bazı CDN'ler bu header'ların yokluğunda bot tespiti yapıp farklı içerik döner.
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Fetch-Dest":  "document",
    "Sec-Fetch-Mode":  "navigate",
    "Sec-Fetch-Site":  "none",
    "Sec-Fetch-User":  "?1",
    "Upgrade-Insecure-Requests": "1",
}

# --- 2. VERİ MODELİ ---
@dataclass
class GazeteManseti:
    id: str
    name: str
    todayUrl: str
    thumbUrl: str
    webAdresi: str

# --- 3. SESSION VE RETRY YÖNETİMİ ---
thread_local = threading.local()

def get_session() -> requests.Session:
    if not hasattr(thread_local, "session"):
        session = requests.Session()
        session.headers.update(HEADERS)
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        thread_local.session = session
    return thread_local.session

# --- 4. GÖRSEL DOĞRULAMA ---
def check_image_url(session: requests.Session, url: str, min_size_bytes: int = 10_000) -> bool:
    try:
        response = session.head(url, timeout=10, allow_redirects=True)
        if response.status_code != 200:
            return False
        content_type = response.headers.get("Content-Type", "").lower()
        if not content_type.startswith("image/"):
            return False
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) < min_size_bytes:
            logger.warning(f"Placeholder tespit edildi ({content_length} bytes): {url}")
            return False
        return True
    except requests.RequestException:
        return False

# --- 5. URL ÇIKARMA (Tüm format varyantlarını destekler) ---
def extract_image_url(soup: BeautifulSoup) -> Optional[str]:
    """
    Haber7'nin hem masaüstü hem de olası diğer HTML varyantlarından
    gazete manşeti görsel URL'sini çıkarır.

    Bilinen format varyantları:
      A) Masaüstü: <img src="https://.../big_DDMMYY_HHMM.jpg?v=...">
      B) Mobil:    <img data-lazy="https://.../small_big_DDMMYY_HHMM.jpg">
      C) Lazy:     <img data-src="https://.../big_DDMMYY_HHMM.jpg">

    Strateji:
      1. Önce src/data-src/data-lazy attribute'larında "/big_" ara → en güvenilir
      2. Bulunamazsa "small_big_" formatını ara → mobil fallback
      3. URL'den tarih+saat bilgisini regex ile doğrula → sahte URL koruması
    """
    # Taranan attribute sırası: src en güvenilir, data-lazy mobil fallback
    ATTRS_TO_CHECK = ["src", "data-src", "data-lazy", "data-original"]

    # ── Strateji 1: /big_ içeren URL ────────────────────────────────────
    for img in soup.find_all("img"):
        for attr in ATTRS_TO_CHECK:
            val = img.get(attr, "")
            if "/big_" in val and val.startswith("http"):
                logger.debug(f"big_ bulundu ({attr}): {val}")
                return val

    # ── Strateji 2: small_big_ formatı (mobil varyant) ──────────────────
    # Örnek: https://i12.haber7.net/haber7/gazete/dunya/small_big_010626_0800.jpg
    # Bu URL'den big_ versiyonunu türetebiliriz: small_big_ → big_
    for img in soup.find_all("img"):
        for attr in ATTRS_TO_CHECK:
            val = img.get(attr, "")
            if "/small_big_" in val and val.startswith("http"):
                # small_big_ → big_ dönüşümü
                big_url = val.replace("/small_big_", "/big_")
                logger.debug(f"small_big_ → big_ dönüştürüldü: {big_url}")
                return big_url

    # ── Strateji 3: Tüm img src'lerini tara, tarih paterni ara ──────────
    # Haber7 URL formatı: big_DDMMYY_HHMM.jpg
    DATE_PATTERN = re.compile(r'/(?:big|small)_\d{6}_\d{4}\.jpg')
    for img in soup.find_all("img"):
        for attr in ATTRS_TO_CHECK:
            val = img.get(attr, "")
            if DATE_PATTERN.search(val) and val.startswith("http"):
                # small_ ile başlıyorsa big_'e çevir
                if "/small_" in val and "/small_big_" not in val:
                    val = val.replace("/small_", "/big_")
                logger.debug(f"Tarih paterni ile bulundu ({attr}): {val}")
                return val

    return None

def build_thumb_url(big_url: str) -> str:
    """
    big_ URL'sinden thumb URL'si üretir.
    Örnek: .../big_300526_0800.jpg?v=300526_0800
       → .../small_300526_0800.jpg?300526_0800
    """
    return big_url.replace("/big_", "/small_")

# --- 6. İŞ MANTIĞI ---
def process_gazete(gazete: dict) -> Optional[GazeteManseti]:
    session = get_session()
    # ✅ www. prefix'i zorunlu — haber7.com bazı redirect'leri farklı HTML ile yanıtlar
    url = f"https://www.haber7.com/gazete-mansetleri/{gazete['slug']}"

    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        big_url = extract_image_url(soup)

        if not big_url:
            logger.warning(f"Görsel bulunamadı: {gazete['name']} — HTML yapısı değişmiş olabilir.")
            # ✅ DÜZELTME 3: Bulunamama durumunda ilk 200 karakteri logla — debug kolaylaşır
            first_imgs = soup.find_all("img")[:3]
            for dbg_img in first_imgs:
                logger.debug(f"  Bulunan img: {dict(dbg_img.attrs)}")
            return None

        thumb_url = build_thumb_url(big_url)

        # Thumb geçerli mi kontrol et
        is_thumb_valid = check_image_url(session, thumb_url)

        if not is_thumb_valid:
            # Thumb yoksa big_'i hem todayUrl hem thumbUrl olarak kullan
            logger.warning(f"Thumb geçersiz, big_ fallback kullanılıyor: {gazete['name']}")
            is_big_valid = check_image_url(session, big_url)
            if not is_big_valid:
                logger.error(f"Her iki URL de geçersiz, atlanıyor: {gazete['name']}")
                return None
            final_thumb = big_url
        else:
            final_thumb = thumb_url

        logger.info(f"✅ Başarılı: {gazete['name']}")

        return GazeteManseti(
            id=gazete["id"],
            name=gazete["name"],
            todayUrl=big_url,
            thumbUrl=final_thumb,
            webAdresi=gazete["link"]
        )

    except Exception as e:
        logger.error(f"Hata ({gazete['name']}): {str(e)}")
        return None

# --- 7. ORKESTRASYON ---
def main():
    logger.info("Manşetler PARALEL olarak çekiliyor...")

    sonuclar: List[dict] = []
    max_workers = min(10, len(GAZETELER))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_gazete = {executor.submit(process_gazete, g): g for g in GAZETELER}
        for future in concurrent.futures.as_completed(future_to_gazete):
            result = future.result()
            if result is not None:
                sonuclar.append(asdict(result))

    sonuclar_sirali = sorted(sonuclar, key=lambda x: x["id"])

    temp_file  = "mansetler.tmp.json"
    final_file = "mansetler.json"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(sonuclar_sirali, f, ensure_ascii=False, indent=4)

    os.replace(temp_file, final_file)
    logger.info(f"✅ İşlem tamam! {len(sonuclar_sirali)} manşet başarıyla kaydedildi.")

if __name__ == "__main__":
    main()
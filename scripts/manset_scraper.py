import os
import json
import logging
import concurrent.futures
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict

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
    {"id": "manset_aydinlik",     "name": "Aydınlık",      "slug": "aydinlik",       "link": "https://www.aydinlik.com.tr"},
    {"id": "manset_dirilis_postasi","name":"Diriliş P.",   "slug": "dirilis-postasi", "link": "https://www.dirilispostasi.com"},
    {"id": "manset_dogru-haber",  "name": "Doğru Haber",   "slug": "dogru-haber",    "link": "https://dogruhaber.com.tr"},
    {"id": "manset_dunya",        "name": "Dünya",         "slug": "dunya",          "link": "https://www.dunya.com"},
    {"id": "manset_hurriyet",     "name": "Hürriyet",      "slug": "hurriyet",       "link": "https://www.hurriyet.com.tr"},
    {"id": "manset_milat",        "name": "Milat",         "slug": "milat",          "link": "https://www.milatgazetesi.com"},
    {"id": "manset_milli_gazete", "name": "Milli Gazete",  "slug": "milli-gazete",   "link": "https://www.milligazete.com.tr"},
    {"id": "manset_milliyet",     "name": "Milliyet",      "slug": "milliyet",       "link": "https://www.milliyet.com.tr"},
    {"id": "manset_sabah",        "name": "Sabah",         "slug": "sabah",          "link": "https://www.sabah.com.tr"},
    {"id": "manset_takvim",       "name": "Takvim",        "slug": "takvim-gazetesi", "link": "https://www.takvim.com.tr"},
    {"id": "manset_turkgun",      "name": "Türkgün",       "slug": "turkgun",        "link": "https://www.turkgun.com"},
    {"id": "manset_turkiye",      "name": "Türkiye",       "slug": "turkiye",        "link": "https://www.turkiyegazetesi.com.tr"},
    {"id": "manset_yeni_akit",    "name": "Yeni Akit",     "slug": "yeni-akit",      "link": "https://www.yeniakit.com.tr"},
    {"id": "manset_yeni_birlik",  "name": "Yeni Birlik",   "slug": "yeni-birlik",    "link": "https://www.gazetebirlik.com"},
    {"id": "manset_yeni_cag",     "name": "Yeniçağ",       "slug": "yenicag",        "link": "https://www.yenicaggazetesi.com.tr"},
    {"id": "manset_yeni_safak",   "name": "Yeni Şafak",    "slug": "yeni-safak",     "link": "https://www.yenisafak.com"},
    {"id": "manset_fotomac",      "name": "Fotomaç",       "slug": "fotomac",        "link": "https://www.fotomac.com.tr"},
    {"id": "manset_fanatik",      "name": "Fanatik",       "slug": "fanatik",        "link": "https://www.fanatik.com.tr"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
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

# --- 3. SESSION VE YÖNETİMİ ---
def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

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
            return False
        return True
    except requests.RequestException:
        return False

# --- 5. TOPLU KAYNAK PARÇALAMA (O(1) Request) ---
def fetch_slider_data(session: requests.Session) -> Dict[str, str]:
    """
    Tüm manşetleri barındıran sayfaya tek bir istek atıp slider içerisindeki
    'slug' -> 'thumb_url' eşleşmelerini çıkarır.
    """
    url = "https://www.haber7.com/"
    
    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Ana sayfa çekilirken hata oluştu: {e}")
        return {}

    soup = BeautifulSoup(response.text, "html.parser")
    slider = soup.find("div", class_="newspaper-slider")
    
    slider_data = {}
    if not slider:
        logger.error("HTML yapısında 'newspaper-slider' div'i bulunamadı.")
        return slider_data

    for a_tag in slider.find_all("a"):
        href = a_tag.get("href", "")
        if not href:
            continue
            
        slug = href.rstrip("/").split("/")[-1]
        img_tag = a_tag.find("img")
        
        if img_tag:
            # Sırasıyla iletilen çıktıdaki muhtemel öznitelikler kontrol ediliyor.
            img_url = img_tag.get("data-original") or img_tag.get("data-lazy") or img_tag.get("src")
            if img_url and ("small_" in img_url or "big_" in img_url):
                slider_data[slug] = img_url

    return slider_data

# --- 6. İŞ MANTIĞI VE DOĞRULAMA ---
def process_gazete(gazete: dict, raw_url: str, session: requests.Session) -> Optional[GazeteManseti]:
    """
    Toplanan raw_url (genellikle small_) üzerinden thumb ve big url'leri oluşturur,
    HEAD isteği ile varlıklarını asenkron doğrular.
    """
    if "small_" in raw_url:
        thumb_url = raw_url
        big_url = raw_url.replace("/small_", "/big_")
    else:
        big_url = raw_url
        thumb_url = raw_url.replace("/big_", "/small_")

    is_thumb_valid = check_image_url(session, thumb_url)
    is_big_valid = check_image_url(session, big_url)

    final_thumb = thumb_url if is_thumb_valid else (big_url if is_big_valid else None)
    final_big = big_url if is_big_valid else (thumb_url if is_thumb_valid else None)

    if not final_thumb or not final_big:
        logger.warning(f"Görseller doğrulanamadı, atlanıyor: {gazete['name']}")
        return None

    logger.info(f"✅ Başarılı: {gazete['name']}")
    
    return GazeteManseti(
        id=gazete["id"],
        name=gazete["name"],
        todayUrl=final_big,
        thumbUrl=final_thumb,
        webAdresi=gazete["link"]
    )

# --- 7. ORKESTRASYON ---
def main():
    session = get_session()
    logger.info("Ana kaynaktan (Slider) manşet verileri toplanıyor...")
    
    # 1. Aşama: Tek GET isteği ile tüm HTML parse işlemi
    slider_data = fetch_slider_data(session)

    if not slider_data:
        logger.error("Slider verisi okunamadı. Program sonlandırılıyor.")
        return

    logger.info(f"{len(slider_data)} adet manşet tespit edildi. Görseller doğrulanıyor (Asenkron HEAD)...")
    
    sonuclar: List[dict] = []
    max_workers = min(20, len(GAZETELER))

    # 2. Aşama: Sadece lightweight HEAD istekleriyle eş zamanlı doğrulama
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for gazete in GAZETELER:
            slug = gazete["slug"]
            if slug in slider_data:
                futures.append(executor.submit(process_gazete, gazete, slider_data[slug], session))
            else:
                logger.warning(f"Slider verisinde bulunamadı: {gazete['name']}")

        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                sonuclar.append(asdict(result))

    sonuclar_sirali = sorted(sonuclar, key=lambda x: x["id"])

    temp_file  = "mansetler.tmp.json"
    final_file = "mansetler.json"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(sonuclar_sirali, f, ensure_ascii=False, indent=4)

    os.replace(temp_file, final_file)
    logger.info(f"✅ İşlem tamam! {len(sonuclar_sirali)} manşet dosyaya yazıldı.")

if __name__ == "__main__":
    main()
import os
import json
import logging
import threading
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
    {"id": "manset_aksam", "name": "Akşam", "slug": "aksam", "link": "https://www.aksam.com.tr"},
    {"id": "manset_aydinlik", "name": "Aydınlık", "slug": "aydinlik", "link": "https://www.aydinlik.com.tr"},
    {"id": "manset_dirilis_postasi", "name": "Diriliş P.", "slug": "dirilis-postasi", "link": "https://www.dirilispostasi.com"},
    {"id": "manset_dogru-haber", "name": "Doğru Haber", "slug": "dogru-haber", "link": "https://dogruhaber.com.tr"},
    {"id": "manset_dunya", "name": "Dünya", "slug": "dunya", "link": "https://www.dunya.com"},
    {"id": "manset_hurriyet", "name": "Hürriyet", "slug": "hurriyet", "link": "https://www.hurriyet.com.tr"},
    {"id": "manset_milat", "name": "Milat", "slug": "milat", "link": "https://www.milatgazetesi.com"},
    {"id": "manset_milli_gazete", "name": "Milli Gazete", "slug": "milli-gazete", "link": "https://www.milligazete.com.tr"},
    {"id": "manset_milliyet", "name": "Milliyet", "slug": "milliyet", "link": "https://www.milliyet.com.tr"},
    {"id": "manset_sabah", "name": "Sabah", "slug": "sabah", "link": "https://www.sabah.com.tr"},
    {"id": "manset_takvim", "name": "Takvim", "slug": "takvim-gazetesi", "link": "https://www.takvim.com.tr"},
    {"id": "manset_turkgun", "name": "Türkgün", "slug": "turkgun", "link": "https://www.turkgun.com"},
    {"id": "manset_turkiye", "name": "Türkiye", "slug": "turkiye", "link": "https://www.turkiyegazetesi.com.tr"},
    {"id": "manset_yeni_akit", "name": "Yeni Akit", "slug": "yeni-akit", "link": "https://www.yeniakit.com.tr"},
    {"id": "manset_yeni_birlik", "name": "Yeni Birlik", "slug": "yeni-birlik", "link": "https://www.gazetebirlik.com"},
    {"id": "manset_yeni_cag", "name": "Yeniçağ", "slug": "yenicag", "link": "https://www.yenicaggazetesi.com.tr"},
    {"id": "manset_yeni_safak", "name": "Yeni Şafak", "slug": "yeni-safak", "link": "https://www.yenisafak.com"},
    {"id": "manset_fotomac", "name": "Fotomaç", "slug": "fotomac", "link": "https://www.fotomac.com.tr"},
    {"id": "manset_fanatik", "name": "Fanatik", "slug": "fanatik", "link": "https://www.fanatik.com.tr"}
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}

# --- 2. VERİ MODELİ (Dataclass) ---
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
    """
    Her thread için, otomatik 'Retry' (Yeniden Deneme) özelliğine sahip
    kalıcı bir Session döndürür.
    """
    if not hasattr(thread_local, "session"):
        session = requests.Session()
        session.headers.update(HEADERS)
        
        # Sunucu 500, 502, 503, 504 veya 429 dönerse otomatik tekrar dener (max 3 kez).
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

# --- 4. GÖRSEL DOĞRULAMA (HEAD İsteği) ---
def check_image_url(session: requests.Session, url: str) -> bool:
    """
    Sadece HTTP başlıklarını çekerek (HEAD isteği ile) görselin varlığını
    ve tipini çok hızlı bir şekilde doğrular.
    """
    try:
        response = session.head(url, timeout=10, allow_redirects=True)
        if response.status_code == 200:
            content_type = response.headers.get("Content-Type", "").lower()
            return content_type.startswith("image/")
        return False
    except requests.RequestException:
        return False

# --- 5. İŞ MANTIĞI (Scraping) ---
def process_gazete(gazete: dict) -> Optional[GazeteManseti]:
    """Bir gazetenin sayfasını indirir, parse eder ve linklerin çalıştığını doğrular."""
    session = get_session()
    url = f"https://www.haber7.com/gazete-mansetleri/{gazete['slug']}"
    
    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        big_url = None
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if "/big_" in src:
                big_url = src
                break
                
        if not big_url:
            logger.warning(f"Görsel HTML'de bulunamadı: {gazete['name']}")
            return None

        # Small URL'yi oluştur
        small_url = big_url.replace("/big_", "/small_").replace("?v=", "?")
        
        # Linklerin çalışıp çalışmadığını kontrol et
        is_small_valid = check_image_url(session, small_url)
        is_big_valid = check_image_url(session, big_url)
        
        # İkisi de çalışmıyorsa reddet.
        if not is_big_valid and not is_small_valid:
            logger.error(f"Kırık Linkler (Sunucuda Yok): {gazete['name']}")
            return None
            
        final_thumb = small_url if is_small_valid else big_url
        final_big = big_url if is_big_valid else final_thumb
        
        logger.info(f"Başarılı: {gazete['name']}")
        
        return GazeteManseti(
            id=gazete["id"],
            name=gazete["name"],
            todayUrl=final_big,
            thumbUrl=final_thumb,
            webAdresi=gazete["link"]
        )

    except Exception as e:
        logger.error(f"Hata ({gazete['name']}): {str(e)}")
        return None

# --- 6. ORKESTRASYON ---
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

    # JSON çıktısında gazetelerin her zaman aynı sırada olması için
    sonuclar_sirali = sorted(sonuclar, key=lambda x: x["id"])

    temp_file = "mansetler.tmp.json"
    final_file = "mansetler.json"
    
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(sonuclar_sirali, f, ensure_ascii=False, indent=4)
        
    os.replace(temp_file, final_file)
    logger.info(f"İşlem tamam! Çalışan {len(sonuclar_sirali)} manşet başarıyla kaydedildi.")

if __name__ == "__main__":
    main()
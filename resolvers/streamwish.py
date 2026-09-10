import re
import json
import random
from urllib.parse import urlparse, urljoin
import logging

def get_best_resolution(master_playlist, base_url):
    try:
        lines = master_playlist.split('\n')
        best_url = None
        best_score = 0

        for i in range(len(lines)):
            match = re.search(r'RESOLUTION=(\d+)x(\d+)', lines[i])
            if match:
                next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if not next_line or next_line.startswith('#'):
                    continue

                score = int(match.group(1)) * int(match.group(2))
                if score > best_score:
                    best_score = score
                    best_url = urljoin(base_url, next_line)
        return best_url
    except Exception as e:
        return None

def unpack(p, a, c, k, e=None, d=None):
    """Simple Dean Edwards Packer unpacker"""
    def unbase(value, base):
        # Convert base `a` encoded integer
        if value == 0: return "0"
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        res = ""
        while value > 0:
            res = alphabet[value % base] + res
            value //= base
        return res or "0"

    k = k.split('|')
    
    def replace_func(match):
        word = match.group(0)
        # convert word from base `a` to integer
        # Actually in standard packer, the word is base 36 or 62 encoded
        val = 0
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for char in word:
            if char in alphabet:
                val = val * a + alphabet.index(char)
        
        return k[val] if val < len(k) and k[val] else word

    # Reemplaza usando \b\w+\b
    unpacked = re.sub(r'\b\w+\b', replace_func, p)
    return unpacked

def extract_packer_args(script_content):
    match = re.search(r'eval\(function\(p,a,c,k,e,d\).*?return p\}\(\'(.*?)\',(\d+),(\d+),\'(.*?)\'\.split\(\'\|\'\)', script_content, re.DOTALL)
    if match:
        p = match.group(1).replace("\\'", "'")
        a = int(match.group(2))
        c = int(match.group(3))
        k = match.group(4)
        return p, a, c, k
    return None

async def extract_streamwish(engine_session, page_url, fetch_html_func):
    logging.info(f"[SW RESOLVER] Resolviendo: {page_url}")
    
    # Redirección anti-DMCA
    dmca = ["strwish.com", "strmwis.com", "dwish.pro", "awish.pro", "mwish.pro", "swish.pro", "wishfast.top", "embedwish.com", "playnixes.com", "medixiru.com"]
    main = ["kravaxxa.com", "davioad.com", "haxloppd.com", "tryzendm.com", "dumbalag.com", "hlswish.com"]
    rules = ["dhcplay.com", "hglink.to", "test.hglink.to", "wish-redirect.aiavh.com"]

    parsed_url = urlparse(page_url)
    destination = random.choice(main) if parsed_url.hostname in rules else random.choice(dmca)
    final_url = f"https://{destination}{parsed_url.path}?{parsed_url.query}"
    
    html = await fetch_html_func(engine_session, final_url)
    if not html:
        logging.warning(f"[SW RESOLVER] Mirror {destination} failed, trying original URL {page_url}")
        html = await fetch_html_func(engine_session, page_url)
        final_url = page_url

    if not html:
        return None

    script_match = re.search(r'<script[^>]*type=[\'"]text/javascript[\'"][^>]*>\s*(eval\(function\(p,a,c,k,e,d\)[\s\S]*?)</script>', html, re.IGNORECASE)
    if not script_match:
        logging.error("[SW RESOLVER] Script packed no encontrado")
        return None

    packed_js = script_match.group(1)
    args = extract_packer_args(packed_js)
    
    if not args:
        logging.error(f"[SW DEBUG] No se pudieron extraer argumentos. Primeros 200 chars: {packed_js[:200]}")
        logging.error(f"[SW DEBUG] Últimos 200 chars: {packed_js[-200:]}")
        return None
        
    p, a, c, k = args
    unpacked = unpack(p, a, c, k)
    
    links_match = re.search(r'var\s+links\s*=\s*(\{[\s\S]*?\});', unpacked, re.IGNORECASE)
    if not links_match:
        # Intenta un json simple en file:"(.*?.m3u8)"
        m3u8_match = re.search(r'file\s*:\s*[\'"]([^\'"]+\.m3u8[^\'"]*)[\'"]', unpacked)
        if m3u8_match:
            master_url = m3u8_match.group(1)
        else:
            logging.error(f"[SW DEBUG] Regex de links y file fallaron. Unpacked snippet: {unpacked[:300]}")
            return None
    else:
        try:
            links_json = links_match.group(1).replace("'", '"')
            # Arreglar llaves sin comillas (ej. hls4: -> "hls4":)
            # Solo reemplazar si está después de un { o , para evitar romper https:
            links_json = re.sub(r'([{,]\s*)([a-zA-Z0-9_]+)\s*:', r'\1"\2":', links_json)
            links = json.loads(links_json)
            link = links.get('hls4') or links.get('hls3') or links.get('hls1') or links.get('hls2')
            if not link:
                logging.error("[SW DEBUG] El json 'links' no contenía hls1-4.")
                return None
            master_url = urljoin(final_url, link) if link.startswith('/') else link
        except Exception as e:
            logging.error(f"[SW DEBUG] Error procesando json de links: {e}. Json string: {links_json}")
            return None

    logging.info(f"[SW RESOLVER] Master playlist encontrada: {master_url}")
    
    try:
        # Intentar obtener la mejor resolución
        playlist = await fetch_html_func(engine_session, master_url)
        if playlist:
            base = master_url[:master_url.rfind('/') + 1]
            best_url = get_best_resolution(playlist, base) or master_url
            return best_url
    except:
        pass
        
    return master_url

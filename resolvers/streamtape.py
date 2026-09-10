import re
import logging
from urllib.parse import urlparse

async def extract_streamtape(engine_session, page_url, fetch_html_func):
    logging.info(f"[ST RESOLVER] Resolviendo: {page_url}")
    try:
        html = await fetch_html_func(engine_session, page_url)
        if not html:
            return None

        # En Streamtape, el enlace se genera con una concatenación como:
        # document.getElementById('robotlink').innerHTML = '//streamtape.com/get_video?id=' + 'xxx' + '&token=' + 'yyy';
        match_id = re.search(r"getElementById\('norobotlink'\)\.innerHTML\s*=\s*(.*?);", html)
        if not match_id:
            match_id = re.search(r"getElementById\('robotlink'\)\.innerHTML\s*=\s*(.*?);", html)

        if not match_id:
            logging.error("[ST RESOLVER] No se encontraron los tokens de Streamtape")
            return None

        # Extraer los strings de la concatenación
        concat_str = match_id.group(1)
        tokens = re.findall(r"['\"]([^'\"]+)['\"]", concat_str)
        
        # Hay subcadenas que se eliminan usando substring
        # Streamtape usa un trick: token = 'xyz'.substring(2)
        # Una forma robusta es buscar el token de 14+ caracteres en el HTML 
        # que acompaña al "&token="
        token_match = re.search(r"&token=([a-zA-Z0-9_-]+)", html)
        id_match = re.search(r"id=([a-zA-Z0-9_-]+)", html)

        if not tokens:
            return None

        url_part = ""
        for t in tokens:
            if t.startswith('//'):
                url_part += "https:" + t
            else:
                url_part += t

        # A veces el url_part tiene un &token= incompleto que se reemplaza
        # Intentaremos buscar un token limpio en el documento
        clean_token = re.search(r"['\"]?&token=([^'\"]+)['\"]?", html)
        if clean_token:
            url_part = re.sub(r"&token=[^'\"]*", "&token=" + clean_token.group(1), url_part)

        # Arreglos rápidos
        url_part = url_part.replace("'+'", "")
        url_part = url_part.replace("+", "")
        
        if url_part.startswith("//"):
            url_part = "https:" + url_part
            
        # Remover partes rotas de substring ej. 'xyz'.substring(1)
        url_part = re.sub(r"'\.substring\(\d+\)", "", url_part)

        logging.info(f"[ST RESOLVER] URL generada: {url_part}")
        
        # Validar que es correcta
        if "&token=" in url_part and "get_video" in url_part:
            return url_part
        return None

    except Exception as e:
        logging.error(f"[ST RESOLVER] Error: {e}")
        return None

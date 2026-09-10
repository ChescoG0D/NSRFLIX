import re
import json
import base64
import logging

def rot13(s):
    result = []
    for c in s:
        if 'a' <= c <= 'z':
            result.append(chr((ord(c) - ord('a') + 13) % 26 + ord('a')))
        elif 'A' <= c <= 'Z':
            result.append(chr((ord(c) - ord('A') + 13) % 26 + ord('A')))
        else:
            result.append(c)
    return ''.join(result)

def sanitize_special_chars(s):
    patterns = ['@$', '^^', '~@', '%?', '*~', '!!', '#&']
    for p in patterns:
        s = s.replace(p, '_')
    return s

def remove_underscores(s):
    return s.replace('_', '')

def decode_base64(s):
    # Padding recovery
    s += '=' * (-len(s) % 4)
    return base64.b64decode(s).decode('utf-8')

def shift_chars(s, shift):
    return ''.join(chr(ord(c) - shift) for c in s)

def reverse_string(s):
    return s[::-1]

def decode_obfuscated_data(obfuscated):
    try:
        step = rot13(obfuscated)
        step = sanitize_special_chars(step)
        step = remove_underscores(step)
        step = decode_base64(step)
        step = shift_chars(step, 3)
        step = reverse_string(step)
        step = decode_base64(step)
        return json.loads(step)
    except Exception as e:
        logging.error(f"[VOE Resolver] Error decoding data: {e}")
        return None

async def extract_voe(engine_session, page_url, fetch_html_func):
    """
    Resuelve el link directo de VOE extrayendo y decodificando 
    la carga útil JSON ofuscada dentro del script.
    """
    logging.info(f"[VOE RESOLVER] Resolviendo: {page_url}")
    try:
        html = await fetch_html_func(engine_session, page_url)
        if not html:
            return None
            
        # Detectar redirección JS
        if 'window.location.href' in html:
            match = re.search(r"window\.location\.href\s*=\s*['\"]([^'\"]+)['\"]", html)
            if match and match.group(1):
                redirect_url = match.group(1)
                logging.info(f"[VOE RESOLVER] Redirección detectada a: {redirect_url}")
                html = await fetch_html_func(engine_session, redirect_url)
                if not html:
                    return None

        # Buscar el script JSON ofuscado
        import bs4
        soup = bs4.BeautifulSoup(html, 'html.parser')
        scripts = soup.find_all('script', type='application/json')
        
        obfuscated = None
        for script in scripts:
            content = script.string
            if content:
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], str):
                        obfuscated = content
                        break
                except:
                    pass
        
        if not obfuscated:
            logging.error("[VOE RESOLVER] No se encontró el script JSON ofuscado")
            return None
            
        data = decode_obfuscated_data(obfuscated)
        if not data:
            logging.error("[VOE RESOLVER] Falló decodificación de datos ofuscados")
            return None
            
        final_url = data.get('direct_access_url') or data.get('source')
        if final_url:
            logging.info(f"[VOE RESOLVER] URL resuelta exitosamente")
        return final_url
        
    except Exception as e:
        logging.error(f"[VOE RESOLVER] Error inesperado: {e}")
        return None

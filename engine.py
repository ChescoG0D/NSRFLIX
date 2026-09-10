import aiohttp
import asyncio
import random
from bs4 import BeautifulSoup
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class AsyncEngine:
    def __init__(self, max_concurrent=5):
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]
        
        self.headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
        }

    def get_random_headers(self):
        headers = self.headers.copy()
        headers['User-Agent'] = random.choice(self.user_agents)
        return headers

    async def fetch_html(self, session, url, max_retries=3, method="GET", data=None, headers=None):
        """Descarga el HTML de una URL de forma asíncrona."""
        async with self.semaphore:
            for attempt in range(max_retries):
                try:
                    # Pequeño delay aleatorio para no saturar el servidor
                    await asyncio.sleep(random.uniform(0.5, 2.0))
                    
                    req_func = session.get if method.upper() == "GET" else session.post
                    req_headers = self.get_random_headers()
                    if headers:
                        req_headers.update(headers)
                    req_kwargs = {"headers": req_headers, "timeout": 15, "allow_redirects": True}
                    if data:
                        req_kwargs["data"] = data

                    async with req_func(url, **req_kwargs) as response:
                        if response.status == 200:
                            content_type = response.headers.get('Content-Type', '')
                            if 'text/html' in content_type:
                                raw_bytes = await response.read()
                                return raw_bytes.decode('utf-8', errors='replace')
                            else:
                                logging.warning(f"La respuesta no es HTML en {url}: {content_type}")
                                return None
                        elif response.status == 404:
                            logging.error(f"Error 404 en {url}")
                            return None
                        elif response.status in [403, 503]:
                            logging.warning(f"Posible bloqueo (Cloudflare) en {url} (HTTP {response.status})")
                            
                        response.raise_for_status()
                except Exception as e:
                    logging.warning(f"Intento {attempt+1}/{max_retries} falló para {url}: {e}")
                    if attempt == max_retries - 1:
                        logging.error(f"Fallo definitivo para {url}")
                        return None
                    await asyncio.sleep(random.uniform(2, 5))
            return None

    def parse_html(self, html):
        """Retorna el objeto BeautifulSoup listo para extraer datos."""
        if not html:
            return None
        return BeautifulSoup(html, 'html.parser')

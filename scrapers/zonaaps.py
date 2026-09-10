import re
import datetime
import urllib.parse
import json
import logging
import asyncio
import aiohttp
from urllib.parse import urljoin

class ZonaapsScraper:
    def __init__(self, base_url="https://zonaaps.com"):
        self.base_url = base_url
        self.provider = "zonaaps"
        self.worker_api = "https://zonaapp.ikkihkurogane.workers.dev/extract?url="

    def _get_absolute_url(self, path):
        if not path:
            return ""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.base_url, path)

    def _extract_slug(self, path):
        if not path:
            return ""
        parts = [p for p in path.split("/") if p]
        return parts[-1] if parts else ""

    def get_categories_urls(self, pages=3, category="all"):
        """Genera las URLs a iterar por cada categoría."""
        urls = []
        # Solo extraemos series de TV como solicitó el usuario
        categories = ["/genre/series-de-tv/"]
        
        for cat in categories:
            for page in range(1, pages + 1):
                if page == 1:
                    url = f"{self.base_url}{cat}"
                else:
                    url = f"{self.base_url}{cat}page/{page}/"
                urls.append((url, "series"))
        return urls

    def parse_catalog(self, soup):
        """Parsea una página de catálogo (ej. /genre/series-de-tv/) y retorna lista de series."""
        items = []
        if not soup:
            return items

        # Dooplay utiliza <article class="item">
        articles = soup.select("article.item")
        
        for el in articles:
            a_tag = el.select_one("a")
            if not a_tag:
                continue
                
            href = a_tag.get("href", "")
            
            # Título
            title_tag = el.select_one("h3")
            title = title_tag.get_text(strip=True) if title_tag else ""
            
            # Póster
            img_tag = el.select_one("img")
            poster = ""
            if img_tag:
                poster = img_tag.get("data-lazy-src") or img_tag.get("data-src") or img_tag.get("src", "")
                poster = self._get_absolute_url(poster)
                
            # Rating
            rating_tag = el.select_one(".rating")
            rating = rating_tag.get_text(strip=True) if rating_tag else None
            
            # Year
            year_tag = el.select_one(".year")
            year = year_tag.get_text(strip=True) if year_tag else None
            
            slug = self._extract_slug(href)
            
            if slug and title:
                items.append({
                    "id": slug,
                    "slug": slug,
                    "title": title,
                    "poster": poster,
                    "rating": rating,
                    "year": year,
                    "type": "series",
                    "url": self._get_absolute_url(href),
                    "provider": self.provider
                })
        
        return items

    def parse_details(self, soup, slug, content_type="series"):
        """Parsea la página de detalles de una serie en Zonaaps."""
        if not soup:
            return None
        
        # Título principal
        title_tag = soup.select_one("h1")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            return None 
        
        # Título original (Dooplay suele tenerlo en extra info)
        original_title = ""
        
        # Sinopsis
        synopsis_tag = soup.select_one(".wp-content p") or soup.select_one("#info p")
        synopsis = synopsis_tag.get_text(strip=True) if synopsis_tag else ""
        
        # Póster
        poster_tag = soup.select_one(".poster img")
        poster = ""
        if poster_tag:
            poster = poster_tag.get("data-lazy-src") or poster_tag.get("data-src") or poster_tag.get("src", "")
            poster = self._get_absolute_url(poster)
        
        # Backdrop
        backdrop = ""
        style_tag = soup.find(lambda t: t.name == 'style' and '#dt_contenedor' in t.text and 'background-image:url' in t.text.replace(' ', ''))
        if style_tag:
            m = re.search(r'background-image:\s*url\((.*?)\)', style_tag.text)
            if m:
                backdrop = self._get_absolute_url(m.group(1).strip("'\""))
        if not backdrop:
            backdrop_tag = soup.select_one(".mvic-thumb") or soup.select_one(".g-thumb")
            if backdrop_tag and backdrop_tag.get("style"):
                m = re.search(r'url\((.*?)\)', backdrop_tag.get("style"))
                if m:
                    backdrop = self._get_absolute_url(m.group(1).strip("'\""))
        
        # Rating
        rating_tag = soup.select_one(".res-score") or soup.select_one(".imdb")
        rating = rating_tag.get_text(strip=True) if rating_tag else None
        
        # Year
        year_tag = soup.select_one(".date")
        year = year_tag.get_text(strip=True) if year_tag else None
                
        # Géneros
        genres = []
        for genre_link in soup.select(".sgeneros a"):
            text = genre_link.get_text(strip=True)
            href = genre_link.get("href", "")
            g_slug = self._extract_slug(href)
            if text:
                genres.append({"name": text, "slug": g_slug})
                
        # Extraer Temporadas y Episodios (Dooplay style)
        seasons = []
        div_seasons = soup.select(".se-c")
        if div_seasons:
            for div in div_seasons:
                s_tag = div.select_one(".se-q .title")
                if not s_tag:
                    continue
                s_match = re.search(r'\d+', s_tag.get_text(strip=True))
                s_num = int(s_match.group(0)) if s_match else 1
                
                episodes = []
                for li in div.select(".episodios li"):
                    ep_a = li.select_one(".episodiotitle a")
                    if not ep_a:
                        continue
                    
                    ep_href = ep_a.get("href", "")
                    ep_title = ep_a.get_text(strip=True)
                    
                    num_div = li.select_one(".numerando")
                    e_num = 1
                    if num_div:
                        num_text = num_div.get_text(strip=True)
                        n_match = re.search(r'\d+\s*-\s*(\d+)', num_text)
                        if n_match:
                            e_num = int(n_match.group(1))
                            
                    episodes.append({
                        "number": e_num,
                        "title": ep_title or f"Episodio {e_num}",
                        "url": self._get_absolute_url(ep_href),
                        "season": s_num
                    })
                    
                episodes.sort(key=lambda x: x["number"])
                if episodes:
                    seasons.append({
                        "number": s_num,
                        "name": f"Temporada {s_num}",
                        "episodes": episodes
                    })
            seasons.sort(key=lambda x: x["number"])
                
        result = {
            "id": slug,
            "slug": slug,
            "title": title,
            "originalTitle": original_title,
            "synopsis": synopsis,
            "poster": poster,
            "backdrop": backdrop,
            "rating": rating,
            "year": year,
            "genres": genres,
            "type": content_type,
            "url": self._get_absolute_url(f"/tvshows/{slug}/"),
            "provider": self.provider,
            "scraped_at": datetime.datetime.now().isoformat()
        }
        if seasons:
            result["seasons"] = seasons
            
        return {k: v for k, v in result.items() if v}
            
    async def async_parse_details(self, session, engine, soup, slug, content_type, url):
        """Método unificado para uso asíncrono desde el api.py"""
        return self.parse_details(soup, slug, content_type)

    async def async_get_servers(self, session, episode_url):
        """Resuelve los servidores de un solo episodio usando el Worker de Cloudflare."""
        servers = []
        api_url = f"{self.worker_api}{urllib.parse.quote(self._get_absolute_url(episode_url), safe='')}"
        
        try:
            async with session.get(api_url, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    streams = data.get("streams", [])
                    if streams and isinstance(streams, list):
                        # Buscar primero el stream HLS que funciona (/stream/ts)
                        working_stream = next((st.get("url") for st in streams if st.get("url") and "/stream/ts" in st.get("url")), None)
                        if not working_stream and len(streams) > 1:
                            working_stream = streams[1].get("url")
                        if not working_stream and len(streams) > 0:
                            working_stream = streams[0].get("url")
                            
                        if working_stream:
                            servers.append({
                                "name": "Zonaaps HLS (0 Anuncios)",
                                "embedUrl": working_stream,
                                "isHls": True,
                                "language": "Latino"
                            })
                    
                    # También agregar el embed web oficial de MovieDays/Zonaaps como respaldo
                    trace = data.get("resolutionTrace", [])
                    if trace and isinstance(trace, list):
                        for tr in trace:
                            embed_page = tr.get("embedUrl")
                            if embed_page and embed_page.startswith("http"):
                                servers.append({
                                    "name": "MovieDays (Servidor Web)",
                                    "embedUrl": embed_page,
                                    "isHls": False,
                                    "language": "Latino"
                                })
        except Exception as e:
            logging.warning(f"Error resolviendo M3U8 para {episode_url}: {e}")
            
        return servers


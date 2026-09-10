import re
import datetime
from urllib.parse import urljoin

class RePelisHDScraper:
    def __init__(self, base_url="https://repelishd.fit"):
        self.base_url = base_url
        self.provider = "repelishd"

    def _get_absolute_url(self, path):
        if not path:
            return ""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.base_url, path)

    def _detect_type_from_path(self, path):
        if not path:
            return "movie"
        if "-online-espanol" in path and "serie" not in path:
            return "movie"
        if "serie" in path:
            return "series"
        return "movie"

    def _extract_slug(self, path):
        if not path:
            return ""
        parts = [p for p in path.split("/") if p]
        return parts[-1] if parts else ""

    def parse_home(self, soup):
        """Parsea la página principal de RePelisHD y devuelve secciones de películas."""
        sections = {}
        if not soup:
            return sections
            
        # Películas en Cines
        cines = []
        cines_container = soup.select_one("#featured-titles")
        if cines_container:
            cines = self.parse_catalog(cines_container)
        sections["cines"] = cines
        
        # Películas Recomendadas
        recomendadas = []
        rec_container = soup.select_one("#estado_actualizadas")
        if rec_container:
            recomendadas = self.parse_catalog(rec_container)
        sections["recomendadas"] = recomendadas
        
        return sections

    def parse_catalog(self, soup):
        """Parsea una página de catálogo de RePelisHD y retorna lista de películas."""
        items = []
        if not soup:
            return items

        # RePelisHD usa .item.movies
        links = soup.select("article.item.movies, div.item")
        
        for el in links:
            a_tag = el.select_one("a[href]")
            if not a_tag:
                continue
                
            href = a_tag.get("href", "")
            
            img_tag = el.select_one("img")
            
            # El título a veces está en un h2/h3, o en el alt de la imagen
            title_tag = el.select_one("h2, h3, .entry-title")
            title = title_tag.get_text(strip=True) if title_tag else (img_tag.get("alt", "") if img_tag else "")
            
            poster = self._get_absolute_url(img_tag.get("data-src") or img_tag.get("src", "")) if img_tag else ""
            
            rating_tag = el.select_one(".rating, .vote")
            rating = rating_tag.get_text(strip=True) if rating_tag else None
            
            content_type = self._detect_type_from_path(href)
            slug = self._extract_slug(href)
            
            if slug and title:
                items.append({
                    "id": slug,
                    "slug": slug,
                    "title": title,
                    "poster": poster,
                    "rating": rating,
                    "type": content_type,
                    "url": self._get_absolute_url(href),
                    "provider": self.provider
                })
        
        return items

    def parse_details(self, soup, slug, content_type="movie"):
        """Parsea la página de detalles."""
        if not soup:
            return None
        
        title_tag = soup.select_one(".data h1") or soup.select_one("h1")
        title = title_tag.get_text(strip=True) if title_tag else ""
        # Limpiar el título
        title = re.sub(r'(?i)\s+online\s+hd$', '', title)
        title = re.sub(r'(?i)\s+online$', '', title)
        title = re.sub(r'(?i)^ver\s+serie\s+', '', title)
        title = re.sub(r'(?i)^ver\s+pelicula\s+', '', title)
        if not title:
            return None 
            
        synopsis_tag = soup.select_one(".data span p") or soup.select_one(".entry-content, .sinopsis, #synopsis, .wp-content")
        synopsis = synopsis_tag.get_text(strip=True) if synopsis_tag else ""
        
        poster_tag = soup.select_one(".poster img") or soup.select_one(".image img")
        poster = ""
        if poster_tag:
            poster_url = poster_tag.get("data-src") or poster_tag.get("src", "")
            poster = self._get_absolute_url(poster_url)
        
        rating = None
        
        year_tag = soup.select_one(".year, .date")
        year = None
        if year_tag:
            match = re.search(r'\d{4}', year_tag.get_text(strip=True))
            if match:
                year = match.group(0)
                
        genres = []
        for span in soup.select(".extra span"):
            if span.get("class"): continue
            text = span.get_text(strip=True)
            if text and not re.match(r'^\d{4}$', text):
                g_slug = self._extract_slug(text)
                genres.append({"name": text, "slug": g_slug})
                
        if not genres:
            for genre_link in soup.select(".genres a, .sgeneros a"):
                text = genre_link.get_text(strip=True)
                href = genre_link.get("href", "")
                g_slug = self._extract_slug(href)
                if text:
                    genres.append({"name": text, "slug": g_slug})
                
        servers = []
        for iframe in soup.select("iframe"):
            src = iframe.get("src")
            if src and "youtube" not in src:
                servers.append({
                    "name": "RePelisHD Server",
                    "embedUrl": src
                })
                
        result = {
            "id": slug,
            "slug": slug,
            "title": title,
            "originalTitle": title,
            "synopsis": synopsis,
            "poster": poster,
            "rating": rating,
            "year": year,
            "genres": genres,
            "servers": servers,
            "type": content_type,
            # No sobreescribir la URL porque ya la obtuvimos correctamente en parse_catalog
            "provider": self.provider,
            "scraped_at": datetime.datetime.now().isoformat()
        }
        
        # Clean up empty values so we don't overwrite good data from the catalog
        return {k: v for k, v in result.items() if v}

    async def async_parse_details(self, session, engine, soup, slug, content_type, url):
        """Versión asíncrona que extrae los servidores reales escondidos en el iframe de RePelisHD."""
        details = self.parse_details(soup, slug, content_type)
        if not details:
            return None
            
        iframe_src = None
        for iframe in soup.select("iframe"):
            src = iframe.get("src", "")
            if src and ("verhdlink" in src or "/video/" in src):
                iframe_src = self._get_absolute_url(src)
                break
                
        if iframe_src:
            try:
                # Obtenemos el HTML del iframe que contiene los verdaderos servidores
                html = await engine.fetch_html(session, iframe_src, headers={"Referer": url})
                if html:
                    r_soup = engine.parse_html(html)
                    real_servers = []
                    
                    languages = [
                        {"key": "latino", "label": "Latino"},
                        {"key": "castellano", "label": "Castellano"},
                        {"key": "subtitulado", "label": "Subtitulado"}
                    ]
                    
                    for lang in languages:
                        for li in r_soup.select(f"ul.{lang['key']} li"):
                            data_link = li.get("data-link", "")
                            if not data_link:
                                continue
                                
                            embed_url = data_link
                            if embed_url.startswith("//"):
                                embed_url = "https:" + embed_url
                                
                            text = li.get_text(strip=True).lower()
                            
                            server_key = "unknown"
                            server_name = "Directo"
                            
                            if "dropload" in text or "dr0pstream" in embed_url or "dropload" in embed_url:
                                server_key = "dropload"
                                server_name = "Dropload"
                            elif "mixdrop" in text or "mixdrop" in embed_url:
                                server_key = "mixdrop"
                                server_name = "Mixdrop"
                            elif "doodstream" in text or "dood" in text or "dood" in embed_url:
                                server_key = "doodstream"
                                server_name = "Doodstream"
                            elif "streamwish" in text or "wish" in text or "streamwish" in embed_url:
                                server_key = "streamwish"
                                server_name = "Streamwish"
                            elif "voe" in text or "voe.sx" in embed_url:
                                server_key = "voe"
                                server_name = "Voe"
                            elif "fullhd" in text or "4k" in text:
                                server_key = "server4k"
                                server_name = "Server 4K"
                            elif text:
                                server_name = text.title()
                                
                            real_servers.append({
                                "name": server_name,
                                "server": server_key,
                                "language": lang["label"],
                                "embedUrl": embed_url
                            })
                            
                    if real_servers:
                        details["servers"] = real_servers
            except Exception as e:
                import logging
                logging.error(f"Error extrayendo servidores de RePelisHD para {slug}: {e}")
                
        return details

    def get_categories_urls(self, pages=2):
        """Genera las URLs a iterar por cada categoría en RePelisHD."""
        urls = []
        categories = ["pelicula", "series", "anime", "home"]
        for cat in categories:
            for page in range(1, pages + 1):
                if cat == "home":
                    url = f"{self.base_url}/page/{page}/" if page > 1 else f"{self.base_url}/"
                else:
                    url = f"{self.base_url}/{cat}/page/{page}/" if page > 1 else f"{self.base_url}/{cat}/"
                urls.append((url, cat))
        return urls

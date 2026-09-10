import re
import datetime
from urllib.parse import urljoin

class PelisPlusScraper:
    def __init__(self, base_url="https://www.pelisplushd.la"):
        self.base_url = base_url
        self.provider = "pelisplus"

    def _get_absolute_url(self, path):
        if not path:
            return ""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.base_url, path)

    def _detect_type_from_path(self, path):
        if not path:
            return "movie"
        # Reglas estrictas de PelisPlus:
        # PelisPlus SOLO provee series y anime, NUNCA películas.
        if "/serie/" in path:
            return "series"
        if "/animes/" in path or "/anime/" in path or "/anime/" in path.lower():
            return "anime"
        # Cualquier otra ruta (incluidas /pelicula/ y resultados de búsqueda
        # con rutas raras) NO se considera válida para pelisplus -> la tratamos
        # como complejo para que parse_catalog la descarte.
        return "blocked"

    def _extract_slug(self, path):
        if not path:
            return ""
        parts = [p for p in path.split("/") if p]
        return parts[-1] if parts else ""

    def parse_catalog(self, soup):
        """Parsea una página de catálogo o búsqueda y retorna lista de películas."""
        items = []
        if not soup:
            return items

        # Basado en la estructura descubierta en PeliApi: a.Posters-link
        links = soup.select("a.Posters-link")
        
        # Resultados de búsqueda pueden usar otra estructura; recoger
        # los enlaces hacia /serie/ y /anime/ por si el selector principal falla
        if not links:
            links = soup.select('a[href*="/serie/"], a[href*="/animes/"], a[href*="/anime/"]')
        
        for el in links:
            href = el.get("href", "")
            
            # Título: intenta atributo data-title, sino busca en .listing-content p
            title = el.get("data-title")
            if not title:
                p_tag = el.select_one(".listing-content p")
                title = p_tag.get_text(strip=True) if p_tag else ""
            
            # Poster
            img_tag = el.select_one("img.Posters-img")
            poster = self._get_absolute_url(img_tag.get("data-src") or img_tag.get("src", "")) if img_tag else ""
            
            # Rating
            rating_span = el.select_one(".rating span")
            rating = None
            if rating_span:
                rating_text = rating_span.get_text(strip=True)
                rating = rating_text.split("/")[0] if rating_text else None
            
            content_type = self._detect_type_from_path(href)
            
            # Solo permitir series y animes (PelisPlus NUNCA provee películas)
            if content_type in ("movie", "blocked"):
                continue
                
                
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
        """Parsea la página de detalles de una película/serie."""
        if not soup:
            return None
        
        # Título principal
        title_tag = soup.select_one("h1.m-b-5") or soup.select_one(".font-size-26.font-weight-bold")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            return None # No se encontró el título
        
        original_title_tag = soup.select_one("p.text-opacity")
        original_title = original_title_tag.get_text(strip=True) if original_title_tag else ""
        
        synopsis_tag = soup.select_one(".text-large") or soup.select_one("#synopsis")
        synopsis = synopsis_tag.get_text(strip=True) if synopsis_tag else ""
        
        poster_tag = soup.select_one("img.img-fluid.m-b-15") or soup.select_one(".img-fluid.rounded") or soup.select_one("img.img-fluid.m-b-30")
        poster = self._get_absolute_url(poster_tag.get("src", "")) if poster_tag else ""
        
        rating_tag = soup.select_one(".font-size-36.font-weight-bold")
        rating = rating_tag.get_text(strip=True) if rating_tag else None
        
        year_tag = soup.select_one(".font-size-18.text-info")
        year = None
        if year_tag:
            match = re.search(r'\d{4}', year_tag.get_text(strip=True))
            if match:
                year = match.group(0)
                
        # Géneros
        genres = []
        for genre_link in soup.select("a[href^='/generos/']"):
            text = genre_link.get_text(strip=True)
            href = genre_link.get("href", "")
            g_slug = self._extract_slug(href)
            if text:
                genres.append({"name": text, "slug": g_slug})
                
        # Extraer Servidores
        servers = []
        
        # Método 1 (Moderno - Usado en Series / Peliculas)
        if soup.select("#link_url span"):
            server_names_map = {}
            for li in soup.select(".TbVideoNv li, .VideoPlayer li"):
                lid = li.get("data-id") or li.get("lid")
                name = li.get_text(strip=True)
                if lid and name:
                    server_names_map[lid] = name
                    
            for span in soup.select("#link_url span"):
                lid = span.get("lid")
                embed_url = span.get("url")
                if embed_url:
                    name = server_names_map.get(lid, "Desconocido")
                    servers.append({
                        "name": name,
                        "embedUrl": self._get_absolute_url(embed_url)
                    })
                    
        # Método 2 (Clásico - Usado en Películas)
        if not servers:
            for li in soup.select("li.playurl"):
                embed_url = li.get("data-url")
                if embed_url:
                    a_tag = li.select_one("a")
                    name = a_tag.get_text(strip=True) if a_tag else li.get_text(strip=True)
                    if not name: name = "Desconocido"
                    servers.append({
                        "name": name,
                        "embedUrl": self._get_absolute_url(embed_url)
                    })
                
        # Extraer Temporadas y Episodios si es una Serie o Anime
        seasons = []
        if content_type in ["series", "anime"]:
            div_seasons = soup.select(".divseason")
            if div_seasons:
                for div in div_seasons:
                    season_title = div.get_text(strip=True)
                    s_match = re.search(r'\d+', season_title)
                    s_num = int(s_match.group(0)) if s_match else 1
                    
                    episodes = []
                    # Siguiente hermano <ul> contiene los capitulos
                    next_ul = div.find_next_sibling("ul")
                    if next_ul:
                        for a_tag in next_ul.select("a"):
                            ep_href = a_tag.get("href", "")
                            ep_title = a_tag.get_text(strip=True)
                            ep_match = re.search(r'temporada/(\d+)/capitulo/(\d+)', ep_href, re.IGNORECASE)
                            if ep_match:
                                e_num = int(ep_match.group(2))
                                episodes.append({
                                    "number": e_num,
                                    "title": ep_title or f"Episodio {e_num}",
                                    "url": self._get_absolute_url(ep_href),
                                    "season": s_num
                                })
                    episodes.sort(key=lambda x: x["number"])
                    seasons.append({
                        "number": s_num,
                        "name": season_title or f"Temporada {s_num}",
                        "episodes": episodes
                    })
                seasons.sort(key=lambda x: x["number"])
            else:
                # Fallback genérico buscando todos los links de temporada
                episodes_map = {}
                for a_tag in soup.select("a[href*='/temporada/']"):
                    ep_href = a_tag.get("href", "")
                    ep_title = a_tag.get_text(strip=True)
                    ep_match = re.search(r'temporada/(\d+)/capitulo/(\d+)', ep_href, re.IGNORECASE)
                    if ep_match:
                        s_num = int(ep_match.group(1))
                        e_num = int(ep_match.group(2))
                        if s_num not in episodes_map:
                            episodes_map[s_num] = []
                        episodes_map[s_num].append({
                            "number": e_num,
                            "title": ep_title or f"Episodio {e_num}",
                            "url": self._get_absolute_url(ep_href),
                            "season": s_num
                        })
                for s_num, eps in episodes_map.items():
                    eps.sort(key=lambda x: x["number"])
                    seasons.append({
                        "number": s_num,
                        "name": f"Temporada {s_num}",
                        "episodes": eps
                    })
                seasons.sort(key=lambda x: x["number"])
                
        result = {
            "id": slug,
            "slug": slug,
            "title": title,
            "originalTitle": original_title,
            "synopsis": synopsis,
            "poster": poster,
            "rating": rating,
            "year": year,
            "genres": genres,
            "servers": servers,
            "type": content_type,
            "url": (
                self._get_absolute_url(f"/pelicula/{slug}") if content_type == "movie"
                else self._get_absolute_url(f"/anime/{slug}") if content_type == "anime"
                else self._get_absolute_url(f"/serie/{slug}")
            ),
            "provider": self.provider,
            "scraped_at": datetime.datetime.now().isoformat()
        }
        if seasons:
            result["seasons"] = seasons
            
        # Clean up empty values so we don't overwrite good data from the catalog
        return {k: v for k, v in result.items() if v}

    def get_categories_urls(self, pages=2):
        """Genera las URLs a iterar por cada categoría."""
        urls = []
        # Solo series y animes en pelisplus, según solicitud
        categories = [
            "/serie", 
            "/series/estrenos", 
            "/series/populares",
            "/animes",
            "/animes/estrenos",
            "/animes/populares"
        ]
        for cat in categories:
            for page in range(1, pages + 1):
                # pelisplus maneja la paginación con ?page=N o /page/N, probemos con ?page=
                url = f"{self.base_url}{cat}?page={page}" if page > 1 else f"{self.base_url}{cat}"
                
                # Asignar el tipo correcto para la DB según la ruta
                if "anime" in cat:
                    type_str = "anime"
                else:
                    type_str = "series"
                    
                urls.append((url, type_str))
        return urls

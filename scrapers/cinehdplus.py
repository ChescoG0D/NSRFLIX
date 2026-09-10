import re
import datetime
from urllib.parse import urljoin

class CineHDPlusScraper:
    def __init__(self, base_url="https://cinehdplus.biz"):
        self.base_url = base_url
        self.provider = "cinehdplus"

    def _get_absolute_url(self, path):
        if not path:
            return ""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.base_url, path)

    def _detect_type_from_path(self, path):
        # Regla estricta de CineHDPlus:
        # CineHDPlus SOLO provee películas. Cualquier ruta de serie/anime
        # se devuelve como "blocked" para que el catálogo la descarte.
        if not path:
            return "movie"
        low = path.lower()
        if "/series/" in low or "/serie/" in low or "/anime/" in low or "temporada" in low or "capitulo" in low:
            return "blocked"
        return "movie"

    def _extract_slug(self, path):
        if not path:
            return ""
        parts = [p for p in path.split("/") if p]
        return parts[-1].replace(".html", "") if parts else ""

    def get_categories_urls(self, pages=1, category="all"):
        """Genera las URLs base de las categorías con sus páginas."""
        urls = []
        
        # Solo películas en cinehdplus, según solicitud
        categories_map = [
            ("/cine/", "cine"),
            ("/", "estrenos"),
            ("/peliculas/", "peliculas")
        ]
        
        for path, cat in categories_map:
            for p in range(1, pages + 1):
                if p == 1:
                    urls.append((self._get_absolute_url(path), cat))
                else:
                    urls.append((self._get_absolute_url(f"{path}page/{p}/"), cat))
        
        return urls

    def parse_catalog(self, soup):
        """Parsea una página de catálogo y retorna lista de películas/series."""
        items = []
        if not soup:
            return items

        # Intentar los selectores principales primero
        cards = soup.select('.catalog .row .col-xl-3, .catalog .row .col-lg-4, .catalog .row .col-md-4, .catalog .row .col-sm-6, .catalog .row .col-6')
        if not cards:
            cards = soup.select('.card, .movie-item, .item')
            
        for el in cards:
            title_tag = el.select_one('.card__title a')
            if not title_tag:
                continue
                
            href = title_tag.get('href', '')
            title = title_tag.text.strip()
            
            # IGNORAR SERIES: Si el título empieza con "Ver serie" o "Serie", lo ignoramos
            title_lower = title.lower()
            if title_lower.startswith("ver serie") or title_lower.startswith("serie"):
                continue
                
            content_type = self._detect_type_from_path(href)
            if content_type != "movie":
                continue
                
            slug = self._extract_slug(href)
            
            poster_tag = el.select_one('img')
            poster = ""
            if poster_tag:
                 poster = poster_tag.get('data-src') or poster_tag.get('src', '')
                 poster = self._get_absolute_url(poster)
                 
            year_tag = el.select_one('.year')
            year = year_tag.text.strip() if year_tag else ""
            
            rate_tag = el.select_one('.card__rate')
            rating_str = rate_tag.text.strip() if rate_tag else "0.0"
            try:
                rating = float(rating_str)
            except ValueError:
                rating = 0.0
                
            quality_tag = el.select_one('.quality')
            quality = quality_tag.text.strip() if quality_tag else "HD"

            items.append({
                "id": slug,
                "title": title,
                "url": self._get_absolute_url(href),
                "poster": poster,
                "year": year,
                "rating": rating,
                "quality": quality,
                "type": self._detect_type_from_path(href),
                "slug": slug,
                "provider": self.provider
            })
            
        return items

    def parse_details(self, soup, slug, content_type="movie"):
        """Parsea la página de detalles para enriquecer la data."""
        if not soup:
            return None
            
        # Intentamos armar el objeto
        data = {
            "id": slug,
            "slug": slug,
            "type": content_type,
            "provider": self.provider
        }
            
        # Sinopsis
        synopsis_tag = soup.select_one('.details__text, .b-text_with_paragraphs, .description, [itemprop="description"]')
        if synopsis_tag:
            text = synopsis_tag.text.strip()
            if text.endswith("fuente"):
                text = text[:-6].strip().rstrip('.')
            data['synopsis'] = text
            
        # Título
        title_tag = soup.select_one('h1')
        if title_tag:
             title_text = title_tag.text.strip()
             title_lower = title_text.lower()
             if title_lower.startswith("ver serie") or title_lower.startswith("serie"):
                 return None
             data['title'] = title_text
             
        # Backdrop
        backdrop_tag = soup.select_one('.details__cover img')
        if backdrop_tag:
             data['backdrop'] = self._get_absolute_url(backdrop_tag.get('data-src') or backdrop_tag.get('src', ''))
             
        # Póster (fallback por si falla la lista)
        img_tag = soup.select_one('.film-poster img')
        if img_tag:
             data['poster'] = self._get_absolute_url(img_tag.get('src', ''))
             
        # Géneros
        genres = []
        genre_tags = soup.select('a[href*="/genre/"]')
        for g in genre_tags:
            g_text = g.text.strip()
            g_slug = g.get('href', '').strip('/').split('/')[-1]
            if g_text:
                genres.append({"name": g_text, "slug": g_slug})
                
        # Extraer director y actores
        import re
        director_tag = soup.find('span', string=re.compile(r'Director:'))
        if director_tag and director_tag.parent:
            data['director'] = director_tag.parent.text.replace('Director:', '').strip()
            
        actores_tag = soup.find('span', string=re.compile(r'Actores:'))
        if actores_tag and actores_tag.parent:
            data['actors'] = actores_tag.parent.text.replace('Actores:', '').strip()
        
        # Eliminar duplicados manteniendo orden (basado en slug)
        seen = set()
        clean_genres = []
        for g in genres:
            if g['slug'] not in seen:
                clean_genres.append(g)
                seen.add(g['slug'])
        data['genres'] = clean_genres
            
        # Servidores (iframes)
        servers = []
        iframes = soup.find_all('iframe')
        for i, iframe in enumerate(iframes):
            src = iframe.get('src')
            if src and not 'youtube.com' in src:
                abs_src = self._get_absolute_url(src)
                servers.append({
                    "name": f"Server {i+1}",
                    "url": abs_src,
                    "embedUrl": abs_src
                })
                
        # Trailer (iframe de youtube)
        for iframe in iframes:
            src = iframe.get('src')
            if src and 'youtube.com' in src:
                data['trailer_url'] = self._get_absolute_url(src)
                break
                
        data['servers'] = servers
        
        # Actualizar timestamp
        data['updated_at'] = datetime.datetime.now().isoformat()
        
        return data

    def parse_home(self, soup):
        """Extrae los slugs de la página principal para saber qué estrenos mostrar."""
        slugs = []
        recent = soup.select('.catalog .row .col-6')
        for item in recent[:30]:
            title_tag = item.select_one('.card__title a')
            if title_tag:
                slug = self._extract_slug(title_tag.get('href', ''))
                if slug and slug not in slugs:
                    slugs.append(slug)
        return slugs

import asyncio
import aiohttp
import logging
from engine import AsyncEngine
from database import DatabaseManager
from scrapers.pelisplus import PelisPlusScraper
from scrapers.repelis import RePelisHDScraper
from scrapers.cinehdplus import CineHDPlusScraper
from scrapers.zonaaps import ZonaapsScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class Orchestrator:
    def __init__(self, max_concurrent=5, provider="all"):
        self.db = DatabaseManager()
        self.engine = AsyncEngine(max_concurrent)
        
        all_scrapers = [
            PelisPlusScraper(),
            RePelisHDScraper(),
            CineHDPlusScraper(),
            ZonaapsScraper()
        ]
        
        if provider == "all":
            self.scrapers = all_scrapers
        else:
            self.scrapers = [s for s in all_scrapers if s.provider == provider]

    async def process_movie_details(self, session, scraper, movie_stub, existing_complete_cache=None):
        """Descarga y parsea los detalles de una película si no existe en BD."""
        # No volver a extraer detalles si ya existe y tiene poster
        if existing_complete_cache is not None:
            if existing_complete_cache.get(movie_stub["id"]):
                logging.info(f"Omitiendo (ya existe y completo): {movie_stub['title']}")
                return
        else:
            existing = next((m for m in self.db.get_all_movies() if m.get("id") == movie_stub["id"]), None)
            if existing and existing.get("poster"):
                logging.info(f"Omitiendo (ya existe y completo): {movie_stub['title']}")
                return

        logging.info(f"Extrayendo detalles: {movie_stub['title']}")
        html = await self.engine.fetch_html(session, movie_stub['url'])
        soup = self.engine.parse_html(html)
        
        if soup:
            details = scraper.parse_details(soup, movie_stub['slug'], movie_stub['type'])
            if details:
                # Unir la info inicial con los detalles nuevos
                full_movie = {**movie_stub, **details}
                # Regla estricta de proveedor -> tipo:
                # · pelisplus  -> SOLO series y anime
                # · cinehdplus -> SOLO películas
                final_type = full_movie.get('type', movie_stub.get('type', 'movie'))
                provider = full_movie.get('provider', scraper.provider)
                if provider == 'pelisplus' and final_type in ('movie', 'blocked'):
                    logging.info(f"Descartado (pelisplus no provee películas): {full_movie.get('title')}")
                    return
                if provider == 'cinehdplus' and final_type in ('series', 'anime', 'blocked'):
                    logging.info(f"Descartado (cinehdplus no provee series/anime): {full_movie.get('title')}")
                    return
                self.db.save_movie(full_movie)
                logging.info(f"Guardado exitoso: {full_movie['title']}")
            else:
                logging.warning(f"No se pudieron extraer detalles de {movie_stub['url']}")
        else:
            logging.error(f"Error al descargar la página de detalles de {movie_stub['url']}")

    async def scrape_site(self, session, scraper, pages=1, category="all"):
        """Extrae el catálogo y luego va por los detalles de cada película de un sitio."""
        logging.info(f"Iniciando scrape de {scraper.provider}")
        category_urls = scraper.get_categories_urls(pages=pages)
        
        if category != "all":
            if category == "movies":
                category_urls = [c for c in category_urls if "pelicula" in c[1]]
            elif category == "series":
                category_urls = [c for c in category_urls if "serie" in c[1]]
            elif category == "anime":
                category_urls = [c for c in category_urls if "anime" in c[1]]
            elif category == "home":
                category_urls = [c for c in category_urls if "home" in c[1]]
            elif category == "cine":
                category_urls = [c for c in category_urls if "cine" in c[1]]
        
        all_stubs = []
        
        # Fase 1: Recolectar todas las películas de las páginas de catálogo
        for url, category in category_urls:
            logging.info(f"Explorando catálogo: {url}")
            html = await self.engine.fetch_html(session, url)
            soup = self.engine.parse_html(html)
            stubs = scraper.parse_catalog(soup)
            
            # Inyectar la sección origen (peliculas, series, estrenos)
            for stub in stubs:
                stub["section"] = category
                # Forzar el tipo según la categoría para corregir errores de detección
                if category == "series":
                    stub["type"] = "series"
                elif category == "anime":
                    stub["type"] = "anime"
                elif category == "peliculas" or category == "estrenos" or category == "cine":
                    stub["type"] = "movie"
                
            logging.info(f"Encontradas {len(stubs)} películas en {url}")
            all_stubs.extend(stubs)
            
        # Fase 2: Descargar detalles de forma concurrente
        logging.info(f"Total a procesar para {scraper.provider}: {len(all_stubs)}")
        
        # Pre-cargar caché de IDs existentes para evitar matar la BD con 150+ conexiones simultáneas
        all_movies_in_db = self.db.get_all_movies()
        existing_complete_cache = {m.get("id"): bool(m.get("poster")) for m in all_movies_in_db if m.get("id")}
        
        tasks = []
        for stub in all_stubs:
            tasks.append(self.process_movie_details(session, scraper, stub, existing_complete_cache))
            
        await asyncio.gather(*tasks)

    async def run(self, pages_per_category=2, category="all"):
        """Ejecuta el proceso principal."""
        # Se requiere aiohttp para hacer las peticiones
        # pip install aiohttp beautifulsoup4
        
        # Ajustar los límites de aiohttp si es necesario
        connector = aiohttp.TCPConnector(limit=self.engine.max_concurrent)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            for scraper in self.scrapers:
                await self.scrape_site(session, scraper, pages=pages_per_category, category=category)

if __name__ == "__main__":
    print("======================================================")
    print("  NSR FLIX - Scraper Multi-Sitio v2.0")
    print("  Desarrollado en Python por: ChescoG0D")
    print("======================================================")
    
    import argparse
    parser = argparse.ArgumentParser(description="NSR FLIX Scraper")
    parser.add_argument("--provider", type=str, default="all", choices=["all", "pelisplus", "repelishd", "cinehdplus", "zonaaps"], help="Proveedor a usar")
    parser.add_argument("--category", type=str, default="all", choices=["all", "movies", "series", "anime", "home", "cine"], help="Categoría a scrapear")
    parser.add_argument("--pages", type=int, default=1, help="Número de páginas a scrapear por categoría")
    parser.add_argument("--reset-db", action="store_true", help="Borra toda la base de datos antes de scrapear")
    args = parser.parse_args()
    
    orchestrator = Orchestrator(max_concurrent=5, provider=args.provider)
    
    if args.reset_db:
        print("\n[!] Borrando toda la base de datos...")
        orchestrator.db.clear_db()
        print("[!] Base de datos limpiada con éxito.\n")
        import sys
        sys.exit(0)
    
    # Ejecutar el loop principal asíncrono
    try:
        asyncio.run(orchestrator.run(pages_per_category=args.pages, category=args.category))
    except KeyboardInterrupt:
        print("\nScraping detenido por el usuario.")
        
    # Limpieza automática de falsas series
    if args.provider in ["all", "cinehdplus"]:
        print("\n[!] Limpiando series falsas de cinehdplus...")
        deleted_count = orchestrator.db.delete_fake_series()
        print(f"[!] Se eliminaron {deleted_count} series falsas del catálogo de cinehdplus.")
    
    # Exportar resultados a JSON
    import json
    all_movies = orchestrator.db.get_all_movies()
    with open('peliculas.json', 'w', encoding='utf-8') as f:
        json.dump(all_movies, f, ensure_ascii=False, indent=2)
    
    print(f"\nProceso finalizado. Se han guardado {len(all_movies)} películas en peliculas.json y peliculas.db.")

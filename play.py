import asyncio
import aiohttp
import sqlite3
import json
import os
import webbrowser
from resolvers import resolve_url
from engine import AsyncEngine
from scrapers.pelisplus import PelisPlusScraper

# Obtener la ruta absoluta del directorio donde está play.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "peliculas.db")

def search_movie(query, db_path=DB_PATH):
    if not os.path.exists(db_path):
        print(f"La base de datos {db_path} no existe.")
        return []
        
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT json_data FROM raw_data WHERE json_data LIKE ?", (f"%{query}%",))
        results = []
        for row in cursor.fetchall():
            try:
                data = json.loads(row[0])
                if query.lower() in data.get('title', '').lower():
                    results.append(data)
            except:
                pass
        return results

async def main():
    print("======================================================")
    print("  NSR FLIX - Reproductor de Consola v2.0 (Con Series)")
    print("======================================================")
    
    query = input("¿Qué película o serie quieres ver?: ").strip()
    if not query:
        return
        
    results = search_movie(query)
    
    if not results:
        print("No se encontraron coincidencias.")
        return
        
    print(f"\nSe encontraron {len(results)} resultados:")
    for i, movie in enumerate(results):
        t = movie.get('type', 'movie')
        type_str = "Película" if t == "movie" else "Serie/Anime"
        print(f"[{i+1}] {movie.get('title')} ({movie.get('year', 'N/A')}) - [{type_str}]")
        
    try:
        choice = int(input("\nElige el número: ")) - 1
        if choice < 0 or choice >= len(results):
            print("Selección inválida.")
            return
    except ValueError:
        print("Entrada inválida.")
        return
        
    selected_movie = results[choice]
    engine = AsyncEngine(max_concurrent=1)
    scraper = PelisPlusScraper()
    
    servers = []
    
    # --- FLUJO PARA SERIES ---
    if selected_movie.get('type') in ['series', 'anime']:
        seasons = selected_movie.get('seasons', [])
        if not seasons:
            print("Esta serie no tiene temporadas registradas en la base de datos.")
            return
            
        print(f"\nTemporadas disponibles para '{selected_movie['title']}':")
        for i, s in enumerate(seasons):
            print(f"[{i+1}] {s.get('name')}")
            
        try:
            s_choice = int(input("\nElige la temporada: ")) - 1
            if s_choice < 0 or s_choice >= len(seasons):
                print("Selección inválida.")
                return
        except ValueError:
            print("Entrada inválida.")
            return
            
        selected_season = seasons[s_choice]
        episodes = selected_season.get('episodes', [])
        
        if not episodes:
            print("Esta temporada no tiene episodios.")
            return
            
        print(f"\nEpisodios de la {selected_season['name']}:")
        for i, ep in enumerate(episodes):
            print(f"[{i+1}] {ep.get('title')}")
            
        try:
            e_choice = int(input("\nElige el episodio: ")) - 1
            if e_choice < 0 or e_choice >= len(episodes):
                print("Selección inválida.")
                return
        except ValueError:
            print("Entrada inválida.")
            return
            
        selected_episode = episodes[e_choice]
        ep_url = selected_episode.get('url')
        print(f"\nScrapeando servidores para el episodio desde: {ep_url}...")
        
        async with aiohttp.ClientSession() as session:
            html = await engine.fetch_html(session, ep_url)
            soup = engine.parse_html(html)
            if soup:
                ep_details = scraper.parse_details(soup, "temp_slug", "movie") # Usamos "movie" para que saque los servers directos
                servers = ep_details.get('servers', [])
            else:
                print("Error al descargar la página del episodio.")
                return
    else:
        # --- FLUJO PARA PELÍCULAS ---
        servers = selected_movie.get('servers', [])
    
    if not servers:
        print("No se encontraron servidores de video.")
        return
        
    print(f"\nServidores de video disponibles:")
    
    # Filtrar solo servidores compatibles (Opcional, ahora mostramos todos)
    supported_names = ["voe", "streamwish", "streamtape", "wish", "tape"]
    valid_servers = servers # Mostramos todos por ahora
        
    for i, server in enumerate(valid_servers):
        is_rec = " (Recomendado)" if any(sub in server.get('name', '').lower() for sub in supported_names) else ""
        print(f"[{i+1}] {server.get('name')}{is_rec}")
        
    try:
        s_choice = int(input("\nElige un servidor: ")) - 1
        if s_choice < 0 or s_choice >= len(valid_servers):
            print("Selección inválida.")
            return
    except ValueError:
        print("Entrada inválida.")
        return
        
    chosen_server = valid_servers[s_choice]
    embed_url = chosen_server.get('embedUrl')
    print(f"\nEnlace embed seleccionado: {embed_url}")
    
    print("\n======================================================")
    print("  OPCIONES DE REPRODUCCIÓN")
    print("======================================================")
    print("[1] Abrir el Iframe original con anuncios (Modo Seguro - Funciona siempre)")
    print("[2] Intentar extraer el .m3u8/.mp4 directo (Modo PeliApi - Para descargas/proxy)")
    
    try:
        play_mode = int(input("\nElige una opción: "))
    except:
        play_mode = 1
        
    if play_mode == 1:
        # Generar reproductor HTML normal sin sandbox
        html_player = f"""<!DOCTYPE html>
<html>
<head>
    <title>NSR FLIX - Web Player</title>
    <style>
        body {{ background: #000; margin: 0; padding: 20px; display: flex; justify-content: center; height: 100vh; }}
        iframe {{ width: 90%; max-width: 1200px; height: 80%; border: 2px solid #e50914; border-radius: 10px; }}
    </style>
</head>
<body>
    <iframe src="{embed_url}" allowfullscreen allow="autoplay; encrypted-media"></iframe>
</body>
</html>"""
        sandbox_path = os.path.join(BASE_DIR, "web_player.html")
        with open(sandbox_path, "w", encoding="utf-8") as f:
            f.write(html_player)
        print("\nAbriendo en el navegador web...")
        webbrowser.open(f"file://{sandbox_path}")
        
    elif play_mode == 2:
        print(f"\nIntentando extraer enlace directo mediante resolvers asíncronos...")
        async with aiohttp.ClientSession() as session:
            direct_url = await resolve_url(session, embed_url, engine.fetch_html)
            
        if direct_url:
            print("\n======================================================")
            print("¡URL DIRECTA OBTENIDA CON ÉXITO!")
            print("======================================================")
            print(f"-> {direct_url}\n")
            # Abrir en navegador
            webbrowser.open(direct_url)
        else:
            print("\nFalló la resolución del servidor. Protegido por Cloudflare o captcha.")

if __name__ == "__main__":
    asyncio.run(main())

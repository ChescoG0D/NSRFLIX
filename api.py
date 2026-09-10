import os
import asyncio
import re
import time
import aiohttp
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Query, Depends, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from scrapers.pelisplus import PelisPlusScraper
from scrapers.repelis import RePelisHDScraper
from scrapers.cinehdplus import CineHDPlusScraper
from scrapers.zonaaps import ZonaapsScraper
from engine import AsyncEngine
from database import DatabaseManager
from utils import obfuscate_url, get_master_key, deobfuscate_url
from main import Orchestrator
from resolvers import resolve_url

# Conectar a la Base de Datos Postgres (espera DATABASE_URL de entorno)
db = DatabaseManager()

engine = AsyncEngine(max_concurrent=5)
pelisplus = PelisPlusScraper()
repelis = RePelisHDScraper()
cinehdplus = CineHDPlusScraper()
zonaaps = ZonaapsScraper()

scheduler = AsyncIOScheduler()

async def run_daily_scrape():
    """Ejecuta el scraper diariamente en segundo plano."""
    print("Iniciando tarea de scrapeo diario...")
    orchestrator = Orchestrator(max_concurrent=3, provider="all")
    await orchestrator.run(pages_per_category=2, category="all")
    # Regla estricta: limpiar películas que no provengan de cinehdplus
    deleted = orchestrator.db.delete_legacy_movies()
    if deleted:
        print(f"Limpieza legacy: {deleted} películas no-cinehdplus eliminadas.")
    print("Scrapeo diario completado.")
from fastapi import Request

def verify_api_key(request: Request, x_nsr_api_key: str = Header(None)):
    # /api/image queda exento porque las etiquetas <img> no pueden enviar headers.
    # Su protección es la whitelist de dominios + límite de tamaño en el endpoint.
    if request.url.path == "/api/image":
        return True

    if not x_nsr_api_key:
        raise HTTPException(status_code=403, detail="Falta el encabezado X-NSR-API-KEY")
    
    # 1. Check Master Key
    if x_nsr_api_key == get_master_key():
        return True
    
    # 2. Check Database Keys
    if db.is_valid_api_key(x_nsr_api_key):
        return True
    
    raise HTTPException(status_code=403, detail="API Key Invalida o revocada")

app = FastAPI(
    title="NSR FLIX Híbrido API", 
    version="2.0.0",
    # La documentación no debe ser pública en producción
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    dependencies=[Depends(verify_api_key)]
)

@app.on_event("startup")
async def start_scheduler():
    # Programar el scraper a las 3:00 AM todos los días
    scheduler.add_job(run_daily_scrape, 'cron', hour=3, minute=0)
    scheduler.start()
    print("Scheduler de APScheduler iniciado correctamente.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://nsrflix.fly.dev",
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import time

active_clients = {}

# Rate limiting en memoria para endpoints sensibles (anti fuerza bruta)
RATE_LIMITS = {
    "/api/auth/login": (5, 60),      # 5 intentos por minuto
    "/api/auth/register": (3, 60),   # 3 registros por minuto
    "/api/auth/google": (5, 60),     # 5 intentos por minuto
    "/api/pair/create": (5, 60),     # 5 códigos de TV por minuto
}
rate_buckets = defaultdict(list)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    limit_cfg = RATE_LIMITS.get(request.url.path)
    if limit_cfg:
        max_reqs, window_secs = limit_cfg
        client_ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "unknown")
        client_ip = client_ip.split(",")[0].strip()
        now = time.time()
        bucket = rate_buckets[(client_ip, request.url.path)]
        bucket[:] = [t for t in bucket if now - t < window_secs]
        if len(bucket) >= max_reqs:
            return JSONResponse(status_code=429, content={"detail": "Demasiados intentos. Espera un momento."})
        bucket.append(now)
    return await call_next(request)

def format_page_title(path: str) -> str:
    if not path:
        return "🏠 Inicio"
    p = path.lower()
    if p in ("/", "/index.html"):
        return "🏠 Inicio"
    if "/peliculas" in p:
        return "🎬 Catálogo Películas"
    if "/series" in p:
        return "📺 Catálogo Series"
    if "/anime" in p:
        return "⛩️ Catálogo Anime"
    if "/buscar" in p:
        return "🔍 Buscando Contenido"
    if "/mi-lista" in p:
        return "⭐ Mi Lista"
    if "/historial" in p:
        return "🕒 Historial"
    if "/admin" in p:
        return "⚙️ Panel de Admin"
    if "/player" in p:
        return "▶️ Reproduciendo Video"
    if "/info/" in p:
        slug = p.split("/info/")[-1].split("?")[0].replace("-", " ").title()
        return f"ℹ️ Viendo: {slug[:22]}"
    if "/api-docs" in p:
        return "📄 Docs API"
    return f"📍 {path[:22]}"

@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Registrar IP activa para conteo de usuarios en vivo (ultimos 5 mins)
    client_ip = request.headers.get("x-forwarded-for")
    if not client_ip and request.client:
        client_ip = request.client.host
    if client_ip:
        client_ip = client_ip.split(",")[0].strip()
        auth_header = request.headers.get("authorization")
        user_obj = get_optional_user(auth_header) if auth_header else None
        page_raw = request.headers.get("x-nsr-page") or request.query_params.get("page")
        
        # Guardar o actualizar datos de conexión del cliente
        existing = active_clients.get(client_ip)
        page_title = format_page_title(page_raw) if page_raw else (existing.get("current_page") if isinstance(existing, dict) and existing.get("current_page") else "🏠 Navegando")
        
        active_clients[client_ip] = {
            "ip": client_ip,
            "user_id": str(user_obj["id"]) if user_obj else None,
            "name": user_obj["name"] if user_obj else "Visitante Anónimo",
            "email": user_obj["email"] if user_obj else f"IP: {client_ip}",
            "avatar_url": user_obj.get("avatar_url") if user_obj else None,
            "is_admin": user_obj.get("is_admin", False) if user_obj else False,
            "current_page": page_title,
            "last_seen": time.time()
        }
        db.log_unique_visitor(client_ip)

    # Solo cuenta la visita si entran a ver la info de una pelicula o serie
    if request.url.path.startswith("/api/info/"):
        db.log_api_request()
    response = await call_next(request)
    return response

@app.get("/api/heartbeat")
async def heartbeat(request: Request, page: Optional[str] = Query(None)):
    """Endpoint de pulsación para mantener viva la sesión de usuario activo en el panel."""
    client_ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "unknown")
    if client_ip:
        client_ip = client_ip.split(",")[0].strip()
        if client_ip in active_clients and isinstance(active_clients[client_ip], dict):
            if page:
                active_clients[client_ip]["current_page"] = format_page_title(page)
            active_clients[client_ip]["last_seen"] = time.time()
    return {"status": "ok"}


engine = AsyncEngine(max_concurrent=5)
pelisplus = PelisPlusScraper()
repelis = RePelisHDScraper()
zonaaps = ZonaapsScraper()

# ============================================================
# ============================================================
# Image Proxy (Evade referer blocks on iOS/Mobile Safari)
# ============================================================
import io

ALLOWED_IMAGE_DOMAINS = {
    "www.pelisplushd.la", "pelisplushd.la",
    "img.pelisplushd.la",
    "cinehdplus.biz", "www.cinehdplus.biz",
    "repelishd.fit", "www.repelishd.fit",
    "zonaaps.com", "www.zonaaps.com",
    "image.tmdb.org", "assets.fanart.tv",
}

@app.get("/api/image")
async def proxy_image(url: str):
    """Proxea una imagen para evadir bloqueos de tracker/referer en iOS/Móvil."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme or parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Esquema de URL inválido")

    if parsed.hostname not in ALLOWED_IMAGE_DOMAINS:
        raise HTTPException(status_code=403, detail="Dominio no permitido para proxy de imágenes")

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                'Referer': 'https://www.pelisplushd.la/',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
            }
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    # Limitar tamaño de respuesta a 5 MB para evitar abuso
                    if len(content) > 5 * 1024 * 1024:
                        raise HTTPException(status_code=413, detail="Imagen demasiado grande")
                    return StreamingResponse(io.BytesIO(content), media_type=resp.content_type)
                else:
                    raise HTTPException(status_code=resp.status, detail="Image fetch failed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
@app.get("/api/resolve")
async def resolve_video(url: str = Query(..., description="URL del embed a resolver (ej. Streamtape)")):
    """Resuelve la URL de un embed para obtener el enlace directo (.mp4/.m3u8)"""
    try:
        async with aiohttp.ClientSession() as session:
            direct_url = await resolve_url(session, url, engine.fetch_html)
            if direct_url:
                return {"success": True, "direct_link": direct_url}
            else:
                return {"success": False, "error": "No se pudo extraer el enlace del video (el servidor pudo haber cambiado sus tokens o requiere captcha)"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ============================================================
# Configuración de Auth
# ============================================================
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET no está configurada en las variables de entorno")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 720  # 30 días
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
if not GOOGLE_CLIENT_ID:
    print("[WARN] GOOGLE_CLIENT_ID no configurada — el login con Google no funcionará")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_jwt(user_id: str, email: str) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRE_HOURS)
    return jwt.encode({"sub": user_id, "email": email, "exp": expire}, JWT_SECRET, algorithm=JWT_ALGORITHM)

def get_current_user(authorization: Optional[str] = Header(None)):
    """Extrae y valida el JWT del header Authorization: Bearer <token>"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token no proporcionado")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token inválido")
        user = db.get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

def get_optional_user(authorization: Optional[str] = Header(None)):
    """Como get_current_user pero no lanza error si no hay token."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return get_current_user(authorization)
    except HTTPException:
        return None

# Modelos Pydantic
class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str

class LoginRequest(BaseModel):
    email: str
    password: str

class GoogleAuthRequest(BaseModel):
    credential: str  # El id_token de Google

class HistoryRequest(BaseModel):
    movie_id: str
    episode_url: Optional[str] = None

class WatchlistRequest(BaseModel):
    movie_id: str

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "db_connected": bool(db.db_url)}

@app.get("/api/stats")
async def get_stats(record_visit: bool = False):
    """Retorna estadísticas públicas del sitio y opcionalmente registra una visita."""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
        
            if record_visit:
                # Incrementar visitas
                cursor.execute("UPDATE site_stats SET total_visits = total_visits + 1 WHERE id = 1")
                conn.commit()
            
            # Obtener total de visitas
            cursor.execute("SELECT total_visits FROM site_stats WHERE id = 1")
            row = cursor.fetchone()
            visits = row[0] if row else 0
        
            # Obtener total de usuarios registrados
            cursor.execute("SELECT COUNT(*) FROM users")
            user_row = cursor.fetchone()
            total_users = user_row[0] if user_row else 0
        
        return {"success": True, "visits": visits, "users": total_users}
    except Exception as e:
        return {"success": False, "visits": 0, "users": 0}

@app.get("/api/top10")
async def get_top10():
    """Retorna el Top 10 basado en la cantidad de vistas (views)."""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor) if 'RealDictCursor' in globals() else conn.cursor()
            # Seleccionamos las 10 más vistas (películas solo de cinehdplus)
            cursor.execute('''
                SELECT slug, title, poster, type, rating, year, views
                FROM movies 
                WHERE poster IS NOT NULL
                  AND (type != 'movie' OR provider IN ('cinehdplus', 'cinehd'))
                ORDER BY views DESC NULLS LAST, id DESC
                LIMIT 10
            ''')
            columns = [desc[0] for desc in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return {"success": True, "data": items}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.post("/api/movies/{slug}/view")
async def record_movie_view(slug: str):
    """Incrementa el contador de vistas de una película o serie."""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE movies SET views = COALESCE(views, 0) + 1 WHERE slug = %s", (slug,))
            conn.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/home")
async def get_home():
    """Sincroniza el orden del inicio con la página principal de CineHDPlus."""
    try:
        # 1. Scrapear la página principal en vivo para obtener el ORDEN EXACTO de los Estrenos en Cines
        live_slugs = []
        try:
            async with aiohttp.ClientSession() as session:
                html = await engine.fetch_html(session, "https://cinehdplus.biz/cine/")
                soup = engine.parse_html(html)
                from scrapers.cinehdplus import CineHDPlusScraper
                slugs = CineHDPlusScraper().parse_home(soup)
                if slugs:
                    live_slugs = slugs
        except Exception as e:
            print(f"Error scraping live homepage from cinehdplus: {e}")

        # 2. Obtener nuestras películas locales (que ya tienen los fondos HD)
        all_movies = db.get_all_movies()
        
        if live_slugs:
            # Diccionario para acceso ultra-rápido por slug
            movies_dict = {m.get('slug'): m for m in all_movies if m.get('slug')}
            
            # Mapeamos los slugs en vivo a nuestras películas guardadas
            estrenos = [movies_dict[slug] for slug in live_slugs if slug in movies_dict]
        else:
            # Fallback en caso de que falle el scrapeo en vivo
            # Regla estricta: películas solo de cinehdplus
            estrenos = [m for m in all_movies if (m.get("section") == "estrenos" or m.get("type") == "movie")
                        and m.get("provider") in ("cinehdplus", "cinehd")]
            estrenos.sort(key=lambda x: (str(x.get('year') or '0'), str(x.get('updated_at', ''))), reverse=True)
            estrenos = estrenos[:20]
        
        series = [m for m in all_movies if m.get("type") == "series"]
        animes = [m for m in all_movies if m.get("type") == "anime"]
        
        # Mezclamos algunas populares al azar (películs solo de cinehdplus)
        import random
        populares = [m for m in all_movies if m.get("type") != "movie" or m.get("provider") in ("cinehdplus", "cinehd")]
        random.seed(datetime.datetime.now().strftime("%Y-%m-%d")) # Semilla diaria para populares
        random.shuffle(populares)
        
        return {
            "success": True, 
            "data": {
                "estrenos": estrenos[:10],
                "series": series[:15],
                "populares": populares[:10],
                "animes": animes[:15]
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

async def run_lazy_cron():
    """Ejecuta el scraper solo si ha pasado suficiente tiempo desde la última vez."""
    last_scrape = db.get_system_setting("last_scrape_time")
    now = datetime.datetime.now()
    
    should_run = False
    if not last_scrape:
        should_run = True
    else:
        try:
            last_dt = datetime.datetime.fromisoformat(last_scrape)
            # Scrapear si han pasado más de 12 horas
            if (now - last_dt).total_seconds() > 12 * 3600:
                should_run = True
        except ValueError:
            should_run = True
            
    if should_run:
        print("[LAZY CRON] Iniciando actualización de contenido en segundo plano...")
        # Guardamos la fecha actual de inmediato para evitar que otras peticiones levanten otro cron
        db.set_system_setting("last_scrape_time", now.isoformat())
        try:
            orchestrator = Orchestrator(max_concurrent=3, provider="all")
            await orchestrator.run(pages_per_category=1, category="all")
            print("[LAZY CRON] Actualización completada con éxito.")
        except Exception as e:
            print(f"[LAZY CRON] Error durante la actualización: {e}")

@app.get("/api/catalog")
async def get_catalog(background_tasks: BackgroundTasks, type: str = Query("all"), provider: Optional[str] = Query(None), page: int = Query(1)):
    """Devuelve todo lo que tenemos guardado en la Base de Datos."""
    # Despertar el lazy cron sin bloquear al usuario
    background_tasks.add_task(run_lazy_cron)

    all_movies = db.get_all_movies()
    
    # Regla estricta: las películas provienen EXCLUSIVAMENTE de cinehdplus
    all_movies = [m for m in all_movies if m.get("type") != "movie" or m.get("provider") in ("cinehdplus", "cinehd")]
    
    if type != "all":
        all_movies = [m for m in all_movies if m.get("type") == type]
        
    if provider:
        all_movies = [m for m in all_movies if m.get("provider") == provider]
        
    # Ordenar por fecha de scrapeo o rating
    all_movies.sort(key=lambda x: x.get('scraped_at', ''), reverse=True)
    
    per_page = 50
    start = (page - 1) * per_page
    end = start + per_page
    
    items = all_movies[start:end]
    has_next_page = end < len(all_movies)
    
    return {
        "success": True,
        "data": {
            "items": items,
            "hasNextPage": has_next_page
        }
    }

@app.get("/api/search")
async def search_catalog(q: str):
    """Busca en BD. Luego lanza scrapers en vivo a CineHDPlus y PelisPlus."""
    # 1. Buscar en BD local y filtrar por la regla estricta de proveedor -> tipo
    all_movies = db.get_all_movies()
    local_results = [m for m in all_movies if q.lower() in m.get("title", "").lower()]
    
    # Regla estricta: pelisplus SOLO series/anime; cinehdplus SOLO películas. zonaaps SOLO series.
    # Las películas del catálogo deben venir EXCLUSIVAMENTE de cinehdplus.
    def is_valid_provider_type(item):
        provider = item.get("provider", "")
        tipo = item.get("type", "movie")
        if provider == "pelisplus":
            return tipo in ("series", "anime")
        if provider == "zonaaps":
            return tipo == "series"
        if provider == "cinehdplus":
            return tipo == "movie"
        return tipo != "movie"  # las películas SOLO provienen de cinehdplus
    
    local_results = [m for m in local_results if is_valid_provider_type(m)]
    
    # 2. Scrapear en vivo simultáneamente
    live_results = []
    async with aiohttp.ClientSession() as session:
        # Tarea 1: PelisPlus (Series / Animes)
        search_pp_url = f"{pelisplus.base_url}/search?s={q}"
        task_pp = engine.fetch_html(session, search_pp_url, method="GET")
        
        # Tarea 2: CineHDPlus (Películas)
        search_cine_url = f"{cinehdplus.base_url}/?story={q}&do=search&subaction=search"
        task_cine = engine.fetch_html(session, search_cine_url, method="GET")
        
        # Tarea 3: Zonaaps (Series)
        search_zna_url = f"{zonaaps.base_url}/?s={q}"
        task_zna = engine.fetch_html(session, search_zna_url, method="GET")
        
        # Ejecutar en paralelo
        html_pp, html_cine, html_zna = await asyncio.gather(task_pp, task_cine, task_zna, return_exceptions=True)
        
        # Procesar PelisPlus: SOLO series y anime (nunca películas).
        if isinstance(html_pp, str) and html_pp:
            soup_pp = engine.parse_html(html_pp)
            res_pp = pelisplus.parse_catalog(soup_pp)
            for item in res_pp:
                # Filtro de seguridad: descartar cualquier película que se cuele
                if item.get("type") in ("series", "anime"):
                    live_results.append(item)
            
        # Procesar CineHDPlus: SOLO películas.
        if isinstance(html_cine, str) and html_cine:
            soup_cine = engine.parse_html(html_cine)
            res_cine = cinehdplus.parse_catalog(soup_cine)
            # Forzar tipo pelicula para cinehdplus y descartar series/anime
            for item in res_cine:
                if item.get("type") == "movie":
                    item["type"] = "movie"
                    live_results.append(item)
                    
        # Procesar Zonaaps: SOLO series.
        if isinstance(html_zna, str) and html_zna:
            soup_zna = engine.parse_html(html_zna)
            res_zna = zonaaps.parse_catalog(soup_zna)
            for item in res_zna:
                if item.get("type") == "series":
                    live_results.append(item)
        
        # Unir y guardar en BD (solo como esqueleto si no existen y si es válido)
        for item in live_results:
            if not any(m.get("id") == item["id"] for m in local_results) and is_valid_provider_type(item):
                db.save_movie(item)
                local_results.append(item)
                
    return {
        "success": True,
        "data": local_results
    }

@app.get("/api/info/{slug}")
async def get_info(slug: str, type: str = "movie", provider: str = "pelisplus"):
    """Busca detalles completos."""
    # Buscar en DB
    all_movies = db.get_all_movies()
    movie = next((m for m in all_movies if m.get("slug") == slug), None)
    
    # Regla estricta: las películas provienen EXCLUSIVAMENTE de cinehdplus.
    # Entradas legacy de otros proveedores se rechazan (ej: pelisplus/repelis).
    if movie and movie.get("type") == "movie" and movie.get("provider") not in ("cinehdplus", "cinehd"):
        return {"success": False, "data": None}
    
    # Determinar el tipo real: preferir el que viene de la DB
    actual_type = type
    if movie and movie.get("type"):
        actual_type = movie.get("type")
    # Normalizar: 'series' y 'anime' se tratan como serie en la URL
    is_series = actual_type in ("series", "anime", "serie")
    
    # Si falta info pesada (sinopsis o temporadas para series), scrapeamos on-demand
    needs_scrape = not movie or not movie.get("synopsis")
    if is_series and (not movie or not movie.get("seasons")):
        needs_scrape = True
        
    actual_provider = provider
    if movie and movie.get("provider"):
        actual_provider = movie.get("provider")

    # Forzar scrapeo si es repelis y solo tiene el servidor iframe original (para actualizar a los servidores reales)
    if movie and actual_provider in ("repelishd", "repelis"):
        servers = movie.get("servers", [])
        if servers and len(servers) == 1 and "RePelisHD Server" in servers[0].get("name", ""):
            needs_scrape = True

    if needs_scrape:
        # Regla estricta: las películas provienen EXCLUSIVAMENTE de cinehdplus
        scraper = cinehdplus if actual_type == "movie" else pelisplus
        if actual_provider == "cinehdplus" or actual_provider == "cinehd":
            scraper = cinehdplus
        elif actual_provider == "zonaaps":
            scraper = zonaaps
        elif actual_provider == "repelishd" or actual_provider == "repelis":
            scraper = repelis
            
        url = movie.get("url") if movie else None
        
        if not url:
            if scraper == pelisplus:
                if is_series:
                    url = f"{scraper.base_url}/serie/{slug}"
                elif actual_type == 'anime':
                    url = f"{scraper.base_url}/anime/{slug}"
                else:
                    url = f"{scraper.base_url}/pelicula/{slug}"
            elif scraper == cinehdplus:
                url = f"{scraper.base_url}/peliculas/{slug}.html"
            elif scraper == zonaaps:
                url = f"{scraper.base_url}/tvshows/{slug}/"
            else:
                url = f"{scraper.base_url}/ver-serie/{slug}" if (is_series or actual_type == 'anime') else f"{scraper.base_url}/ver-pelicula/{slug}"
        elif not url.startswith("http"):
            url = f"{scraper.base_url}{url}" if url.startswith("/") else f"{scraper.base_url}/{url}"
            
        async with aiohttp.ClientSession() as session:
            html = await engine.fetch_html(session, url)
            soup = engine.parse_html(html)
            if hasattr(scraper, "async_parse_details"):
                details = await scraper.async_parse_details(session, engine, soup, slug, actual_type, url)
            else:
                details = scraper.parse_details(soup, slug, actual_type)
                
            if details:
                if movie:
                    # Fusionar solo si el nuevo valor existe y no es basura
                    for k, v in details.items():
                        if v and not (k == 'title' and v == 'RePelisHD'):
                            movie[k] = v
                    details = movie
                db.save_movie(details)
                movie = details
    
    # Limpiar el titulo si viene con el patron feo
    if movie and movie.get("title"):
        import re
        title = movie["title"]
        # Remover 'VER ... Online Gratis HD' y similares
        title = re.sub(r'^(?:ver\s+)?(.+?)\s+online(?:\s+gratis)?(?:\s+hd)?$', r'\1', title, flags=re.IGNORECASE)
        movie["title"] = title.strip()
        
    # Ofuscar los links
    if movie:
        if movie.get("servers"):
            for srv in movie["servers"]:
                if srv.get("url"):
                    if "embedUrl" not in srv:
                        srv["embedUrl"] = srv["url"]
                    srv["url"] = obfuscate_url(srv["url"])
        if movie.get("seasons"):
            for season in movie["seasons"]:
                if season.get("episodes"):
                    for ep in season["episodes"]:
                        if ep.get("url"):
                            ep["url"] = obfuscate_url(ep["url"])
                
    return {
        "success": True,
        "data": movie
    }

@app.get("/api/episode/servers")
async def get_servers(url: str, provider: str = "pelisplus"):
    """Scrapea la página exacta de un episodio para sacar los servidores."""
    if provider == "cinehdplus" or provider == "cinehd":
        scraper = cinehdplus
    elif provider == "zonaaps":
        scraper = zonaaps
    elif provider == "pelisplus":
        scraper = pelisplus
    else:
        scraper = repelis
    
    # El frontend manda la URL ofuscada para que no la roben scrapers basicos
    real_url = deobfuscate_url(url)
    
    async with aiohttp.ClientSession() as session:
        if provider == "zonaaps":
            # Para Zonaaps extraemos el M3U8 vía worker en un método especial
            servers = await scraper.async_get_servers(session, real_url)
        else:
            html = await engine.fetch_html(session, real_url)
            soup = engine.parse_html(html)
            if hasattr(scraper, "async_parse_details"):
                details = await scraper.async_parse_details(session, engine, soup, "temp", "movie", real_url)
            else:
                details = scraper.parse_details(soup, "temp", "movie")
            servers = details.get("servers", []) if details else []
        
    # Ofuscar los links de servidores resultantes
    for srv in servers:
        if srv.get("url"):
            srv["url"] = obfuscate_url(srv["url"])
            
    return {
        "success": True,
        "data": servers
    }

# ============================================================
# Endpoints de Autenticación
# ============================================================

@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    """Registro con email y contraseña."""
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")
    
    password_hash = pwd_context.hash(req.password)
    user = db.create_user(email=req.email, name=req.name, password_hash=password_hash)
    
    if not user:
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email")
    
    token = create_jwt(str(user["id"]), user["email"])
    return {"success": True, "token": token, "user": {
        "id": str(user["id"]), "email": user["email"],
        "name": user["name"], "avatar_url": user.get("avatar_url"),
        "is_admin": user.get("is_admin", False)
    }}

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    """Login con email y contraseña."""
    user = db.get_user_by_email(req.email)
    
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    
    if not pwd_context.verify(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    
    token = create_jwt(str(user["id"]), user["email"])
    return {"success": True, "token": token, "user": {
        "id": str(user["id"]), "email": user["email"],
        "name": user["name"], "avatar_url": user.get("avatar_url"),
        "is_admin": user.get("is_admin", False)
    }}

@app.post("/api/auth/google")
async def google_auth(req: GoogleAuthRequest):
    """Login / Registro con Google usando el id_token del frontend."""
    try:
        idinfo = id_token.verify_oauth2_token(
            req.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )
        google_id = idinfo["sub"]
        email = idinfo.get("email", "")
        name = idinfo.get("name", "")
        avatar_url = idinfo.get("picture", "")
        
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Token de Google inválido: {str(e)}")
    
    user = db.upsert_google_user(google_id=google_id, email=email, name=name, avatar_url=avatar_url)
    
    if not user:
        raise HTTPException(status_code=500, detail="Error al procesar usuario de Google")
    
    token = create_jwt(str(user["id"]), user["email"])
    return {"success": True, "token": token, "user": {
        "id": str(user["id"]), "email": user["email"],
        "name": user["name"], "avatar_url": user.get("avatar_url"),
        "is_admin": user.get("is_admin", False)
    }}

@app.get("/api/user/me")
async def get_me(current_user = Depends(get_current_user)):
    """Perfil del usuario autenticado."""
    return {"success": True, "user": {
        "id": str(current_user["id"]),
        "email": current_user["email"],
        "name": current_user["name"],
        "avatar_url": current_user.get("avatar_url"),
        "is_admin": current_user.get("is_admin", False),
        "created_at": str(current_user.get("created_at", ""))
    }}

# ============================================================
# Admin Endpoints
# ============================================================

@app.get("/api/admin/stats")
async def get_admin_stats(current_user = Depends(get_current_user)):
    """Estadísticas avanzadas para el panel de administración."""
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="No tienes permisos de administrador")
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor) if 'RealDictCursor' in globals() else conn.cursor()
        
            # Últimos 100 usuarios registrados
            cursor.execute("SELECT id, name, email, avatar_url, created_at, is_admin FROM users ORDER BY created_at DESC LIMIT 100")
            columns = [desc[0] for desc in cursor.description]
            recent_users = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
            for u in recent_users:
                if u.get("created_at"):
                    u["created_at"] = str(u["created_at"])
                
            # Total de usuarios y registrados hoy
            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
        
            cursor.execute("SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE")
            row_today = cursor.fetchone()
            new_users_today = row_today[0] if row_today else 0
        
            # Total visitas
            cursor.execute("SELECT total_visits FROM site_stats WHERE id = 1")
            row = cursor.fetchone()
            total_visits = row[0] if row else 0
        
            # Total películas y series
            cursor.execute("SELECT type, COUNT(*) FROM movies GROUP BY type")
            catalog_stats = {row[0]: row[1] for row in cursor.fetchall()}
        
            # Usuarios activos en vivo (últimos 5 minutos = 300 segundos)
            now_ts = time.time()
            active_users_list = []
            for c_ip, info in list(active_clients.items()):
                if isinstance(info, dict):
                    secs_ago = int(now_ts - info.get("last_seen", now_ts))
                    if secs_ago < 300:
                        active_users_list.append({
                            "ip": info["ip"],
                            "user_id": info.get("user_id"),
                            "name": info.get("name", "Visitante Anónimo"),
                            "email": info.get("email", info["ip"]),
                            "avatar_url": info.get("avatar_url"),
                            "is_admin": info.get("is_admin", False),
                            "current_page": info.get("current_page", "🏠 Navegando"),
                            "last_seen_secs": secs_ago
                        })
                elif isinstance(info, (int, float)):
                    secs_ago = int(now_ts - info)
                    if secs_ago < 300:
                        active_users_list.append({
                            "ip": c_ip,
                            "user_id": None,
                            "name": "Visitante Anónimo",
                            "email": f"IP: {c_ip}",
                            "avatar_url": None,
                            "is_admin": False,
                            "current_page": "🏠 Navegando",
                            "last_seen_secs": secs_ago
                        })

            active_users_list.sort(key=lambda x: x["last_seen_secs"])
            active_users = len(active_users_list)
            if active_users == 0:
                active_users = 1 # Al menos el administrador actual

            # Visitantes únicos acumulados (Base acumulada histórica de los 2.5 días + IPs únicas reales en DB)
            raw_unique = db.get_unique_visitors_count()
            unique_visitors = 1250 + raw_unique if raw_unique < 1250 else raw_unique

            # Datos del grafico de API (ultimos 7 dias)
            api_chart_data = db.get_api_chart_data()
        
        return {
            "success": True,
            "data": {
                "total_users": total_users,
                "new_users_today": new_users_today,
                "active_users": active_users,
                "active_users_list": active_users_list,
                "unique_visitors": unique_visitors,
                "total_visits": total_visits,
                "catalog_stats": catalog_stats,
                "recent_users": recent_users,
                "api_chart_data": api_chart_data
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# Login por código (Vinculación TV)
# ============================================================
class PairApproveRequest(BaseModel):
    code: str

@app.post("/api/pair/create")
async def pair_create():
    """La TV pide un código de 6 caracteres para vincularse. Vigencia 10 min."""
    code = db.create_pair_code()
    if not code:
        raise HTTPException(status_code=500, detail="No se pudo generar el código")
    return {"success": True, "code": code, "expires_in": 600}

@app.get("/api/pair/status/{code}")
async def pair_status(code: str):
    """La TV consulta el estado de su código (polling cada 3s)."""
    status = db.get_pair_status(code.upper().strip())
    if not status.get("exists"):
        return {"success": False, "status": "invalid"}
    if status.get("expired"):
        return {"success": True, "status": "expired"}
    if status.get("approved"):
        user = status["user"]
        token = create_jwt(user["id"], user["email"])
        db.consume_pair_code(code.upper().strip())
        return {"success": True, "status": "approved", "token": token, "user": user}
    return {"success": True, "status": "pending"}

@app.post("/api/pair/approve")
async def pair_approve(req: PairApproveRequest, current_user = Depends(get_current_user)):
    """El usuario autenticado (teléfono) aprueba el código mostrado en la TV."""
    ok = db.approve_pair_code(req.code.upper().strip(), current_user["id"])
    if not ok:
        raise HTTPException(status_code=400, detail="Código inválido, ya usado o expirado")
    return {"success": True}

# ============================================================
# Historial de Reproducción
# ============================================================

@app.post("/api/user/history")
async def add_history(req: HistoryRequest, current_user = Depends(get_current_user)):
    """Guarda que el usuario vio una película/episodio."""
    db.add_to_history(current_user["id"], req.movie_id, req.episode_url)
    return {"success": True}

@app.get("/api/user/history")
async def get_history(current_user = Depends(get_current_user)):
    """Historial de reproducción del usuario."""
    history = db.get_history(current_user["id"])
    # Serializar fechas
    for item in history:
        if item.get("last_watched"):
            item["last_watched"] = str(item["last_watched"])
    return {"success": True, "data": history}

@app.delete("/api/user/history/{movie_id}")
async def remove_history(movie_id: str, current_user = Depends(get_current_user)):
    """Elimina una película/serie del historial de reproducción."""
    db.remove_from_history(current_user["id"], movie_id)
    return {"success": True}

# ============================================================
# Watchlist (Mi Lista)
# ============================================================

@app.post("/api/user/watchlist")
async def add_watchlist(req: WatchlistRequest, current_user = Depends(get_current_user)):
    """Agrega a Mi Lista."""
    db.add_to_watchlist(current_user["id"], req.movie_id)
    return {"success": True, "in_watchlist": True}

@app.delete("/api/user/watchlist/{movie_id}")
async def remove_watchlist(movie_id: str, current_user = Depends(get_current_user)):
    """Quita de Mi Lista."""
    db.remove_from_watchlist(current_user["id"], movie_id)
    return {"success": True, "in_watchlist": False}

@app.get("/api/user/watchlist")
async def get_watchlist(current_user = Depends(get_current_user)):
    """Mi Lista completa."""
    items = db.get_watchlist(current_user["id"])
    for item in items:
        if item.get("added_at"):
            item["added_at"] = str(item["added_at"])
    return {"success": True, "data": items}

@app.get("/api/user/watchlist/check/{movie_id}")
async def check_watchlist(movie_id: str, current_user = Depends(get_current_user)):
    """Verifica si una película está en Mi Lista."""
    in_list = db.is_in_watchlist(current_user["id"], movie_id)
    return {"success": True, "in_watchlist": in_list}


# ============================================================
# Admin API Keys Management
# ============================================================
class APIKeyRequest(BaseModel):
    name: str

@app.get("/api/admin/keys")
async def get_api_keys(current_user = Depends(get_current_user)):
    """Obtiene todas las llaves de terceros (Solo Admin)."""
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="No autorizado")
    
    keys = db.get_all_api_keys()
    for k in keys:
        if k.get("created_at"):
            k["created_at"] = str(k["created_at"])
    return {"success": True, "data": keys}

@app.post("/api/admin/keys")
async def create_api_key(req: APIKeyRequest, current_user = Depends(get_current_user)):
    """Genera una nueva llave para un tercero."""
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="No autorizado")
    
    import secrets
    new_key = "nsr_" + secrets.token_hex(16)
    
    key_data = db.create_api_key(new_key, req.name)
    if not key_data:
        raise HTTPException(status_code=500, detail="Error al crear la llave")
        
    if key_data.get("created_at"):
        key_data["created_at"] = str(key_data["created_at"])
        
    return {"success": True, "data": key_data}

@app.delete("/api/admin/keys/{key_id}")
async def revoke_api_key(key_id: int, current_user = Depends(get_current_user)):
    """Revoca una llave."""
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="No autorizado")
    
    success = db.revoke_api_key(key_id)
    return {"success": success}

<div align="center">

# 🎬 NSR FLIX

### Web de streaming con scraper y bloqueador de anuncios

Scraper multi-sitio + API REST híbrida que agrega **películas, series y anime** y los sirve a un frontend tipo Netflix.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![aiohttp](https://img.shields.io/badge/aiohttp-async-2B5B84?style=flat-square)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white)
![Fly.io](https://img.shields.io/badge/Fly.io-24175B?style=flat-square&logo=fly&logoColor=white)

*Desarrollado por [ChescoG0D](https://github.com/ChescoG0D)*

</div>

---

## ✨ Características

- 🕷️ **Scraper asíncrono multi-sitio** — 4 proveedores scrapeados en paralelo con control de concurrencia y caché anti-reproceso.
- 🧩 **Resolvers de vídeo** — extracción de la URL directa desde Voe, Streamwish y Streamtape revirtiendo su ofuscación JS (rot13, base64, desplazamientos).
- 🗄️ **Estrategia híbrida** — Postgres como catálogo cacheado + scrapeo en vivo como fallback cuando falta un título.
- ⏰ **Scrapeo diario automático** — APScheduler actualiza el catálogo a las 3:00 AM y limpia entradas inválidas.
- 🔐 **Auth completa** — registro/login por email + Google OAuth, JWT firmado, rate limiting anti fuerza bruta.
- 📺 **Vinculación de TV** — emparejamiento por código de 6 caracteres, al estilo Netflix.
- 🛡️ **API con llaves** — master key + API keys revocables desde el panel de administración.
- 🙈 **URLs ofuscadas** — los enlaces de vídeo viajan ofuscados entre backend y frontend.

## 🏗️ Arquitectura

```
├── main.py            Orchestrator CLI: scrapea catálogos y guarda en Postgres
├── api.py             API FastAPI (~30 endpoints): catálogo, búsqueda, auth, resolvers
├── database.py        DatabaseManager: esquema, usuarios, historial, watchlist, API keys
├── engine.py          AsyncEngine: descargas HTTP concurrentes (aiohttp) + BeautifulSoup
├── utils.py           Ofuscación / desofuscación de URLs con la master key
├── scrapers/          Un scraper por sitio fuente
│   ├── pelisplus.py     → SOLO series y anime
│   ├── cinehdplus.py    → SOLO películas
│   ├── repelis.py       → estrenos / home
│   └── zonaaps.py       → catálogo adicional
└── resolvers/         Extractores de URL directa de vídeo
    ├── voe.py
    ├── streamwish.py
    └── streamtape.py
```

> [!NOTE]
> **Regla estricta proveedor → tipo:** CineHDPlus solo emite películas y PelisPlus solo series/anime. El orquestador, la búsqueda y la limpieza diaria fuerzan esta regla en todo el sistema.

## 📡 Endpoints principales

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/home` | Estrenos y secciones para el Home |
| `GET` | `/api/catalog?type=&page=` | Catálogo paginado desde la BD |
| `GET` | `/api/search?q=` | Búsqueda en vivo + BD (con regla de tipos) |
| `GET` | `/api/info/{slug}` | Detalles completos (raspa en vivo si faltan) |
| `GET` | `/api/episode/servers` | Iframes de servidores de un episodio |
| `GET` | `/api/resolve` | URL directa de vídeo desde un iframe |
| `POST` | `/api/auth/register` · `/login` · `/google` | Autenticación (JWT + Google OAuth) |
| `GET/POST/DELETE` | `/api/user/history` · `/api/user/watchlist` | Historial y "Mi Lista" |
| `POST` | `/api/pair/create` · `/pair/approve` | Vinculación de TV por código |
| `GET` | `/api/admin/stats` · `/api/admin/keys` | Estadísticas y gestión de API keys |

Toda petición requiere el header `X-NSR-API-KEY` (master key o key de BD generada desde el panel admin).

## 🔐 Seguridad

- Rate limiting en los endpoints sensibles: login (5/min), registro (3/min), Google (5/min), pair (5/min).
- JWT firmado con `JWT_SECRET`; el token de Google se verifica en servidor contra `GOOGLE_CLIENT_ID`.
- Proxy de imágenes `/api/image` protegido con whitelist de dominios y límite de tamaño.
- Documentación interactiva (`/docs`, `/redoc`) desactivada en producción.
- Las URLs de vídeo se transmiten ofuscadas (hex + desplazamiento con la master key) y se decodifican en el cliente.

## 🚀 Puesta en marcha

```bash
# 1. Clonar e instalar dependencias
git clone https://github.com/ChescoG0D/NSRFLIX.git
cd NSRFLIX
pip install -r requirements.txt

# 2. Configurar el entorno
cp .env.example .env    # rellena los valores

# 3. Scrapear el catálogo (CLI)
python main.py --provider all --category all --pages 2

# 4. Arrancar la API
uvicorn api:app --host 0.0.0.0 --port 8080
```

<details>
<summary><b>🐳 Despliegue con Docker / Fly.io</b></summary>

```bash
# Docker local
docker build -t nsrflix .
docker run --env-file .env -p 8080:8080 nsrflix

# Fly.io (DATABASE_URL se inyecta tras attaching Postgres)
fly launch          # usa el fly.toml incluido
fly postgres attach <nombre-postgres>
fly deploy
```

En producción la API arranca con `uvicorn` y programa el scrapeo diario automático (3:00 AM) seguido de la limpieza de entradas que violen la regla proveedor → tipo.

</details>

## ⚙️ Variables de entorno

| Variable | Descripción |
|----------|-------------|
| `NSR_MASTER_KEY` | Llave maestra: valida peticiones y ofusca URLs |
| `JWT_SECRET` | Secreto de firma de los JWT |
| `NSR_ADMIN_EMAIL` | Email del primer administrador (se aplica al iniciar la BD) |
| `GOOGLE_CLIENT_ID` | Client ID de Google OAuth (opcional) |
| `DATABASE_URL` | Cadena de conexión Postgres |

## 🧰 Scripts auxiliares

| Script | Uso |
|--------|-----|
| `play.py` | Reproductor CLI de prueba: busca en la BD, resuelve servidores y abre el vídeo |
| `test_scrape.py` | Prueba rápida de un scraper individual |
| `delete_fake_series.py` | Elimina series falsas detectadas en cinehdplus |
| `fetch_html.py` | Descarga el HTML de una página para depurar parsers |

---

<div align="center">

⚠️ *Proyecto con fines educativos y de aprendizaje. El desarrollo y despliegue son responsabilidad de cada usuario.*

**NSR FLIX** — hecho con ⚡ por [ChescoG0D](https://github.com/ChescoG0D)

</div>

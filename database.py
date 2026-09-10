import psycopg2
import psycopg2.extras
import json
import os
import datetime
from contextlib import contextmanager

class DatabaseManager:
    def __init__(self, db_url=None):
        # En Fly.io, la URL se inyecta en DATABASE_URL tras hacer 'fly postgres attach'
        self.db_url = db_url or os.environ.get("DATABASE_URL")
        
        if not self.db_url:
            print("ADVERTENCIA: No se encontró DATABASE_URL. La Base de datos no funcionará hasta configurarla.")
        else:
            self._init_db()

    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(self.db_url)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Inicializa la base de datos y crea las tablas si no existen."""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # Tabla de películas
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS movies (
                        id TEXT PRIMARY KEY,
                        slug TEXT,
                        title TEXT,
                        original_title TEXT,
                        synopsis TEXT,
                        poster TEXT,
                        rating TEXT,
                        year TEXT,
                        type TEXT,
                        url TEXT,
                        provider TEXT,
                        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                # Configuración del sistema (Cron Perezoso, etc)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS system_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                # Tabla de géneros
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS genres (
                        id SERIAL PRIMARY KEY,
                        name TEXT UNIQUE,
                        slug TEXT UNIQUE
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS movie_genres (
                        movie_id TEXT,
                        genre_id INTEGER,
                        FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE,
                        FOREIGN KEY(genre_id) REFERENCES genres(id) ON DELETE CASCADE,
                        PRIMARY KEY (movie_id, genre_id)
                    )
                ''')
                
                # Tabla cruda (para guardar toda la info JSON)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS raw_data (
                        movie_id TEXT PRIMARY KEY,
                        json_data TEXT,
                        FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE
                    )
                ''')
                # Tabla de usuarios
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        email VARCHAR UNIQUE NOT NULL,
                        name VARCHAR,
                        avatar_url VARCHAR,
                        google_id VARCHAR UNIQUE,
                        password_hash VARCHAR,
                        is_admin BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                ''')
                # Migración segura por si la tabla ya existía sin la columna
                cursor.execute('''
                    DO $$ 
                    BEGIN 
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='is_admin') THEN 
                            ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE;
                        END IF;
                    END $$;
                ''')
                # Configurar el admin desde variable de entorno (si existe)
                admin_email = os.environ.get("NSR_ADMIN_EMAIL")
                if admin_email:
                    cursor.execute('UPDATE users SET is_admin = TRUE WHERE email = %s', (admin_email,))
                conn.commit()
                # Historial de reproducción
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS watch_history (
                        id SERIAL PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        movie_id VARCHAR NOT NULL,
                        episode_url VARCHAR,
                        last_watched TIMESTAMP DEFAULT NOW(),
                        UNIQUE(user_id, movie_id, episode_url)
                    )
                ''')
                # Lista de "Ver más tarde"
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS watchlist (
                        id SERIAL PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        movie_id VARCHAR NOT NULL,
                        added_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(user_id, movie_id)
                    )
                ''')
                # Contador de visitas del sitio
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS site_stats (
                        id INT PRIMARY KEY DEFAULT 1,
                        total_visits BIGINT DEFAULT 0,
                        CONSTRAINT single_row CHECK (id = 1)
                    )
                ''')
                cursor.execute('''
                    INSERT INTO site_stats (id, total_visits) VALUES (1, 0)
                    ON CONFLICT (id) DO NOTHING
                ''')
                
                # Tabla de llaves API
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS api_keys (
                        id SERIAL PRIMARY KEY,
                        key TEXT UNIQUE NOT NULL,
                        name TEXT NOT NULL,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Tabla para el grafico de uso de API
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS api_logs (
                        log_date DATE PRIMARY KEY DEFAULT CURRENT_DATE,
                        requests INT DEFAULT 0
                    )
                ''')
                # Tabla para registro de visitantes unicos por IP
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS unique_visitors (
                        ip VARCHAR PRIMARY KEY,
                        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                # Códigos de vinculación TV (login por código estilo YouTube)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS pair_codes (
                        code VARCHAR(10) PRIMARY KEY,
                        user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP + INTERVAL '10 minutes'
                    )
                ''')
                conn.commit()

    def clear_db(self):
        """Borra todos los registros de la base de datos."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('TRUNCATE movies, genres, movie_genres, raw_data CASCADE')
                conn.commit()
                return True

    def movie_exists(self, movie_id):
        """Verifica si una película ya existe en la base de datos."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT 1 FROM movies WHERE id = %s', (movie_id,))
                return cursor.fetchone() is not None

    # ============================================================
    # Códigos de vinculación TV (Login por código)
    # ============================================================

    def create_pair_code(self):
        """Genera un código de 6 caracteres para vincular una TV. Vigencia: 10 min."""
        import secrets as _secrets
        if not self.db_url: return None
        # Sin caracteres ambiguos (0/O, 1/I) para poder escribirlo fácil en el control
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # Limpieza de códigos vencidos
                cursor.execute('DELETE FROM pair_codes WHERE expires_at < CURRENT_TIMESTAMP')
                for _ in range(5):
                    code = ''.join(_secrets.choice(alphabet) for _ in range(6))
                    try:
                        cursor.execute('INSERT INTO pair_codes (code) VALUES (%s)', (code,))
                        conn.commit()
                        return code
                    except Exception:
                        continue  # colisión extremadamente improbable, reintentar
                return None

    def approve_pair_code(self, code, user_id):
        """Vincula un código pendiente a un usuario (desde su teléfono)."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    UPDATE pair_codes SET user_id = %s
                    WHERE code = %s AND user_id IS NULL AND expires_at > CURRENT_TIMESTAMP
                ''', (user_id, code))
                conn.commit()
                return cursor.rowcount > 0

    def get_pair_status(self, code):
        """Estado de un código: existe / aprobado (con usuario) / expirado."""
        if not self.db_url: return {"exists": False}
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute('''
                    SELECT p.code, p.user_id, p.expires_at,
                           u.id, u.email, u.name, u.avatar_url, u.is_admin, u.created_at
                    FROM pair_codes p
                    LEFT JOIN users u ON u.id = p.user_id
                    WHERE p.code = %s
                ''', (code,))
                row = cursor.fetchone()
                if not row:
                    return {"exists": False}
                user = None
                if row.get("id"):
                    user = {
                        "id": str(row["id"]),
                        "email": row["email"],
                        "name": row["name"],
                        "avatar_url": row["avatar_url"],
                        "is_admin": row["is_admin"] or False,
                    }
                return {
                    "exists": True,
                    "approved": user is not None,
                    "expired": row["expires_at"].replace(tzinfo=None) < datetime.datetime.now(),
                    "user": user,
                }

    def consume_pair_code(self, code):
        """Elimina el código tras entregar el token (uso único)."""
        if not self.db_url: return
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('DELETE FROM pair_codes WHERE code = %s', (code,))
                conn.commit()

    # ============================================================
    # Llaves API (API Keys)
    # ============================================================    
    def create_api_key(self, key, name):
        """Crea una nueva API Key en la base de datos."""
        if not self.db_url: return None
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                try:
                    cursor.execute('''
                        INSERT INTO api_keys (key, name)
                        VALUES (%s, %s)
                        RETURNING id, key, name, is_active, created_at
                    ''', (key, name))
                    conn.commit()
                    return dict(cursor.fetchone())
                except Exception:
                    conn.rollback()
                    return None

    def get_all_api_keys(self):
        """Obtiene todas las API Keys registradas."""
        if not self.db_url: return []
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute('SELECT id, key, name, is_active, created_at FROM api_keys ORDER BY created_at DESC')
                return [dict(r) for r in cursor.fetchall()]

    def revoke_api_key(self, key_id):
        """Desactiva o elimina una API Key."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('DELETE FROM api_keys WHERE id = %s', (key_id,))
                conn.commit()
                return cursor.rowcount > 0

    def is_valid_api_key(self, key):
        """Verifica si una API Key es válida y está activa."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT 1 FROM api_keys WHERE key = %s AND is_active = TRUE', (key,))
                return cursor.fetchone() is not None

    def save_movie(self, movie_data):
        """Guarda o actualiza una película en la base de datos de Postgres."""
        if not movie_data.get('id') or not self.db_url:
            return False

        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                
                # Insertar o actualizar la película (ON CONFLICT Postgres)
                cursor.execute('''
                    INSERT INTO movies 
                    (id, slug, title, original_title, synopsis, poster, rating, year, type, url, provider) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        slug = EXCLUDED.slug,
                        title = EXCLUDED.title,
                        original_title = EXCLUDED.original_title,
                        synopsis = EXCLUDED.synopsis,
                        poster = EXCLUDED.poster,
                        rating = EXCLUDED.rating,
                        year = EXCLUDED.year,
                        type = EXCLUDED.type,
                        url = EXCLUDED.url,
                        provider = EXCLUDED.provider
                ''', (
                    movie_data.get('id'),
                    movie_data.get('slug', ''),
                    movie_data.get('title', ''),
                    movie_data.get('originalTitle', ''),
                    movie_data.get('synopsis', ''),
                    movie_data.get('poster', ''),
                    movie_data.get('rating', ''),
                    movie_data.get('year', ''),
                    movie_data.get('type', 'movie'),
                    movie_data.get('url', ''),
                    movie_data.get('provider', '')
                ))
                
                # Guardar el JSON completo como backup
                cursor.execute('''
                    INSERT INTO raw_data (movie_id, json_data) 
                    VALUES (%s, %s)
                    ON CONFLICT (movie_id) DO UPDATE SET
                        json_data = EXCLUDED.json_data
                ''', (movie_data.get('id'), json.dumps(movie_data, ensure_ascii=False)))
                
                # Guardar géneros
                if 'genres' in movie_data:
                    for genre in movie_data['genres']:
                        genre_name = genre.get('name', genre) if isinstance(genre, dict) else genre
                        genre_slug = genre.get('slug', genre_name.lower().replace(' ', '-')) if isinstance(genre, dict) else genre_name.lower().replace(' ', '-')
                        
                        # Insertar género ignorando si ya existe
                        cursor.execute('''
                            INSERT INTO genres (name, slug) VALUES (%s, %s)
                            ON CONFLICT (name) DO NOTHING
                        ''', (genre_name, genre_slug))
                        
                        # Obtener ID del género
                        cursor.execute('SELECT id FROM genres WHERE name = %s', (genre_name,))
                        result = cursor.fetchone()
                        if result:
                            genre_id = result[0]
                            # Relacionar película con género
                            cursor.execute('''
                                INSERT INTO movie_genres (movie_id, genre_id) VALUES (%s, %s)
                                ON CONFLICT (movie_id, genre_id) DO NOTHING
                            ''', (movie_data['id'], genre_id))
                
                conn.commit()
                return True

    def get_all_movies(self):
        """Retorna todas las películas almacenadas (desde raw_data)."""
        if not self.db_url: return []
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT json_data FROM raw_data')
                rows = cursor.fetchall()
                return [json.loads(row[0]) for row in rows]

    # ============================================================
    # Métodos de Usuarios
    # ============================================================

    def create_user(self, email, name, password_hash=None, google_id=None, avatar_url=None):
        """Crea un nuevo usuario. Retorna el usuario creado o None si ya existe."""
        if not self.db_url: return None
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                try:
                    cursor.execute('''
                        INSERT INTO users (email, name, password_hash, google_id, avatar_url)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id, email, name, avatar_url, is_admin, created_at
                    ''', (email, name, password_hash, google_id, avatar_url))
                    conn.commit()
                    return dict(cursor.fetchone())
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    return None

    def get_user_by_email(self, email):
        """Busca un usuario por email."""
        if not self.db_url: return None
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute('SELECT * FROM users WHERE email = %s', (email,))
                row = cursor.fetchone()
                return dict(row) if row else None

    def get_user_by_google_id(self, google_id):
        """Busca un usuario por su Google ID."""
        if not self.db_url: return None
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute('SELECT * FROM users WHERE google_id = %s', (google_id,))
                row = cursor.fetchone()
                return dict(row) if row else None

    def get_user_by_id(self, user_id):
        """Busca un usuario por su ID."""
        if not self.db_url: return None
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute('SELECT id, email, name, avatar_url, is_admin, created_at FROM users WHERE id = %s', (str(user_id),))
                row = cursor.fetchone()
                return dict(row) if row else None

    def upsert_google_user(self, google_id, email, name, avatar_url):
        """Crea o actualiza un usuario de Google. Retorna el usuario."""
        if not self.db_url: return None
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute('''
                    INSERT INTO users (email, name, google_id, avatar_url)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (google_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        avatar_url = EXCLUDED.avatar_url
                    RETURNING id, email, name, avatar_url, is_admin, created_at
                ''', (email, name, google_id, avatar_url))
                conn.commit()
                return dict(cursor.fetchone())

    # ============================================================
    # Historial de Reproducción
    # ============================================================

    def add_to_history(self, user_id, movie_id, episode_url=None):
        """Agrega o actualiza una entrada en el historial del usuario."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    INSERT INTO watch_history (user_id, movie_id, episode_url, last_watched)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (user_id, movie_id, episode_url)
                    DO UPDATE SET last_watched = NOW()
                ''', (str(user_id), movie_id, episode_url or ''))
                conn.commit()
                return True

    def get_history(self, user_id, limit=20):
        """Obtiene el historial de reproducción del usuario."""
        if not self.db_url: return []
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute('''
                    SELECT wh.movie_id, wh.episode_url, wh.last_watched,
                           m.title, m.poster, m.type, m.slug
                    FROM watch_history wh
                    LEFT JOIN movies m ON wh.movie_id = m.id
                    WHERE wh.user_id = %s
                    ORDER BY wh.last_watched DESC
                    LIMIT %s
                ''', (str(user_id), limit))
                return [dict(r) for r in cursor.fetchall()]

    def remove_from_history(self, user_id, movie_id):
        """Elimina una película/serie del historial del usuario."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    DELETE FROM watch_history 
                    WHERE user_id = %s AND movie_id = %s
                ''', (str(user_id), movie_id))
                conn.commit()
                return True

    # ============================================================
    # Watchlist (Mi Lista)
    # ============================================================

    def add_to_watchlist(self, user_id, movie_id):
        """Agrega una pelicula a la lista del usuario."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                try:
                    cursor.execute('''
                        INSERT INTO watchlist (user_id, movie_id)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                    ''', (str(user_id), movie_id))
                    conn.commit()
                    return True
                except Exception as e:
                    print(f"Error borrando history: {e}")
                    conn.rollback()
                    return False

    def log_api_request(self):
        """Registra una peticion a la API en el dia actual."""
        if not self.db_url:
            return
        with self.get_connection() as conn:
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO api_logs (log_date, requests) 
                    VALUES (CURRENT_DATE, 1)
                    ON CONFLICT (log_date) 
                    DO UPDATE SET requests = api_logs.requests + 1
                ''')
                conn.commit()
            except Exception as e:
                print(f"Error logging api request: {e}")
                conn.rollback()

    def get_api_chart_data(self):
        """Obtiene las consultas de los ultimos 7 dias."""
        if not self.db_url:
            return []
        with self.get_connection() as conn:
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT log_date, requests 
                    FROM api_logs 
                    ORDER BY log_date DESC 
                    LIMIT 7
                ''')
                rows = cursor.fetchall()
                # Revertir para mostrar de mas antiguo a mas reciente
                return [{"date": str(row[0]), "requests": row[1]} for row in reversed(rows)]
            except Exception as e:
                print(f"Error getting api chart data: {e}")
                return []

    def log_unique_visitor(self, ip: str):
        """Registra una IP unica en la tabla unique_visitors."""
        if not self.db_url or not ip:
            return
        with self.get_connection() as conn:
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO unique_visitors (ip, last_seen) 
                    VALUES (%s, CURRENT_TIMESTAMP)
                    ON CONFLICT (ip) DO UPDATE SET last_seen = CURRENT_TIMESTAMP
                ''', (ip,))
                conn.commit()
            except Exception as e:
                print(f"Error logging unique visitor: {e}")
                conn.rollback()

    def get_unique_visitors_count(self) -> int:
        """Obtiene la cantidad total de IPs/Visitantes unicos."""
        if not self.db_url:
            return 0
        with self.get_connection() as conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM unique_visitors")
                row = cursor.fetchone()
                return row[0] if row else 0
            except Exception as e:
                print(f"Error getting unique visitors count: {e}")
                return 0


    def remove_from_watchlist(self, user_id, movie_id):
        """Quita una película de la lista del usuario."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    'DELETE FROM watchlist WHERE user_id = %s AND movie_id = %s',
                    (str(user_id), movie_id)
                )
                conn.commit()
                return True

    def get_watchlist(self, user_id):
        """Obtiene la lista completa del usuario."""
        if not self.db_url: return []
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute('''
                    SELECT wl.movie_id, wl.added_at,
                           m.title, m.poster, m.type, m.slug, m.year
                    FROM watchlist wl
                    LEFT JOIN movies m ON wl.movie_id = m.id
                    WHERE wl.user_id = %s
                    ORDER BY wl.added_at DESC
                ''', (str(user_id),))
                return [dict(r) for r in cursor.fetchall()]

    def is_in_watchlist(self, user_id, movie_id):
        """Verifica si una pelicula está en la lista del usuario."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    'SELECT 1 FROM watchlist WHERE user_id = %s AND movie_id = %s',
                    (str(user_id), movie_id)
                )
                return cursor.fetchone() is not None

    def get_system_setting(self, key, default=None):
        """Obtiene un valor de la tabla system_settings."""
        if not self.db_url: return default
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT value FROM system_settings WHERE key = %s', (key,))
                row = cursor.fetchone()
                return row[0] if row else default

    def set_system_setting(self, key, value):
        """Guarda o actualiza un valor en system_settings."""
        if not self.db_url: return False
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    INSERT INTO system_settings (key, value, updated_at) 
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                ''', (key, str(value)))
                conn.commit()
                return True

    def delete_fake_series(self):
        """Elimina las series que se hayan colado en scrapers de películas (como cinehdplus)."""
        if not self.db_url: return 0
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # Eliminamos si el título empieza con 'ver serie' o 'serie' (case insensitive)
                cursor.execute('''
                    DELETE FROM movies 
                    WHERE (title ILIKE 'ver serie%%' OR title ILIKE 'serie%%' OR title ILIKE '%%serie%%')
                    AND provider = 'cinehdplus'
                ''')
                deleted = cursor.rowcount
                conn.commit()
                return deleted

    def delete_legacy_movies(self):
        """Regla estricta: elimina películas que NO provengan de cinehdplus y entradas bloqueadas."""
        if not self.db_url: return 0
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                    DELETE FROM movies 
                    WHERE (type = 'movie' AND provider NOT IN ('cinehdplus', 'cinehd'))
                       OR type = 'blocked'
                ''')
                deleted = cursor.rowcount
                conn.commit()
                return deleted

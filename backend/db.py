# db.py
# SQLite local (dev) o Turso (producción).
# Variables en Render:
#   TURSO_DATABASE_URL=libsql://nombre-org.turso.io
#   TURSO_AUTH_TOKEN=...
import os
import secrets
import string
import re

def _clean(val):
    if not val:
        return ""
    v = str(val).strip().strip('"').strip("'").strip()
    # quitar saltos de línea / espacios raros
    v = re.sub(r"\s+", "", v)
    return v

TURSO_URL = _clean(os.environ.get("TURSO_DATABASE_URL", ""))
TURSO_TOKEN = _clean(os.environ.get("TURSO_AUTH_TOKEN", ""))
USE_TURSO = bool(TURSO_URL and TURSO_TOKEN)

DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(__file__), "enlace-escolar.db")
)

_con = None
_client = None
_db_error = None


def _normalize_turso_url(url):
    """Asegura un host válido para evitar errores idna/label empty."""
    u = _clean(url)
    if not u:
        return u
    # Si pegaron https://, convertir a libsql://
    if u.startswith("https://"):
        u = "libsql://" + u[len("https://"):]
    if u.startswith("http://"):
        u = "libsql://" + u[len("http://"):]
    # Quitar barra final
    u = u.rstrip("/")
    # Evitar puntos dobles en el host
    if "://" in u:
        scheme, rest = u.split("://", 1)
        rest = re.sub(r"\.{2,}", ".", rest)
        rest = rest.strip(".")
        u = scheme + "://" + rest
    return u


def _connect():
    global _con, _client, _db_error, TURSO_URL, USE_TURSO
    _db_error = None
    if USE_TURSO:
        TURSO_URL = _normalize_turso_url(TURSO_URL)
        print(f"DB: intentando Turso -> {TURSO_URL[:40]}...")
        try:
            from libsql_client import create_client_sync
            _client = create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)
            # prueba rápida
            _client.execute("SELECT 1")
            _con = None
            print("DB: Turso OK (datos permanentes)")
        except Exception as e:
            _db_error = str(e)
            _client = None
            print(f"DB ERROR Turso: {e}")
            # Fallback local para que el servidor al menos arranque y /api/salud responda
            import sqlite3
            _con = sqlite3.connect(DB_PATH, check_same_thread=False)
            _con.row_factory = sqlite3.Row
            _con.execute("PRAGMA foreign_keys = ON")
            USE_TURSO = False
            print("DB: fallback a SQLite local (revisa TURSO_DATABASE_URL y TOKEN)")
    else:
        import sqlite3
        _con = sqlite3.connect(DB_PATH, check_same_thread=False)
        _con.row_factory = sqlite3.Row
        _con.execute("PRAGMA foreign_keys = ON")
        _client = None
        print(f"DB: SQLite local -> {DB_PATH}")


_connect()


def get_conn():
    return _con


def db_status():
    return {
        "turso_configured": bool(_clean(os.environ.get("TURSO_DATABASE_URL", "")) and _clean(os.environ.get("TURSO_AUTH_TOKEN", ""))),
        "using_turso": USE_TURSO and _client is not None,
        "error": _db_error,
    }


def execute(sql, params=()):
    params = tuple(params) if params is not None else ()
    if USE_TURSO and _client is not None:
        rs = _client.execute(sql, list(params))
        cols = list(rs.columns) if getattr(rs, "columns", None) else []
        rows_data = []
        for r in rs.rows:
            if hasattr(r, "tolist"):
                rows_data.append(tuple(r.tolist()))
            elif isinstance(r, (list, tuple)):
                rows_data.append(tuple(r))
            else:
                try:
                    rows_data.append(tuple(r))
                except TypeError:
                    rows_data.append((r,))
        return cols, rows_data
    else:
        cur = _con.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        data = cur.fetchall()
        return cols, [tuple(r) for r in data]


def execute_write(sql, params=()):
    params = tuple(params) if params is not None else ()
    if USE_TURSO and _client is not None:
        _client.execute(sql, list(params))
    else:
        _con.execute(sql, params)
        _con.commit()


def init_db():
    statements = [
        """CREATE TABLE IF NOT EXISTS usuarios (
            id TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            rol TEXT NOT NULL,
            clave_acceso TEXT UNIQUE,
            curso_id TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS cursos (
            id TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            clave_curso TEXT UNIQUE NOT NULL,
            tutor_id TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS docente_cursos (
            id TEXT PRIMARY KEY,
            docente_id TEXT NOT NULL,
            curso_id TEXT NOT NULL,
            asignatura TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS estudiantes (
            id TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            curso_id TEXT NOT NULL,
            tutor_id TEXT NOT NULL,
            representante_id TEXT,
            representante_nombre_sugerido TEXT,
            representante_contacto TEXT,
            codigo_invitacion TEXT UNIQUE NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS mensajes (
            id TEXT PRIMARY KEY,
            estudiante_id TEXT NOT NULL,
            remitente_id TEXT NOT NULL,
            remitente_rol TEXT NOT NULL,
            tipo TEXT NOT NULL,
            texto TEXT NOT NULL,
            fecha TEXT NOT NULL,
            confirmado_tutor INTEGER NOT NULL DEFAULT 0,
            confirmado_representante INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS dispositivos_push (
            id TEXT PRIMARY KEY,
            usuario_id TEXT NOT NULL,
            token TEXT NOT NULL
        )""",
    ]
    for s in statements:
        execute_write(s)


def uid(prefix="id"):
    return prefix + "_" + secrets.token_hex(6)


def codigo6():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))

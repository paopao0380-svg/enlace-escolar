# db.py
# Soporta SQLite local (desarrollo) y Turso (producción, datos permanentes).
# En Render configura:
#   TURSO_DATABASE_URL = libsql://...
#   TURSO_AUTH_TOKEN  = ...
import os
import secrets
import string

TURSO_URL = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
TURSO_TOKEN = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
USE_TURSO = bool(TURSO_URL and TURSO_TOKEN)

DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(__file__), "enlace-escolar.db")
)

_con = None
_client = None


def _connect():
    global _con, _client
    if USE_TURSO:
        try:
            from libsql_client import create_client_sync
        except ImportError:
            raise RuntimeError(
                "Falta el paquete libsql-client. Agrega libsql-client a requirements.txt"
            )
        _client = create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)
        _con = None
        print("DB: conectado a Turso (datos permanentes)")
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


def _row_to_dict(columns, values):
    if values is None:
        return None
    return {columns[i]: values[i] for i in range(len(columns))}


def execute(sql, params=()):
    """Ejecuta SQL y devuelve (columns, rows_as_tuples)."""
    params = tuple(params) if params is not None else ()
    if USE_TURSO:
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
        # Si no hay columns en el result, intentar inferir de vacío
        return cols, rows_data
    else:
        cur = _con.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        data = cur.fetchall()
        rows_data = [tuple(r) for r in data]
        return cols, rows_data


def execute_write(sql, params=()):
    params = tuple(params) if params is not None else ()
    if USE_TURSO:
        _client.execute(sql, list(params))
    else:
        _con.execute(sql, params)
        _con.commit()


def init_db():
    script = """
        CREATE TABLE IF NOT EXISTS usuarios (
            id            TEXT PRIMARY KEY,
            nombre        TEXT NOT NULL,
            rol           TEXT NOT NULL,
            clave_acceso  TEXT UNIQUE,
            curso_id      TEXT
        );

        CREATE TABLE IF NOT EXISTS cursos (
            id           TEXT PRIMARY KEY,
            nombre       TEXT NOT NULL,
            clave_curso  TEXT UNIQUE NOT NULL,
            tutor_id     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS docente_cursos (
            id          TEXT PRIMARY KEY,
            docente_id  TEXT NOT NULL,
            curso_id    TEXT NOT NULL,
            asignatura  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS estudiantes (
            id                              TEXT PRIMARY KEY,
            nombre                          TEXT NOT NULL,
            curso_id                        TEXT NOT NULL,
            tutor_id                        TEXT NOT NULL,
            representante_id                TEXT,
            representante_nombre_sugerido   TEXT,
            representante_contacto          TEXT,
            codigo_invitacion               TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mensajes (
            id                        TEXT PRIMARY KEY,
            estudiante_id             TEXT NOT NULL,
            remitente_id              TEXT NOT NULL,
            remitente_rol             TEXT NOT NULL,
            tipo                      TEXT NOT NULL,
            texto                     TEXT NOT NULL,
            fecha                     TEXT NOT NULL,
            confirmado_tutor          INTEGER NOT NULL DEFAULT 0,
            confirmado_representante  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS dispositivos_push (
            id           TEXT PRIMARY KEY,
            usuario_id   TEXT NOT NULL,
            token        TEXT NOT NULL
        );
    """
    if USE_TURSO:
        for stmt in script.split(";"):
            s = stmt.strip()
            if s:
                execute_write(s)
    else:
        _con.executescript(script)
        _con.commit()


def uid(prefix="id"):
    return prefix + "_" + secrets.token_hex(6)


def codigo6():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))

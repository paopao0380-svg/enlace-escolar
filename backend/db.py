# db.py
# SQLite local o Turso vía HTTP API (sin WebSocket; más estable en Render).
import os
import re
import json
import secrets
import string
import urllib.request
import urllib.error

def _clean(val):
    if not val:
        return ""
    v = str(val).strip().strip('"').strip("'")
    v = re.sub(r"\s+", "", v)
    return v

TURSO_URL_RAW = _clean(os.environ.get("TURSO_DATABASE_URL", ""))
TURSO_TOKEN = _clean(os.environ.get("TURSO_AUTH_TOKEN", ""))
USE_TURSO = bool(TURSO_URL_RAW and TURSO_TOKEN)

DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(__file__), "enlace-escolar.db")
)

_con = None
_http_base = None  # https://host
_db_error = None
_using_turso = False


def _to_https_base(url):
    u = _clean(url)
    if u.startswith("libsql://"):
        u = "https://" + u[len("libsql://"):]
    elif u.startswith("wss://"):
        u = "https://" + u[len("wss://"):]
    elif u.startswith("ws://"):
        u = "https://" + u[len("ws://"):]
    elif not u.startswith("https://"):
        u = "https://" + u
    return u.rstrip("/")


def _turso_pipeline(requests_list):
    """Llama al HTTP API de Turso /v2/pipeline."""
    body = json.dumps({"requests": requests_list}).encode("utf-8")
    req = urllib.request.Request(
        _http_base + "/v2/pipeline",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + TURSO_TOKEN,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Turso HTTP {e.code}: {err_body[:300]}")
    # Revisar errores en resultados
    results = data.get("results") or []
    for r in results:
        if r.get("type") == "error":
            raise RuntimeError(str(r.get("error") or r))
    return results


def _connect():
    global _con, _http_base, _db_error, _using_turso
    _db_error = None
    _using_turso = False
    if USE_TURSO:
        _http_base = _to_https_base(TURSO_URL_RAW)
        print(f"DB: Turso HTTP -> {_http_base}")
        try:
            _turso_pipeline([{"type": "execute", "stmt": {"sql": "SELECT 1"}}])
            _using_turso = True
            _con = None
            print("DB: Turso OK (datos permanentes via HTTP)")
        except Exception as e:
            _db_error = str(e)
            print(f"DB ERROR Turso: {e}")
            import sqlite3
            _con = sqlite3.connect(DB_PATH, check_same_thread=False)
            _con.row_factory = sqlite3.Row
            _con.execute("PRAGMA foreign_keys = ON")
            print("DB: fallback SQLite local")
    else:
        import sqlite3
        _con = sqlite3.connect(DB_PATH, check_same_thread=False)
        _con.row_factory = sqlite3.Row
        _con.execute("PRAGMA foreign_keys = ON")
        print(f"DB: SQLite local -> {DB_PATH}")


_connect()


def get_conn():
    return _con


def db_status():
    return {
        "turso_configured": bool(TURSO_URL_RAW and TURSO_TOKEN),
        "using_turso": _using_turso,
        "error": _db_error,
        "endpoint": _http_base if _http_base else None,
    }


def _parse_turso_execute(result_item):
    """Convierte respuesta Turso execute en (cols, rows)."""
    if not result_item or result_item.get("type") != "ok":
        err = (result_item or {}).get("error") or result_item
        raise RuntimeError(f"Turso execute error: {err}")
    response = result_item.get("response") or {}
    # format: response.result.cols / rows
    inner = response.get("result") or response
    cols_raw = inner.get("cols") or []
    cols = []
    for c in cols_raw:
        if isinstance(c, dict):
            cols.append(c.get("name") or c.get("nameof") or "")
        else:
            cols.append(str(c))
    rows_out = []
    for row in inner.get("rows") or []:
        values = []
        for cell in row:
            if isinstance(cell, dict):
                # {"type":"text","value":"..."} o integer, null, etc.
                t = cell.get("type")
                if t == "null" or cell.get("value") is None and t != "integer":
                    values.append(None)
                else:
                    values.append(cell.get("value"))
            else:
                values.append(cell)
        rows_out.append(tuple(values))
    return cols, rows_out


def execute(sql, params=()):
    params = list(params) if params is not None else []
    if _using_turso:
        # Turso args: list of {type, value}
        args = []
        for p in params:
            if p is None:
                args.append({"type": "null"})
            elif isinstance(p, bool):
                args.append({"type": "integer", "value": str(int(p))})
            elif isinstance(p, int):
                args.append({"type": "integer", "value": str(p)})
            elif isinstance(p, float):
                args.append({"type": "float", "value": str(p)})
            else:
                args.append({"type": "text", "value": str(p)})
        stmt = {"sql": sql}
        if args:
            stmt["args"] = args
        results = _turso_pipeline([{"type": "execute", "stmt": stmt}])
        # results[0] is execute result
        if not results:
            return [], []
        return _parse_turso_execute(results[0])
    else:
        cur = _con.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description] if cur.description else []
        data = cur.fetchall()
        return cols, [tuple(r) for r in data]


def execute_write(sql, params=()):
    params = list(params) if params is not None else []
    if _using_turso:
        args = []
        for p in params:
            if p is None:
                args.append({"type": "null"})
            elif isinstance(p, bool):
                args.append({"type": "integer", "value": str(int(p))})
            elif isinstance(p, int):
                args.append({"type": "integer", "value": str(p)})
            elif isinstance(p, float):
                args.append({"type": "float", "value": str(p)})
            else:
                args.append({"type": "text", "value": str(p)})
        stmt = {"sql": sql}
        if args:
            stmt["args"] = args
        # execute + commit
        _turso_pipeline([
            {"type": "execute", "stmt": stmt},
            {"type": "close"},
        ])
    else:
        _con.execute(sql, tuple(params))
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
        """CREATE TABLE IF NOT EXISTS mensajes_institucionales (
            id TEXT PRIMARY KEY,
            remitente_id TEXT NOT NULL,
            remitente_rol TEXT NOT NULL,
            destino_tipo TEXT NOT NULL,
            destino_id TEXT,
            tipo TEXT NOT NULL,
            texto TEXT NOT NULL,
            fecha TEXT NOT NULL
        )""",
    ]
    for s in statements:
        execute_write(s)


def ensure_foto_columns():
    for table in ("mensajes", "mensajes_institucionales"):
        for col in ("foto_url", "archivo_url", "archivo_nombre", "archivo_tipo"):
            try:
                execute_write("ALTER TABLE %s ADD COLUMN %s TEXT" % (table, col))
            except Exception:
                pass



def uid(prefix="id"):

    return prefix + "_" + secrets.token_hex(6)


def codigo6():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))

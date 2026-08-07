# server.py
# API de Enlace Escolar en Python puro (solo librería estándar: http.server + sqlite3).
# No requiere "pip install" de nada.
import datetime
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from db import get_conn, init_db, uid, codigo6

PORT = int(os.environ.get('PORT', 3000))

init_db()
con = get_conn()


# ---------- helpers de base de datos ----------
from db import execute, execute_write

def row(sql, params=()):
    cols, data = execute(sql, params)
    if not data:
        return None
    return {cols[i]: data[0][i] for i in range(len(cols))}


def rows(sql, params=()):
    cols, data = execute(sql, params)
    return [{cols[i]: r[i] for i in range(len(cols))} for r in data]


def run(sql, params=()):
    execute_write(sql, params)
    return None


# ---------- serializadores ----------
def ser_usuario(u):
    if not u:
        return None
    return {'id': u['id'], 'nombre': u['nombre'], 'rol': u['rol'], 'claveAcceso': u['clave_acceso'], 'cursoId': u['curso_id']}


def ser_curso(c):
    if not c:
        return None
    return {'id': c['id'], 'nombre': c['nombre'], 'claveCurso': c['clave_curso'], 'tutorId': c['tutor_id']}


def ser_estudiante(e):
    if not e:
        return None
    return {
        'id': e['id'], 'nombre': e['nombre'], 'cursoId': e['curso_id'], 'tutorId': e['tutor_id'],
        'representanteId': e['representante_id'], 'codigoInvitacion': e['codigo_invitacion'],
    }


def ser_mensaje(m):
    return {
        'id': m['id'], 'estudianteId': m['estudiante_id'], 'remitenteId': m['remitente_id'],
        'remitenteRol': m['remitente_rol'], 'tipo': m['tipo'], 'texto': m['texto'], 'fecha': m['fecha'],
        'confirmadoTutor': bool(m['confirmado_tutor']), 'confirmadoRepresentante': bool(m['confirmado_representante']),
    }


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


# ---------- handlers ----------
def h_crear_tutor(params, body):
    nombre = (body.get('nombre') or '').strip()
    curso_nombre = (body.get('cursoNombre') or '').strip()
    if not nombre or not curso_nombre:
        raise ApiError(400, 'Completa tu nombre y el nombre del curso.')
    clave = codigo6()
    curso_id = uid('curso')
    tutor_id = uid('u')
    run('INSERT INTO cursos (id, nombre, clave_curso, tutor_id) VALUES (?,?,?,?)', (curso_id, curso_nombre, clave, tutor_id))
    run('INSERT INTO usuarios (id, nombre, rol, curso_id) VALUES (?,?,?,?)', (tutor_id, nombre, 'tutor', curso_id))
    return 201, {'usuarioId': tutor_id, 'cursoId': curso_id, 'claveCurso': clave, 'cursoNombre': curso_nombre}


def h_login_tutor(params, body):
    clave = (body.get('claveCurso') or '').strip().upper()
    curso = row('SELECT * FROM cursos WHERE clave_curso = ?', (clave,))
    if not curso:
        raise ApiError(404, 'No existe ningún curso con esa clave.')
    tutor = row('SELECT * FROM usuarios WHERE id = ?', (curso['tutor_id'],))
    return 200, {'usuario': ser_usuario(tutor), 'curso': ser_curso(curso)}


def h_crear_estudiante(params, body):
    tutor_id = body.get('tutorId')
    curso_id = body.get('cursoId')
    nombre = (body.get('nombre') or '').strip()
    rep_nombre = (body.get('representanteNombre') or '').strip()
    rep_contacto = (body.get('representanteContacto') or '').strip()
    if not tutor_id or not curso_id or not nombre or not rep_nombre:
        raise ApiError(400, 'Completa el nombre del estudiante y del representante.')
    eid = uid('e')
    codigo = codigo6()
    run(
        '''INSERT INTO estudiantes (id, nombre, curso_id, tutor_id, representante_id, representante_nombre_sugerido, representante_contacto, codigo_invitacion)
           VALUES (?,?,?,?,NULL,?,?,?)''',
        (eid, nombre, curso_id, tutor_id, rep_nombre, rep_contacto, codigo),
    )
    return 201, {'estudiante': ser_estudiante(row('SELECT * FROM estudiantes WHERE id = ?', (eid,)))}


def h_curso_por_id(params, body):
    c = row('SELECT * FROM cursos WHERE id = ?', (params['id'],))
    if not c:
        raise ApiError(404, 'No encontrado.')
    return 200, {'curso': ser_curso(c)}


def h_estudiantes_de_curso(params, body):
    lista = rows(
        '''SELECT e.*, ut.nombre as tutor_nombre, ur.nombre as representante_nombre
           FROM estudiantes e
           LEFT JOIN usuarios ut ON ut.id = e.tutor_id
           LEFT JOIN usuarios ur ON ur.id = e.representante_id
           WHERE e.curso_id = ?''',
        (params['cursoId'],),
    )
    out = []
    for e in lista:
        d = ser_estudiante(e)
        d['tutorNombre'] = e['tutor_nombre']
        d['representanteNombre'] = e['representante_nombre']
        out.append(d)
    return 200, {'estudiantes': out}


def h_crear_docente(params, body):
    nombre = (body.get('nombre') or '').strip()
    cursos = body.get('cursos') or []
    if not nombre or not isinstance(cursos, list) or len(cursos) == 0:
        raise ApiError(400, 'Ingresa tu nombre y al menos un curso con su clave y asignatura.')
    encontrados = []
    for c in cursos:
        clave = (c.get('clave') or '').strip().upper()
        curso = row('SELECT * FROM cursos WHERE clave_curso = ?', (clave,))
        if not curso:
            raise ApiError(404, f'La clave "{clave}" no corresponde a ningún curso registrado.')
        encontrados.append({'curso': curso, 'asignatura': c.get('asignatura')})
    docente_id = uid('u')
    clave_acceso = codigo6()
    run('INSERT INTO usuarios (id, nombre, rol, clave_acceso) VALUES (?,?,?,?)', (docente_id, nombre, 'docente', clave_acceso))
    for ef in encontrados:
        run('INSERT INTO docente_cursos (id, docente_id, curso_id, asignatura) VALUES (?,?,?,?)', (uid('dc'), docente_id, ef['curso']['id'], ef['asignatura']))
    return 201, {'usuarioId': docente_id, 'claveAcceso': clave_acceso}


def h_login_docente(params, body):
    clave = (body.get('claveAcceso') or '').strip().upper()
    docente = row("SELECT * FROM usuarios WHERE rol='docente' AND clave_acceso = ?", (clave,))
    if not docente:
        raise ApiError(404, 'Clave incorrecta.')
    return 200, {'usuario': ser_usuario(docente)}


def h_cursos_de_docente(params, body):
    lista = rows(
        '''SELECT dc.id as dc_id, dc.asignatura, c.id as curso_id, c.nombre as curso_nombre
           FROM docente_cursos dc JOIN cursos c ON c.id = dc.curso_id
           WHERE dc.docente_id = ?''',
        (params['id'],),
    )
    out = [{'docenteCursoId': r['dc_id'], 'cursoId': r['curso_id'], 'cursoNombre': r['curso_nombre'], 'asignatura': r['asignatura']} for r in lista]
    return 200, {'cursos': out}


def h_agregar_curso_docente(params, body):
    clave = (body.get('clave') or '').strip().upper()
    asignatura = body.get('asignatura')
    if not clave or not asignatura:
        raise ApiError(400, 'Completa la clave y la asignatura.')
    curso = row('SELECT * FROM cursos WHERE clave_curso = ?', (clave,))
    if not curso:
        raise ApiError(404, 'No existe ningún curso con esa clave.')
    ya_existe = row('SELECT * FROM docente_cursos WHERE docente_id=? AND curso_id=? AND asignatura=?', (params['id'], curso['id'], asignatura))
    if ya_existe:
        raise ApiError(409, 'Ya tienes registrada esa asignatura en ese curso.')
    run('INSERT INTO docente_cursos (id, docente_id, curso_id, asignatura) VALUES (?,?,?,?)', (uid('dc'), params['id'], curso['id'], asignatura))
    return 201, {'cursoId': curso['id'], 'cursoNombre': curso['nombre'], 'asignatura': asignatura}


def h_editar_asignatura(params, body):
    asignatura = (body.get('asignatura') or '').strip()
    if not asignatura:
        raise ApiError(400, 'La asignatura no puede quedar vacía.')
    dc = row('SELECT * FROM docente_cursos WHERE id = ? AND docente_id = ?', (params['dcId'], params['id']))
    if not dc:
        raise ApiError(404, 'No encontrado.')
    run('UPDATE docente_cursos SET asignatura = ? WHERE id = ?', (asignatura, params['dcId']))
    return 200, {'ok': True}


def h_quitar_curso_docente(params, body):
    run('DELETE FROM docente_cursos WHERE id = ? AND docente_id = ?', (params['dcId'], params['id']))
    return 200, {'ok': True}


def h_crear_representante(params, body):
    nombre = (body.get('nombre') or '').strip()
    codigo = (body.get('codigoInvitacion') or '').strip().upper()
    if not nombre or not codigo:
        raise ApiError(400, 'Completa tu nombre y el código de invitación.')
    est = row('SELECT * FROM estudiantes WHERE codigo_invitacion = ? AND representante_id IS NULL', (codigo,))
    if not est:
        raise ApiError(404, 'Código inválido o ya utilizado.')
    rep_id = uid('u')
    clave_acceso = codigo6()
    run('INSERT INTO usuarios (id, nombre, rol, clave_acceso) VALUES (?,?,?,?)', (rep_id, nombre, 'representante', clave_acceso))
    run('UPDATE estudiantes SET representante_id = ? WHERE id = ?', (rep_id, est['id']))
    return 201, {'usuarioId': rep_id, 'claveAcceso': clave_acceso, 'estudianteNombre': est['nombre']}


def h_login_representante(params, body):
    clave = (body.get('claveAcceso') or '').strip().upper()
    rep = row("SELECT * FROM usuarios WHERE rol='representante' AND clave_acceso = ?", (clave,))
    if not rep:
        raise ApiError(404, 'Clave incorrecta.')
    return 200, {'usuario': ser_usuario(rep)}


def h_vincular_representante(params, body):
    codigo = (body.get('codigoInvitacion') or '').strip().upper()
    est = row('SELECT * FROM estudiantes WHERE codigo_invitacion = ? AND representante_id IS NULL', (codigo,))
    if not est:
        raise ApiError(404, 'Código inválido o ya utilizado.')
    run('UPDATE estudiantes SET representante_id = ? WHERE id = ?', (params['id'], est['id']))
    return 200, {'estudianteNombre': est['nombre']}


def h_estudiantes_de_representante(params, body):
    lista = rows(
        '''SELECT e.*, ut.nombre as tutor_nombre
           FROM estudiantes e LEFT JOIN usuarios ut ON ut.id = e.tutor_id
           WHERE e.representante_id = ?''',
        (params['id'],),
    )
    out = []
    for e in lista:
        d = ser_estudiante(e)
        d['tutorNombre'] = e['tutor_nombre']
        out.append(d)
    return 200, {'estudiantes': out}


def h_usuario_por_id(params, body):
    u = row('SELECT * FROM usuarios WHERE id = ?', (params['id'],))
    if not u:
        raise ApiError(404, 'No encontrado.')
    return 200, {'usuario': ser_usuario(u)}


def h_crear_mensaje(params, body):
    estudiante_id = body.get('estudianteId')
    remitente_id = body.get('remitenteId')
    remitente_rol = body.get('remitenteRol')
    tipo = body.get('tipo')
    texto = body.get('texto')
    if not all([estudiante_id, remitente_id, remitente_rol, tipo, texto]):
        raise ApiError(400, 'Faltan datos del mensaje.')
    mid = uid('m')
    fecha = datetime.datetime.now(datetime.timezone.utc).isoformat()
    confirmado_tutor = 1 if remitente_rol == 'tutor' else 0
    run(
        '''INSERT INTO mensajes (id, estudiante_id, remitente_id, remitente_rol, tipo, texto, fecha, confirmado_tutor, confirmado_representante)
           VALUES (?,?,?,?,?,?,?,?,0)''',
        (mid, estudiante_id, remitente_id, remitente_rol, tipo, texto, fecha, confirmado_tutor),
    )
    return 201, {'mensaje': ser_mensaje(row('SELECT * FROM mensajes WHERE id = ?', (mid,)))}


def h_confirmar_mensaje(params, body):
    campo = 'confirmado_representante' if body.get('campo') == 'confirmadoRepresentante' else 'confirmado_tutor'
    run(f'UPDATE mensajes SET {campo} = 1 WHERE id = ?', (params['id'],))
    return 200, {'ok': True}


def h_mensajes_tutor(params, body):
    lista = rows(
        '''SELECT m.*, e.nombre as estudiante_nombre, u.nombre as remitente_nombre
           FROM mensajes m
           JOIN estudiantes e ON e.id = m.estudiante_id
           JOIN usuarios u ON u.id = m.remitente_id
           WHERE e.tutor_id = ? ORDER BY m.fecha ASC''',
        (params['id'],),
    )
    out = []
    for m in lista:
        d = ser_mensaje(m)
        d['estudianteNombre'] = m['estudiante_nombre']
        d['remitenteNombre'] = m['remitente_nombre']
        out.append(d)
    return 200, {'mensajes': out}


def h_mensajes_docente(params, body):
    lista = rows(
        '''SELECT m.*, e.nombre as estudiante_nombre FROM mensajes m
           JOIN estudiantes e ON e.id = m.estudiante_id
           WHERE m.remitente_id = ? AND m.remitente_rol = 'docente' ORDER BY m.fecha ASC''',
        (params['id'],),
    )
    out = []
    for m in lista:
        d = ser_mensaje(m)
        d['estudianteNombre'] = m['estudiante_nombre']
        out.append(d)
    return 200, {'mensajes': out}


def h_mensajes_representante(params, body):
    lista = rows(
        '''SELECT m.*, e.nombre as estudiante_nombre, u.nombre as remitente_nombre
           FROM mensajes m
           JOIN estudiantes e ON e.id = m.estudiante_id
           JOIN usuarios u ON u.id = m.remitente_id
           WHERE e.representante_id = ? ORDER BY m.fecha ASC''',
        (params['id'],),
    )
    out = []
    for m in lista:
        d = ser_mensaje(m)
        d['estudianteNombre'] = m['estudiante_nombre']
        d['remitenteNombre'] = m['remitente_nombre']
        out.append(d)
    return 200, {'mensajes': out}


# --- Borrado independiente por usuario ---
def h_borrar_tutor(params, body):
    """Solo el tutor puede borrar su curso, estudiantes, mensajes y representantes vinculados."""
    tutor = row("SELECT * FROM usuarios WHERE id = ? AND rol='tutor'", (params['id'],))
    if not tutor:
        raise ApiError(404, 'Tutor no encontrado.')
    curso = row('SELECT * FROM cursos WHERE tutor_id = ?', (params['id'],))
    if curso:
        reps = rows(
            'SELECT DISTINCT representante_id FROM estudiantes WHERE curso_id = ? AND representante_id IS NOT NULL',
            (curso['id'],),
        )
        run('DELETE FROM mensajes WHERE estudiante_id IN (SELECT id FROM estudiantes WHERE curso_id = ?)', (curso['id'],))
        run('DELETE FROM docente_cursos WHERE curso_id = ?', (curso['id'],))
        run('DELETE FROM estudiantes WHERE curso_id = ?', (curso['id'],))
        run('DELETE FROM cursos WHERE id = ?', (curso['id'],))
        for r in reps:
            rid = r['representante_id']
            otros = row('SELECT id FROM estudiantes WHERE representante_id = ?', (rid,))
            if not otros:
                run("DELETE FROM usuarios WHERE id = ? AND rol = 'representante'", (rid,))
    run('DELETE FROM usuarios WHERE id = ?', (params['id'],))
    return 200, {'ok': True}



def h_borrar_docente(params, body):
    docente = row("SELECT * FROM usuarios WHERE id = ? AND rol='docente'", (params['id'],))
    if not docente:
        raise ApiError(404, 'Docente no encontrado.')
    run("DELETE FROM mensajes WHERE remitente_id = ? AND remitente_rol = 'docente'", (params['id'],))
    run('DELETE FROM docente_cursos WHERE docente_id = ?', (params['id'],))
    run('DELETE FROM usuarios WHERE id = ?', (params['id'],))
    return 200, {'ok': True}


def h_borrar_representante(params, body):
    rep = row("SELECT * FROM usuarios WHERE id = ? AND rol='representante'", (params['id'],))
    if not rep:
        raise ApiError(404, 'Representante no encontrado.')
    hijos = rows('SELECT * FROM estudiantes WHERE representante_id = ?', (params['id'],))
    for h in hijos:
        run('UPDATE estudiantes SET representante_id = NULL, codigo_invitacion = ? WHERE id = ?', (codigo6(), h['id']))
    run('DELETE FROM usuarios WHERE id = ?', (params['id'],))
    return 200, {'ok': True, 'hijosDesvinculados': len(hijos)}


def h_borrar_todo(params, body):
    for stmt in [
        'DELETE FROM mensajes',
        'DELETE FROM dispositivos_push',
        'DELETE FROM estudiantes',
        'DELETE FROM docente_cursos',
        'DELETE FROM cursos',
        'DELETE FROM usuarios',
    ]:
        execute_write(stmt)
    return 200, {'ok': True}



def h_mensaje_a_curso(params, body):
    """Tutor envia el mismo mensaje a todos los representantes del curso."""
    tutor_id = body.get('remitenteId') or params.get('id')
    texto = (body.get('texto') or '').strip()
    tipo = body.get('tipo') or 'alert'
    if not tutor_id or not texto:
        raise ApiError(400, 'Faltan datos del mensaje.')
    tutor = row("SELECT * FROM usuarios WHERE id = ? AND rol='tutor'", (tutor_id,))
    if not tutor:
        raise ApiError(404, 'Tutor no encontrado.')
    curso = row('SELECT * FROM cursos WHERE tutor_id = ?', (tutor_id,))
    if not curso:
        raise ApiError(404, 'No tienes un curso registrado.')
    estudiantes = rows(
        'SELECT * FROM estudiantes WHERE curso_id = ? AND representante_id IS NOT NULL',
        (curso['id'],),
    )
    if not estudiantes:
        raise ApiError(400, 'No hay representantes vinculados en este curso todavía.')
    fecha = datetime.datetime.now(datetime.timezone.utc).isoformat()
    creados = []
    for est in estudiantes:
        mid = uid('msg')
        run(
            'INSERT INTO mensajes (id, estudiante_id, remitente_id, remitente_rol, tipo, texto, fecha, confirmado_tutor, confirmado_representante) VALUES (?,?,?,?,?,?,?,1,0)',
            (mid, est['id'], tutor_id, 'tutor', tipo, texto, fecha),
        )
        creados.append(mid)
    return 201, {'ok': True, 'enviados': len(creados), 'mensajeIds': creados}


def h_salud(params, body):
    return 200, {'ok': True, 'servicio': 'Enlace Escolar API (Python)', 'hora': datetime.datetime.now(datetime.timezone.utc).isoformat()}


# ---------- tabla de rutas ----------
ROUTES = [
    ('POST', r'^/api/tutores$', h_crear_tutor),
    ('POST', r'^/api/tutores/login$', h_login_tutor),
    ('DELETE', r'^/api/tutores/(?P<id>[^/]+)$', h_borrar_tutor),

    ('POST', r'^/api/estudiantes$', h_crear_estudiante),

    ('GET', r'^/api/cursos/(?P<id>[^/]+)$', h_curso_por_id),
    ('GET', r'^/api/cursos/(?P<cursoId>[^/]+)/estudiantes$', h_estudiantes_de_curso),

    ('POST', r'^/api/docentes$', h_crear_docente),
    ('POST', r'^/api/docentes/login$', h_login_docente),
    ('DELETE', r'^/api/docentes/(?P<id>[^/]+)$', h_borrar_docente),
    ('GET', r'^/api/docentes/(?P<id>[^/]+)/cursos$', h_cursos_de_docente),
    ('POST', r'^/api/docentes/(?P<id>[^/]+)/cursos$', h_agregar_curso_docente),
    ('PATCH', r'^/api/docentes/(?P<id>[^/]+)/cursos/(?P<dcId>[^/]+)$', h_editar_asignatura),
    ('DELETE', r'^/api/docentes/(?P<id>[^/]+)/cursos/(?P<dcId>[^/]+)$', h_quitar_curso_docente),
    ('GET', r'^/api/docentes/(?P<id>[^/]+)/mensajes$', h_mensajes_docente),

    ('POST', r'^/api/representantes$', h_crear_representante),
    ('POST', r'^/api/representantes/login$', h_login_representante),
    ('DELETE', r'^/api/representantes/(?P<id>[^/]+)$', h_borrar_representante),
    ('POST', r'^/api/representantes/(?P<id>[^/]+)/vincular$', h_vincular_representante),
    ('GET', r'^/api/representantes/(?P<id>[^/]+)/estudiantes$', h_estudiantes_de_representante),
    ('GET', r'^/api/representantes/(?P<id>[^/]+)/mensajes$', h_mensajes_representante),

    ('GET', r'^/api/usuarios/(?P<id>[^/]+)$', h_usuario_por_id),

    ('POST', r'^/api/mensajes$', h_crear_mensaje),
    ('POST', r'^/api/mensajes/curso$', h_mensaje_a_curso),
    ('PATCH', r'^/api/mensajes/(?P<id>[^/]+)/confirmar$', h_confirmar_mensaje),
    ('GET', r'^/api/tutores/(?P<id>[^/]+)/mensajes$', h_mensajes_tutor),

    ('POST', r'^/api/borrar-todo$', h_borrar_todo),
    ('GET', r'^/api/salud$', h_salud),
]
COMPILED_ROUTES = [(m, re.compile(p), h) for (m, p, h) in ROUTES]


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, data):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,PATCH,DELETE,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _dispatch(self, method):
        path = urlparse(self.path).path
        for m, regex, handler in COMPILED_ROUTES:
            if m != method:
                continue
            match = regex.match(path)
            if not match:
                continue
            params = match.groupdict()
            try:
                body = self._read_body() if method != 'GET' else {}
                status, data = handler(params, body)
                self._send(status, data)
            except ApiError as e:
                self._send(e.status, {'error': e.message})
            except Exception as e:
                self._send(500, {'error': 'Error interno del servidor.', 'detalle': str(e)})
            return
        self._send(404, {'error': 'Ruta no encontrada.'})

    def do_GET(self):
        path = urlparse(self.path).path
        # API routes
        if path.startswith('/api/'):
            self._dispatch('GET')
            return
        # Static frontend files (same service on Render)
        self._serve_static(path)

    def _serve_static(self, path):
        import mimetypes
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')
        if not os.path.isdir(base):
            base = os.path.dirname(os.path.abspath(__file__))
        if path in ('', '/'):
            path = '/index.html'
        # security: no path traversal
        rel = path.lstrip('/').replace('..', '')
        fpath = os.path.join(base, rel)
        if not os.path.isfile(fpath):
            # try deploy folder layout
            alt = os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)
            if os.path.isfile(alt):
                fpath = alt
            else:
                self._send(404, {'error': 'Archivo no encontrado.', 'path': path})
                return
        ctype = mimetypes.guess_type(fpath)[0] or 'application/octet-stream'
        try:
            with open(fpath, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send(500, {'error': str(e)})

    def do_POST(self):
        self._dispatch('POST')

    def do_PATCH(self):
        self._dispatch('PATCH')

    def do_DELETE(self):
        self._dispatch('DELETE')

    def do_OPTIONS(self):
        self._send(204, {})

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Enlace Escolar API (Python) escuchando en puerto {PORT}')
    server.serve_forever()

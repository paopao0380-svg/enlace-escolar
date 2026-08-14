# server.py
# API de Enlace Escolar en Python puro (solo librería estándar: http.server + sqlite3).
# No requiere "pip install" de nada.
import datetime
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from db import get_conn, init_db, uid, codigo6, db_status

PORT = int(os.environ.get('PORT', 3000))

try:
    init_db()
except Exception as e:
    print('init_db error:', e)
con = get_conn()


# ---------- helpers de base de datos ----------
from db import execute, execute_write


def mayus_nombre(s):
    """Normaliza nombres a mayúsculas (español)."""
    return (s or '').strip().upper()


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
        'representanteNombreSugerido': e.get('representante_nombre_sugerido') or '',
        'representanteContacto': e.get('representante_contacto') or '',
    }


def _safe_get(row, key, default=None):
    try:
        if row is None:
            return default
        if isinstance(row, dict):
            return row.get(key, default)
        if hasattr(row, 'get'):
            v = row.get(key)
            if v is not None:
                return v
        try:
            return row[key]
        except Exception:
            return default
    except Exception:
        return default


def ser_mensaje(m):

    return {
        'id': m['id'], 'estudianteId': m['estudiante_id'], 'remitenteId': m['remitente_id'],
        'remitenteRol': m['remitente_rol'], 'tipo': m['tipo'], 'texto': m['texto'], 'fecha': m['fecha'],
        'confirmadoTutor': bool(m['confirmado_tutor']), 'confirmadoRepresentante': bool(m['confirmado_representante']),
        'fotoUrl': _safe_get(m, 'foto_url', '') or '',
    }


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


# ---------- Notificaciones Push (Web Push) ----------
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "BPKuR-cqJQMe-gClAbNXgs4PGye7LJY9xXiAWDIWHZAoSDsKIhx8dFEJ4Q2we8xcNe4UZXEoS-cLbWKtYPjoCY0")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg38VubrV83DXu+RQl
N4LEmfapdmHmOTOFP7ib9VQLGcihRANCAATyrkfnKiUDHvoApQGzV4LODxsnuyyW
PcV4gFgyFh2QKEg7CiIcfHRRCeENsHvMXDXuFGVxKEvnC21irWD46AmN
-----END PRIVATE KEY-----""")
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_MAILTO", "mailto:enlace-escolar@example.com")

try:
    from pywebpush import webpush
    PUSH_AVAILABLE = True
except ImportError:
    PUSH_AVAILABLE = False
    print("AVISO: pywebpush no instalado. Notificaciones push desactivadas. pip install pywebpush")


def guardar_suscripcion(usuario_id, subscription):
    if not usuario_id or not subscription:
        return
    endpoint = (subscription.get("endpoint") or "").strip()
    if not endpoint:
        return
    keys = subscription.get("keys") or {}
    token = json.dumps({
        "endpoint": endpoint,
        "keys": {
            "p256dh": keys.get("p256dh", ""),
            "auth": keys.get("auth", ""),
        },
    }, ensure_ascii=False)
    run("DELETE FROM dispositivos_push WHERE usuario_id = ?", (usuario_id,))
    run(
        "INSERT INTO dispositivos_push (id, usuario_id, token) VALUES (?,?,?)",
        (uid("push"), usuario_id, token),
    )


def enviar_push_a_usuario(usuario_id, titulo, cuerpo, data=None):
    if not usuario_id:
        return
    if not PUSH_AVAILABLE:
        print("push skip: pywebpush no disponible")
        return
    lista = rows("SELECT * FROM dispositivos_push WHERE usuario_id = ?", (usuario_id,))
    if not lista:
        print("push skip: sin dispositivos para", usuario_id)
        return
    print("push enviando a", usuario_id, "dispositivos", len(lista))
    payload = json.dumps({
        "title": titulo or "Enlace Escolar",
        "body": (cuerpo or "Tienes un mensaje nuevo")[:180],
        "data": data or {},
    }, ensure_ascii=False)
    for d in lista:
        try:
            sub = json.loads(d["token"])
        except Exception:
            continue
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIMS_EMAIL},
            )
        except Exception as e:
            err = str(e)
            if "410" in err or "404" in err:
                try:
                    run("DELETE FROM dispositivos_push WHERE id = ?", (d["id"],))
                except Exception:
                    pass
            print("push error:", err)


def notificar_destinatarios_mensaje(estudiante_id, remitente_id, remitente_rol, texto):
    est = row("SELECT * FROM estudiantes WHERE id = ?", (estudiante_id,))
    if not est:
        return
    preview = (texto or "")[:120]
    remitente = row("SELECT * FROM usuarios WHERE id = ?", (remitente_id,))
    nombre_rem = (remitente or {}).get("nombre") or remitente_rol or "Usuario"
    targets = []
    if remitente_rol == "tutor":
        if est.get("representante_id"):
            targets.append((est["representante_id"], "Mensaje del Tutor " + nombre_rem, preview))
    elif remitente_rol == "docente":
        if est.get("representante_id"):
            targets.append((est["representante_id"], "Mensaje del Docente " + nombre_rem, preview))
        curso = row("SELECT * FROM cursos WHERE id = ?", (est.get("curso_id"),))
        if curso and curso.get("tutor_id") and curso["tutor_id"] != remitente_id:
            targets.append((curso["tutor_id"], "Mensaje del Docente " + nombre_rem, preview))
    elif remitente_rol == "representante":
        curso = row("SELECT * FROM cursos WHERE id = ?", (est.get("curso_id"),))
        if curso and curso.get("tutor_id"):
            targets.append((curso["tutor_id"], "Mensaje del Representante " + nombre_rem, preview))
    else:
        if est.get("representante_id"):
            targets.append((est["representante_id"], "Mensaje de " + nombre_rem, preview))
        curso = row("SELECT * FROM cursos WHERE id = ?", (est.get("curso_id"),))
        if curso and curso.get("tutor_id"):
            targets.append((curso["tutor_id"], "Mensaje de " + nombre_rem, preview))
    for uid_dest, title, body in targets:
        if uid_dest and uid_dest != remitente_id:
            enviar_push_a_usuario(uid_dest, title, body, {"tipo": "mensaje"})


def h_push_vapid_public(params, body):
    return 200, {"publicKey": VAPID_PUBLIC_KEY, "pushAvailable": PUSH_AVAILABLE}


def h_push_subscribe(params, body):
    usuario_id = body.get("usuarioId") or body.get("usuario_id")
    subscription = body.get("subscription")
    if not subscription and body.get("endpoint"):
        subscription = body
    if not usuario_id:
        raise ApiError(400, "Falta usuarioId")
    if not subscription or not subscription.get("endpoint"):
        raise ApiError(400, "Falta subscription")
    u = row("SELECT id FROM usuarios WHERE id = ?", (usuario_id,))
    if not u:
        raise ApiError(404, "Usuario no encontrado")
    guardar_suscripcion(usuario_id, subscription)
    return 200, {"ok": True}



def h_push_test(params, body):
    """Envía una notificación de prueba al usuario indicado."""
    usuario_id = body.get('usuarioId') or body.get('usuario_id')
    if not usuario_id:
        raise ApiError(400, 'Falta usuarioId')
    lista = rows('SELECT * FROM dispositivos_push WHERE usuario_id = ?', (usuario_id,))
    if not lista:
        return 200, {'ok': False, 'error': 'No hay dispositivo registrado. Activa las notificaciones en la App primero.', 'pushAvailable': PUSH_AVAILABLE}
    enviar_push_a_usuario(usuario_id, 'Enlace Escolar', 'Prueba de aviso: si ves esto, las notificaciones funcionan.', {'tipo': 'test'})
    return 200, {'ok': True, 'dispositivos': len(lista), 'pushAvailable': PUSH_AVAILABLE}

def h_push_unsubscribe(params, body):
    usuario_id = body.get("usuarioId") or body.get("usuario_id")
    if usuario_id:
        run("DELETE FROM dispositivos_push WHERE usuario_id = ?", (usuario_id,))
    return 200, {"ok": True}



# ---------- handlers ----------
def h_crear_tutor(params, body):
    nombre = mayus_nombre(body.get('nombre'))
    curso_nombre = mayus_nombre(body.get('cursoNombre'))
    if not nombre or not curso_nombre:
        raise ApiError(400, 'Completa tu nombre y el nombre del curso.')
    clave = codigo6()
    curso_id = uid('curso')
    tutor_id = uid('u')
    run('INSERT INTO cursos (id, nombre, clave_curso, tutor_id) VALUES (?,?,?,?)', (curso_id, curso_nombre, clave, tutor_id))
    run('INSERT INTO usuarios (id, nombre, rol, curso_id) VALUES (?,?,?,?)', (tutor_id, nombre, 'tutor', curso_id))
    return 201, {'usuarioId': tutor_id, 'cursoId': curso_id, 'claveCurso': clave, 'cursoNombre': curso_nombre}


def h_login_tutor(params, body):
    """Tutor entra con su clave personal.
    La clave del curso NO se modifica y sigue sirviendo a los Docentes.
    Si el tutor aún no cambió su clave, puede entrar con la clave del curso.
    """
    clave = (body.get('claveCurso') or body.get('claveAcceso') or '').strip().upper()
    if not clave:
        raise ApiError(400, 'Ingresa tu clave.')

    # 1) Clave personal del tutor (prioridad)
    tutor = row("SELECT * FROM usuarios WHERE rol = 'tutor' AND UPPER(COALESCE(clave_acceso,'')) = ?", (clave,))
    if tutor:
        curso = row('SELECT * FROM cursos WHERE tutor_id = ?', (tutor['id'],))
        if not curso:
            raise ApiError(404, 'No se encontró el curso del tutor.')
        return 200, {'usuario': ser_usuario(tutor), 'curso': ser_curso(curso)}

    # 2) Clave del curso: solo si el tutor aún no tiene clave personal distinta
    curso = row('SELECT * FROM cursos WHERE clave_curso = ?', (clave,))
    if not curso:
        raise ApiError(404, 'Clave incorrecta.')
    tutor = row('SELECT * FROM usuarios WHERE id = ?', (curso['tutor_id'],))
    if not tutor:
        raise ApiError(404, 'Tutor no encontrado.')

    personal = (tutor.get('clave_acceso') or '').strip().upper()
    curso_clave = (curso.get('clave_curso') or '').strip().upper()

    # Si ya cambió su clave personal, debe usar esa (no la del curso)
    if personal and personal != curso_clave:
        raise ApiError(403, 'Usa tu clave personal de acceso (la que configuraste). La clave del curso es solo para Docentes.')

    if not personal:
        run('UPDATE usuarios SET clave_acceso = ? WHERE id = ?', (clave, tutor['id']))
        tutor = row('SELECT * FROM usuarios WHERE id = ?', (tutor['id'],))

    return 200, {'usuario': ser_usuario(tutor), 'curso': ser_curso(curso)}


def h_crear_estudiante(params, body):
    tutor_id = body.get('tutorId')
    curso_id = body.get('cursoId')
    nombre = mayus_nombre(body.get('nombre'))
    if not tutor_id or not curso_id or not nombre:
        raise ApiError(400, 'Ingresa el nombre del estudiante.')
    eid = uid('e')
    codigo = codigo6()
    run(
        "INSERT INTO estudiantes (id, nombre, curso_id, tutor_id, representante_id, representante_nombre_sugerido, representante_contacto, codigo_invitacion) VALUES (?,?,?,?,NULL,'','',?)",
        (eid, nombre, curso_id, tutor_id, codigo),
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



def h_editar_estudiante(params, body):
    eid = params['id']
    est = row('SELECT * FROM estudiantes WHERE id = ?', (eid,))
    if not est:
        raise ApiError(404, 'Estudiante no encontrado.')
    tutor_id = body.get('tutorId')
    if not tutor_id or est['tutor_id'] != tutor_id:
        raise ApiError(403, 'Solo el Tutor de este curso puede editar al estudiante.')
    nombre = mayus_nombre(body.get('nombre'))
    if not nombre:
        raise ApiError(400, 'El nombre del estudiante es obligatorio.')
    if 'representanteContacto' in body:
        contacto = ''.join(ch for ch in str(body.get('representanteContacto') or '') if ch.isdigit())[:10]
        run("UPDATE estudiantes SET nombre = ?, representante_contacto = ? WHERE id = ?", (nombre, contacto, eid))
    else:
        run("UPDATE estudiantes SET nombre = ? WHERE id = ?", (nombre, eid))
    est2 = row('SELECT * FROM estudiantes WHERE id = ?', (eid,))
    d = ser_estudiante(est2)
    d['representanteContacto'] = est2.get('representante_contacto') or ''
    return 200, {'estudiante': d}


def h_borrar_estudiante(params, body):
    eid = params['id']
    est = row('SELECT * FROM estudiantes WHERE id = ?', (eid,))
    if not est:
        raise ApiError(404, 'Estudiante no encontrado.')
    body = body or {}
    tutor_id = body.get('tutorId')
    if not tutor_id or est['tutor_id'] != tutor_id:
        raise ApiError(403, 'Solo el Tutor de este curso puede eliminar al estudiante.')
    run('DELETE FROM mensajes WHERE estudiante_id = ?', (eid,))
    run('DELETE FROM estudiantes WHERE id = ?', (eid,))
    return 200, {'ok': True, 'eliminado': eid}


def h_crear_docente(params, body):
    nombre = mayus_nombre(body.get('nombre'))
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
    nombre = mayus_nombre(body.get('nombre'))
    codigo = (body.get('codigoInvitacion') or '').strip().upper()
    contacto = (body.get('contacto') or body.get('celular') or '').strip()
    if not nombre or not codigo or not contacto:
        raise ApiError(400, 'Completa tu nombre, celular y el código de invitación.')
    est = row('SELECT * FROM estudiantes WHERE codigo_invitacion = ? AND representante_id IS NULL', (codigo,))
    if not est:
        raise ApiError(404, 'Código inválido o ya utilizado.')
    rep_id = uid('u')
    clave_acceso = codigo6()
    run('INSERT INTO usuarios (id, nombre, rol, clave_acceso) VALUES (?,?,?,?)', (rep_id, nombre, 'representante', clave_acceso))
    run(
        'UPDATE estudiantes SET representante_id = ?, representante_nombre_sugerido = ?, representante_contacto = ? WHERE id = ?',
        (rep_id, nombre, contacto, est['id']),
    )
    return 201, {'usuarioId': rep_id, 'claveAcceso': clave_acceso, 'estudianteNombre': est['nombre']}


def h_login_representante(params, body):
    clave = (body.get('claveAcceso') or '').strip().upper()
    rep = row("SELECT * FROM usuarios WHERE rol='representante' AND clave_acceso = ?", (clave,))
    if not rep:
        raise ApiError(404, 'Clave incorrecta.')
    return 200, {'usuario': ser_usuario(rep)}


def h_vincular_representante(params, body):
    codigo = (body.get('codigoInvitacion') or '').strip().upper()
    contacto = (body.get('contacto') or body.get('celular') or '').strip()
    est = row('SELECT * FROM estudiantes WHERE codigo_invitacion = ? AND representante_id IS NULL', (codigo,))
    if not est:
        raise ApiError(404, 'Código inválido o ya utilizado.')
    u = row('SELECT * FROM usuarios WHERE id = ?', (params['id'],))
    nombre = (u['nombre'] if u else '') or ''
    run(
        'UPDATE estudiantes SET representante_id = ?, representante_nombre_sugerido = ?, representante_contacto = ? WHERE id = ?',
        (params['id'], nombre, contacto, est['id']),
    )
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



def h_cambiar_clave(params, body):
    uid_user = params['id']
    u = row('SELECT * FROM usuarios WHERE id = ?', (uid_user,))
    if not u:
        raise ApiError(404, 'Usuario no encontrado.')
    actual = (body.get('claveActual') or '').strip().upper()
    nueva = (body.get('claveNueva') or '').strip().upper()
    if not actual or not nueva:
        raise ApiError(400, 'Ingresa la clave actual y la nueva.')

    guardada = (u.get('clave_acceso') or '').strip().upper()
    ok_actual = bool(guardada) and actual == guardada

    # Tutor: aceptar también la clave del curso como clave actual (sin modificarla)
    if not ok_actual and u.get('rol') == 'tutor':
        curso = row('SELECT * FROM cursos WHERE tutor_id = ?', (uid_user,))
        if curso and actual == (curso.get('clave_curso') or '').strip().upper():
            ok_actual = True

    if not ok_actual:
        raise ApiError(403, 'La clave actual no es correcta. Usa la clave con la que ingresas a la App.')

    if len(nueva) < 4:
        raise ApiError(400, 'La nueva clave debe tener al menos 4 caracteres.')
    if len(nueva) > 20:
        raise ApiError(400, 'La nueva clave no puede superar 20 caracteres.')
    if nueva == actual:
        raise ApiError(400, 'La nueva clave debe ser distinta a la actual.')

    permitidos = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-')
    if not all(c in permitidos for c in nueva):
        raise ApiError(400, 'La clave solo puede tener letras, números y . _ -')

    # ¿Alguien más ya usa esa clave?
    try:
        otros = rows('SELECT id, clave_acceso FROM usuarios WHERE id != ?', (uid_user,))
    except Exception:
        otros = []
    for o in otros:
        if (o.get('clave_acceso') or '').strip().upper() == nueva:
            raise ApiError(409, 'Esa clave ya está en uso. Elige otra.')

    try:
        run('UPDATE usuarios SET clave_acceso = ? WHERE id = ?', (nueva, uid_user))
    except Exception as e:
        msg = str(e).lower()
        if 'unique' in msg or 'constraint' in msg:
            raise ApiError(409, 'Esa clave ya está en uso. Elige otra.')
        raise ApiError(500, 'No se pudo guardar la nueva clave. Intenta de nuevo.')

    return 200, {'ok': True, 'claveAcceso': nueva}


def h_usuario_por_id(params, body):
    u = row('SELECT * FROM usuarios WHERE id = ?', (params['id'],))
    if not u:
        raise ApiError(404, 'No encontrado.')
    return 200, {'usuario': ser_usuario(u)}


def h_crear_mensaje(params, body):
    try:
        from db import ensure_foto_columns
        ensure_foto_columns()
    except Exception:
        pass
    estudiante_id = body.get('estudianteId')
    remitente_id = body.get('remitenteId')
    remitente_rol = body.get('remitenteRol')
    tipo = body.get('tipo')
    texto = (body.get('texto') or '').strip()
    foto_url = (body.get('fotoUrl') or body.get('foto_url') or '').strip()
    if not all([estudiante_id, remitente_id, remitente_rol, tipo]):
        raise ApiError(400, 'Faltan datos del mensaje.')
    if not texto and not foto_url:
        raise ApiError(400, 'Escribe un mensaje o adjunta una foto.')
    if not texto:
        texto = '(Foto)'
    mid = uid('m')
    fecha = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if remitente_rol not in ('tutor', 'docente', 'representante'):
        raise ApiError(400, 'Rol de remitente no válido.')
    confirmado_tutor = 1 if remitente_rol == 'tutor' else 0
    confirmado_rep = 1 if remitente_rol == 'representante' else 0
    try:
        run(
            "INSERT INTO mensajes (id, estudiante_id, remitente_id, remitente_rol, tipo, texto, fecha, confirmado_tutor, confirmado_representante, foto_url) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mid, estudiante_id, remitente_id, remitente_rol, tipo, texto, fecha, confirmado_tutor, confirmado_rep, foto_url or None),
        )
    except Exception:
        run(
            "INSERT INTO mensajes (id, estudiante_id, remitente_id, remitente_rol, tipo, texto, fecha, confirmado_tutor, confirmado_representante) VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, estudiante_id, remitente_id, remitente_rol, tipo, texto, fecha, confirmado_tutor, confirmado_rep),
        )
    try:
        notificar_destinatarios_mensaje(estudiante_id, remitente_id, remitente_rol, texto if texto != '(Foto)' else 'Foto')
    except Exception as e:
        print('notify error:', e)
    return 201, {'mensaje': ser_mensaje(row('SELECT * FROM mensajes WHERE id = ?', (mid,)))}


def h_confirmar_mensaje(params, body):
    campo = 'confirmado_representante' if body.get('campo') == 'confirmadoRepresentante' else 'confirmado_tutor'
    run(f'UPDATE mensajes SET {campo} = 1 WHERE id = ?', (params['id'],))
    return 200, {'ok': True}


def h_mensajes_tutor(params, body):
    lista = rows(
        "SELECT m.*, e.nombre as estudiante_nombre, e.representante_contacto as rep_contacto, "
        "u.nombre as remitente_nombre, "
        "(SELECT dc.asignatura FROM docente_cursos dc "
        " WHERE dc.docente_id = m.remitente_id AND dc.curso_id = e.curso_id LIMIT 1) as asignatura "
        "FROM mensajes m "
        "JOIN estudiantes e ON e.id = m.estudiante_id "
        "JOIN usuarios u ON u.id = m.remitente_id "
        "WHERE e.tutor_id = ? ORDER BY m.fecha ASC",
        (params['id'],),
    )
    out = []
    for m in lista:
        d = ser_mensaje(m)
        d['estudianteNombre'] = m['estudiante_nombre']
        d['remitenteNombre'] = m['remitente_nombre']
        d['remitenteContacto'] = m['rep_contacto'] if m.get('rep_contacto') else ''
        d['asignatura'] = m.get('asignatura') or ''
        out.append(d)
    return 200, {'mensajes': out}



def h_mensajes_docente(params, body):
    lista = rows(
        "SELECT m.*, e.nombre as estudiante_nombre, "
        "(SELECT dc.asignatura FROM docente_cursos dc "
        " WHERE dc.docente_id = m.remitente_id AND dc.curso_id = e.curso_id LIMIT 1) as asignatura "
        "FROM mensajes m "
        "JOIN estudiantes e ON e.id = m.estudiante_id "
        "WHERE m.remitente_id = ? AND m.remitente_rol = 'docente' ORDER BY m.fecha ASC",
        (params['id'],),
    )
    out = []
    for m in lista:
        d = ser_mensaje(m)
        d['estudianteNombre'] = m['estudiante_nombre']
        d['asignatura'] = m.get('asignatura') or ''
        out.append(d)
    return 200, {'mensajes': out}



def h_mensajes_representante(params, body):
    lista = rows(
        "SELECT m.*, e.nombre as estudiante_nombre, e.representante_contacto as rep_contacto, "
        "u.nombre as remitente_nombre, "
        "(SELECT dc.asignatura FROM docente_cursos dc "
        " WHERE dc.docente_id = m.remitente_id AND dc.curso_id = e.curso_id LIMIT 1) as asignatura "
        "FROM mensajes m "
        "JOIN estudiantes e ON e.id = m.estudiante_id "
        "JOIN usuarios u ON u.id = m.remitente_id "
        "WHERE e.representante_id = ? ORDER BY m.fecha ASC",
        (params['id'],),
    )
    out = []
    for m in lista:
        d = ser_mensaje(m)
        d['estudianteNombre'] = m['estudiante_nombre']
        d['remitenteNombre'] = m['remitente_nombre']
        d['remitenteContacto'] = m['rep_contacto'] if m.get('rep_contacto') else ''
        d['asignatura'] = m.get('asignatura') or ''
        out.append(d)
    return 200, {'mensajes': out}



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
    require_admin(body)
    for stmt in [
        'DELETE FROM mensajes_institucionales',
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
    try:
        _tutor = row("SELECT * FROM usuarios WHERE id = ?", (tutor_id,))
        _nombre_t = (_tutor or {}).get('nombre') or 'Tutor'
        _preview = (texto or '')[:120]
        for _e in estudiantes:
            _rid = _e.get('representante_id')
            if _rid:
                enviar_push_a_usuario(_rid, 'Mensaje del Tutor ' + _nombre_t, _preview, {'tipo': 'mensaje'})
    except Exception as _ex:
        print('notify curso error:', _ex)
    return 201, {'ok': True, 'enviados': len(creados), 'mensajeIds': creados}





def h_mensajes_docente_enviados_tutor(params, body):
    """Mensajes que el docente envió al tutor (tabla institucional)."""
    did = params['id']
    lista = rows(
        "SELECT * FROM mensajes_institucionales WHERE remitente_id = ? AND remitente_rol = 'docente' ORDER BY fecha DESC",
        (did,),
    )
    out = []
    for m in lista:
        dest_nombre = ''
        if m.get('destino_id'):
            u = row('SELECT nombre FROM usuarios WHERE id = ?', (m['destino_id'],))
            dest_nombre = u['nombre'] if u else ''
        d = ser_msg_inst(m, None)
        d['destinoNombre'] = dest_nombre
        out.append(d)
    return 200, {'mensajes': out}

def h_mensaje_docente_a_tutor(params, body):
    """Docente envía mensaje al Tutor del curso y, el mismo, a los representantes del curso."""
    docente_id = body.get('docenteId')
    curso_id = body.get('cursoId')
    tipo = (body.get('tipo') or 'info').strip()
    texto = (body.get('texto') or '').strip()
    foto_url = (body.get('fotoUrl') or body.get('foto_url') or '').strip()
    if not docente_id or not curso_id:
        raise ApiError(400, 'Completa el mensaje y el curso.')
    if not texto and not foto_url:
        raise ApiError(400, 'Escribe un mensaje o adjunta una foto.')
    if not texto:
        texto = '(Foto)'
    doc = row('SELECT * FROM usuarios WHERE id = ? AND rol = ?', (docente_id, 'docente'))
    if not doc:
        raise ApiError(403, 'Solo un docente puede enviar este mensaje.')
    vinculo = row(
        'SELECT * FROM docente_cursos WHERE docente_id = ? AND curso_id = ?',
        (docente_id, curso_id),
    )
    if not vinculo:
        raise ApiError(403, 'No estás vinculado a este curso.')
    curso = row('SELECT * FROM cursos WHERE id = ?', (curso_id,))
    if not curso:
        raise ApiError(404, 'Curso no encontrado.')
    tutor_id = curso['tutor_id']
    if not tutor_id:
        raise ApiError(400, 'Este curso no tiene tutor asignado.')
    asignatura = vinculo.get('asignatura') or ''
    texto_final = texto
    if asignatura:
        texto_final = '[' + str(asignatura) + '] ' + texto
    mid = uid('mi')
    fecha = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        run(
            "INSERT INTO mensajes_institucionales (id, remitente_id, remitente_rol, destino_tipo, destino_id, tipo, texto, fecha, foto_url) VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, docente_id, 'docente', 'tutor', tutor_id, tipo, texto_final, fecha, foto_url or None),
        )
    except Exception:
        run(
            "INSERT INTO mensajes_institucionales (id, remitente_id, remitente_rol, destino_tipo, destino_id, tipo, texto, fecha) VALUES (?,?,?,?,?,?,?,?)",
            (mid, docente_id, 'docente', 'tutor', tutor_id, tipo, texto_final, fecha),
        )
    try:
        nombre_d = doc.get('nombre') or 'Docente'
        enviar_push_a_usuario(tutor_id, 'Mensaje del Docente ' + nombre_d, (texto or '')[:120], {'tipo': 'mensaje'})
    except Exception as e:
        print('push tutor:', e)

    # Mismo mensaje a todos los representantes del curso
    enviados_rep = 0
    try:
        estudiantes = rows(
            "SELECT * FROM estudiantes WHERE curso_id = ? AND representante_id IS NOT NULL",
            (curso_id,),
        )
        for est in estudiantes:
            midr = uid('m')
            try:
                run(
                    "INSERT INTO mensajes (id, estudiante_id, remitente_id, remitente_rol, tipo, texto, fecha, confirmado_tutor, confirmado_representante, foto_url) VALUES (?,?,?,?,?,?,?,1,0,?)",
                    (midr, est['id'], docente_id, 'docente', tipo, texto_final, fecha, foto_url or None),
                )
            except Exception:
                run(
                    "INSERT INTO mensajes (id, estudiante_id, remitente_id, remitente_rol, tipo, texto, fecha, confirmado_tutor, confirmado_representante) VALUES (?,?,?,?,?,?,?,1,0)",
                    (midr, est['id'], docente_id, 'docente', tipo, texto_final, fecha),
                )
            enviados_rep += 1
            try:
                enviar_push_a_usuario(est['representante_id'], 'Mensaje del Docente ' + (doc.get('nombre') or ''), (texto or '')[:120], {'tipo': 'mensaje'})
            except Exception:
                pass
    except Exception as e:
        print('copia reps error:', e)

    return 201, {'ok': True, 'mensajeId': mid, 'enviadosRepresentantes': enviados_rep}



def h_crear_autoridad(params, body):
    # Solo desde configuración (adminKey)
    require_admin(body)
    nombre = mayus_nombre(body.get('nombre'))
    rol = (body.get('rol') or '').strip().lower()
    if rol not in ('rector', 'inspector', 'dece'):
        raise ApiError(400, 'Rol inválido. Use rector, inspector o dece.')
    if not nombre:
        raise ApiError(400, 'Ingresa tu nombre.')
    clave = codigo6()
    aid = uid('aut')
    run('INSERT INTO usuarios (id, nombre, rol, clave_acceso) VALUES (?,?,?,?)', (aid, nombre, rol, clave))
    return 201, {'usuarioId': aid, 'claveAcceso': clave, 'rol': rol}


def h_login_autoridad(params, body):
    clave = (body.get('claveAcceso') or '').strip().upper()
    if not clave:
        raise ApiError(400, 'Ingresa tu clave de acceso.')
    u = row("SELECT * FROM usuarios WHERE clave_acceso = ? AND rol IN ('rector','inspector','dece')", (clave,))
    if not u:
        raise ApiError(401, 'Clave incorrecta o usuario no registrado.')
    return 200, {'usuario': ser_usuario(u)}


def h_lista_tutores(params, body):
    lista = rows("SELECT id, nombre, curso_id FROM usuarios WHERE rol='tutor' ORDER BY nombre")
    out = []
    for u in lista:
        c = row('SELECT nombre, clave_curso FROM cursos WHERE tutor_id = ?', (u['id'],))
        out.append({
            'id': u['id'],
            'nombre': u['nombre'],
            'cursoNombre': c['nombre'] if c else '',
            'claveCurso': c['clave_curso'] if c else '',
        })
    return 200, {'tutores': out}


def h_lista_docentes(params, body):
    lista = rows("SELECT id, nombre FROM usuarios WHERE rol='docente' ORDER BY nombre")
    out = []
    for u in lista:
        cursos = rows(
            "SELECT c.nombre AS nombre, dc.asignatura AS asignatura FROM docente_cursos dc JOIN cursos c ON c.id = dc.curso_id WHERE dc.docente_id = ?",
            (u['id'],),
        )
        out.append({
            'id': u['id'],
            'nombre': u['nombre'],
            'cursos': [{'nombre': x['nombre'], 'asignatura': x['asignatura']} for x in cursos],
        })
    return 200, {'docentes': out}


def ser_msg_inst(m, remitente_nombre=None):
    return {
        'id': m['id'],
        'remitenteId': m['remitente_id'],
        'remitenteRol': m['remitente_rol'],
        'remitenteNombre': remitente_nombre or '',
        'destinoTipo': m['destino_tipo'],
        'destinoId': m['destino_id'],
        'tipo': m['tipo'],
        'texto': m['texto'],
        'fecha': m['fecha'],
        'fotoUrl': _safe_get(m, 'foto_url', '') or '',
    }


def h_crear_mensaje_institucional(params, body):
    remitente_id = body.get('remitenteId')
    destino_tipo = (body.get('destinoTipo') or '').strip()
    destino_id = body.get('destinoId')
    tipo = (body.get('tipo') or 'info').strip()
    texto = (body.get('texto') or '').strip()
    if not remitente_id or not destino_tipo or not texto:
        raise ApiError(400, 'Completa destino y mensaje.')
    if destino_tipo not in ('todos_docentes', 'todos_tutores', 'tutor', 'docente'):
        raise ApiError(400, 'Destino no válido.')
    if destino_tipo in ('tutor', 'docente') and not destino_id:
        raise ApiError(400, 'Selecciona el destinatario.')
    rem = row('SELECT * FROM usuarios WHERE id = ?', (remitente_id,))
    if not rem or rem['rol'] not in ('rector', 'inspector', 'dece'):
        raise ApiError(403, 'Solo Rector o Inspector pueden enviar este tipo de mensaje.')
    mid = uid('mi')
    fecha = datetime.datetime.now(datetime.timezone.utc).isoformat()
    run(
        "INSERT INTO mensajes_institucionales (id, remitente_id, remitente_rol, destino_tipo, destino_id, tipo, texto, fecha) VALUES (?,?,?,?,?,?,?,?)",
        (mid, remitente_id, rem['rol'], destino_tipo, destino_id, tipo, texto, fecha),
    )
    return 201, {'ok': True, 'mensajeId': mid}


def h_mensajes_enviados_autoridad(params, body):
    aid = params['id']
    lista = rows(
        'SELECT * FROM mensajes_institucionales WHERE remitente_id = ? ORDER BY fecha DESC',
        (aid,),
    )
    out = []
    for m in lista:
        d = ser_msg_inst(m)
        dest_nombre = ''
        if m.get('destino_id') and m.get('destino_tipo') in ('tutor', 'docente'):
            u = row('SELECT nombre FROM usuarios WHERE id = ?', (m['destino_id'],))
            dest_nombre = u['nombre'] if u else ''
        d['destinoNombre'] = dest_nombre
        out.append(d)
    return 200, {'mensajes': out}


def h_mensajes_institucionales_recibidos(params, body):
    uid_user = params['id']
    u = row('SELECT * FROM usuarios WHERE id = ?', (uid_user,))
    if not u:
        raise ApiError(404, 'Usuario no encontrado.')
    rol = u['rol']
    if rol == 'tutor':
        lista = rows(
            "SELECT * FROM mensajes_institucionales WHERE destino_tipo = 'todos_tutores' OR (destino_tipo = 'tutor' AND destino_id = ?) ORDER BY fecha DESC",
            (uid_user,),
        )
    elif rol == 'docente':
        lista = rows(
            "SELECT * FROM mensajes_institucionales WHERE destino_tipo = 'todos_docentes' OR (destino_tipo = 'docente' AND destino_id = ?) ORDER BY fecha DESC",
            (uid_user,),
        )
    else:
        lista = []
    out = []
    for m in lista:
        rem = row('SELECT nombre FROM usuarios WHERE id = ?', (m['remitente_id'],))
        out.append(ser_msg_inst(m, rem['nombre'] if rem else ''))
    return 200, {'mensajes': out}



def h_docentes_de_curso(params, body):
    """Docentes vinculados a un curso (para que el Tutor les escriba)."""
    curso_id = params['id']
    curso = row('SELECT * FROM cursos WHERE id = ?', (curso_id,))
    if not curso:
        raise ApiError(404, 'Curso no encontrado.')
    lista = rows(
        "SELECT DISTINCT u.id, u.nombre, dc.asignatura FROM docente_cursos dc "
        "JOIN usuarios u ON u.id = dc.docente_id "
        "WHERE dc.curso_id = ? AND u.rol = 'docente' ORDER BY u.nombre ASC",
        (curso_id,),
    )
    out = []
    for r in lista:
        out.append({
            'id': r['id'],
            'nombre': r['nombre'],
            'asignatura': r.get('asignatura') or '',
        })
    return 200, {'docentes': out}



def h_mensaje_tutor_a_todos_docentes(params, body):
    """Tutor envía el mismo mensaje a todos los docentes de su curso."""
    tutor_id = body.get('tutorId')
    tipo = (body.get('tipo') or 'info').strip()
    texto = (body.get('texto') or '').strip()
    foto_url = (body.get('fotoUrl') or body.get('foto_url') or '').strip()
    if not tutor_id:
        raise ApiError(400, 'Faltan datos.')
    if not texto and not foto_url:
        raise ApiError(400, 'Escribe un mensaje o adjunta una foto.')
    if not texto:
        texto = '(Foto)'
    tutor = row("SELECT * FROM usuarios WHERE id = ? AND rol = 'tutor'", (tutor_id,))
    if not tutor:
        raise ApiError(403, 'Solo el Tutor puede enviar este mensaje.')
    curso = row('SELECT * FROM cursos WHERE tutor_id = ?', (tutor_id,))
    if not curso:
        raise ApiError(404, 'No tienes curso asignado.')
    docs = rows(
        "SELECT DISTINCT u.id, u.nombre FROM docente_cursos dc "
        "JOIN usuarios u ON u.id = dc.docente_id "
        "WHERE dc.curso_id = ? AND u.rol = 'docente'",
        (curso['id'],),
    )
    if not docs:
        raise ApiError(400, 'No hay docentes vinculados a tu curso.')
    fecha = datetime.datetime.now(datetime.timezone.utc).isoformat()
    ids = []
    for d in docs:
        mid = uid('mi')
        try:
            run(
                "INSERT INTO mensajes_institucionales (id, remitente_id, remitente_rol, destino_tipo, destino_id, tipo, texto, fecha, foto_url) VALUES (?,?,?,?,?,?,?,?,?)",
                (mid, tutor_id, 'tutor', 'docente', d['id'], tipo, texto, fecha, foto_url or None),
            )
        except Exception:
            run(
                "INSERT INTO mensajes_institucionales (id, remitente_id, remitente_rol, destino_tipo, destino_id, tipo, texto, fecha) VALUES (?,?,?,?,?,?,?,?)",
                (mid, tutor_id, 'tutor', 'docente', d['id'], tipo, texto, fecha),
            )
        ids.append(mid)
        try:
            enviar_push_a_usuario(d['id'], 'Mensaje del Tutor ' + (tutor.get('nombre') or ''), (texto or '')[:120], {'tipo': 'mensaje'})
        except Exception:
            pass
    return 201, {'ok': True, 'enviados': len(ids), 'mensajeIds': ids}


def h_mensaje_tutor_a_docente(params, body):
    """Tutor envía mensaje a un docente de su curso."""
    tutor_id = body.get('tutorId')
    docente_id = body.get('docenteId')
    tipo = (body.get('tipo') or 'info').strip()
    texto = (body.get('texto') or '').strip()
    foto_url = (body.get('fotoUrl') or body.get('foto_url') or '').strip()
    if not tutor_id or not docente_id:
        raise ApiError(400, 'Faltan datos.')
    if not texto and not foto_url:
        raise ApiError(400, 'Escribe un mensaje o adjunta una foto.')
    if not texto:
        texto = '(Foto)'
    tutor = row("SELECT * FROM usuarios WHERE id = ? AND rol = 'tutor'", (tutor_id,))
    if not tutor:
        raise ApiError(403, 'Solo el Tutor puede enviar este mensaje.')
    curso = row('SELECT * FROM cursos WHERE tutor_id = ?', (tutor_id,))
    if not curso:
        raise ApiError(404, 'No tienes curso asignado.')
    vinculo = row(
        'SELECT * FROM docente_cursos WHERE docente_id = ? AND curso_id = ?',
        (docente_id, curso['id']),
    )
    if not vinculo:
        raise ApiError(403, 'Ese docente no está vinculado a tu curso.')
    mid = uid('mi')
    fecha = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        run(
            "INSERT INTO mensajes_institucionales (id, remitente_id, remitente_rol, destino_tipo, destino_id, tipo, texto, fecha, foto_url) VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, tutor_id, 'tutor', 'docente', docente_id, tipo, texto, fecha, foto_url or None),
        )
    except Exception:
        run(
            "INSERT INTO mensajes_institucionales (id, remitente_id, remitente_rol, destino_tipo, destino_id, tipo, texto, fecha) VALUES (?,?,?,?,?,?,?,?)",
            (mid, tutor_id, 'tutor', 'docente', docente_id, tipo, texto, fecha),
        )
    try:
        enviar_push_a_usuario(docente_id, 'Mensaje del Tutor ' + (tutor.get('nombre') or ''), (texto or '')[:120], {'tipo': 'mensaje'})
    except Exception as ex:
        print('push tutor->docente', ex)
    return 201, {'ok': True, 'mensajeId': mid}


def h_mensajes_enviados_tutor(params, body):
    """Mensajes que el tutor envió a representantes y a docentes."""
    tid = params['id']
    tutor = row("SELECT * FROM usuarios WHERE id = ? AND rol = 'tutor'", (tid,))
    if not tutor:
        raise ApiError(404, 'Tutor no encontrado.')
    out = []
    # A representantes (tabla mensajes)
    lista = rows(
        "SELECT m.*, e.nombre as estudiante_nombre, u.nombre as dest_nombre "
        "FROM mensajes m "
        "JOIN estudiantes e ON e.id = m.estudiante_id "
        "LEFT JOIN usuarios u ON u.id = e.representante_id "
        "WHERE m.remitente_id = ? AND m.remitente_rol = 'tutor' ORDER BY m.fecha DESC",
        (tid,),
    )
    for m in lista:
        d = ser_mensaje(m)
        d['estudianteNombre'] = m.get('estudiante_nombre') or ''
        d['destinoNombre'] = m.get('dest_nombre') or 'Representante'
        d['destinoTipo'] = 'representante'
        d['remitenteNombre'] = tutor.get('nombre') or 'Tutor'
        out.append(d)
    # A docentes (institucionales)
    lista2 = rows(
        "SELECT * FROM mensajes_institucionales WHERE remitente_id = ? AND remitente_rol = 'tutor' ORDER BY fecha DESC",
        (tid,),
    )
    for m in lista2:
        dest_nombre = ''
        if m.get('destino_id'):
            u = row('SELECT nombre FROM usuarios WHERE id = ?', (m['destino_id'],))
            dest_nombre = u['nombre'] if u else 'Docente'
        d = ser_msg_inst(m, tutor.get('nombre') or 'Tutor')
        d['destinoNombre'] = dest_nombre
        d['destinoTipo'] = 'docente'
        out.append(d)
    out.sort(key=lambda x: str(x.get('fecha') or ''), reverse=True)
    return 200, {'mensajes': out}


def h_salud(params, body):
    st = db_status()
    return 200, {
        'ok': True,
        'servicio': 'Enlace Escolar API (Python)',
        'hora': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'db': st,
    }



ADMIN_KEY = 'LouPao0320.'


def require_admin(body):
    key = (body or {}).get('adminKey') or ''
    if key != ADMIN_KEY:
        raise ApiError(403, 'Clave de configuración incorrecta.')


def h_admin_login(params, body):
    require_admin(body)
    return 200, {'ok': True}


def h_admin_resumen(params, body):
    require_admin(body)
    tutores = []
    for u in rows("SELECT * FROM usuarios WHERE rol = 'tutor' ORDER BY nombre"):
        c = row('SELECT nombre, clave_curso FROM cursos WHERE tutor_id = ?', (u['id'],))
        tutores.append({
            'id': u['id'],
            'nombre': u['nombre'],
            'claveAcceso': u['clave_acceso'],
            'cursoNombre': c['nombre'] if c else '',
            'claveCurso': c['clave_curso'] if c else '',
        })
    docentes = []
    for u in rows("SELECT * FROM usuarios WHERE rol = 'docente' ORDER BY nombre"):
        cursos = rows(
            "SELECT c.nombre AS nombre, dc.asignatura AS asignatura, c.clave_curso AS clave_curso "
            "FROM docente_cursos dc JOIN cursos c ON c.id = dc.curso_id WHERE dc.docente_id = ?",
            (u['id'],),
        )
        docentes.append({
            'id': u['id'],
            'nombre': u['nombre'],
            'claveAcceso': u['clave_acceso'],
            'cursos': [
                {'nombre': x['nombre'], 'asignatura': x['asignatura'], 'claveCurso': x['clave_curso']}
                for x in cursos
            ],
        })
    representantes = []
    for u in rows("SELECT * FROM usuarios WHERE rol = 'representante' ORDER BY nombre"):
        hijos = rows(
            "SELECT e.nombre AS nombre, e.representante_contacto AS contacto "
            "FROM estudiantes e WHERE e.representante_id = ?",
            (u['id'],),
        )
        representantes.append({
            'id': u['id'],
            'nombre': u['nombre'],
            'claveAcceso': u['clave_acceso'],
            'estudiantes': [{'nombre': h['nombre'], 'contacto': h['contacto'] or ''} for h in hijos],
        })
    autoridades = []
    for u in rows("SELECT * FROM usuarios WHERE rol IN ('rector','inspector','dece') ORDER BY rol, nombre"):
        autoridades.append({
            'id': u['id'],
            'nombre': u['nombre'],
            'rol': u['rol'],
            'claveAcceso': u['clave_acceso'],
        })
    return 200, {
        'tutores': tutores,
        'docentes': docentes,
        'representantes': representantes,
        'autoridades': autoridades,
    }


def h_admin_crear_autoridad(params, body):
    require_admin(body)
    nombre = mayus_nombre(body.get('nombre'))
    rol = (body.get('rol') or '').strip().lower()
    if rol not in ('rector', 'inspector', 'dece'):
        raise ApiError(400, 'Rol inválido. Use rector, inspector o dece.')
    if not nombre:
        raise ApiError(400, 'Ingresa el nombre de la autoridad.')
    clave = codigo6()
    aid = uid('aut')
    run('INSERT INTO usuarios (id, nombre, rol, clave_acceso) VALUES (?,?,?,?)', (aid, nombre, rol, clave))
    return 201, {'usuarioId': aid, 'claveAcceso': clave, 'rol': rol, 'nombre': nombre}



def h_admin_borrar_usuario(params, body):
    require_admin(body)
    uid_del = (body.get('id') or '').strip()
    rol = (body.get('rol') or '').strip().lower()
    if not uid_del or rol not in ('tutor', 'docente'):
        raise ApiError(400, 'Indica el usuario y el rol (tutor o docente).')
    if rol == 'tutor':
        return h_borrar_tutor({'id': uid_del}, body)
    return h_borrar_docente({'id': uid_del}, body)


def h_admin_editar_autoridad(params, body):
    require_admin(body)
    aid = (body.get('id') or '').strip()
    nombre = mayus_nombre(body.get('nombre'))
    rol = (body.get('rol') or '').strip().lower()
    if not aid or not nombre:
        raise ApiError(400, 'Indica el usuario y el nombre.')
    if rol not in ('rector', 'inspector', 'dece'):
        raise ApiError(400, 'Rol inválido. Use rector, inspector o dece.')
    u = row("SELECT * FROM usuarios WHERE id = ? AND rol IN ('rector','inspector','dece')", (aid,))
    if not u:
        raise ApiError(404, 'Autoridad no encontrada.')
    run('UPDATE usuarios SET nombre = ?, rol = ? WHERE id = ?', (nombre, rol, aid))
    return 200, {'ok': True, 'id': aid, 'nombre': nombre, 'rol': rol}


def h_admin_borrar_autoridad(params, body):
    require_admin(body)
    aid = (body.get('id') or '').strip()
    if not aid:
        raise ApiError(400, 'Indica la autoridad a eliminar.')
    u = row("SELECT * FROM usuarios WHERE id = ? AND rol IN ('rector','inspector','dece')", (aid,))
    if not u:
        raise ApiError(404, 'Autoridad no encontrada.')
    try:
        run("DELETE FROM mensajes_institucionales WHERE remitente_id = ?", (aid,))
    except Exception:
        pass
    run('DELETE FROM usuarios WHERE id = ?', (aid,))
    return 200, {'ok': True}

def h_admin_borrar_todo(params, body):
    require_admin(body)
    for stmt in [
        'DELETE FROM mensajes_institucionales',
        'DELETE FROM mensajes',
        'DELETE FROM dispositivos_push',
        'DELETE FROM estudiantes',
        'DELETE FROM docente_cursos',
        'DELETE FROM cursos',
        'DELETE FROM usuarios',
    ]:
        try:
            execute_write(stmt)
        except Exception:
            run(stmt)
    return 200, {'ok': True}



# ---------- tabla de rutas ----------


def _multipart_body(fields, file_field, filename, raw, content_type="image/jpeg"):
    import uuid
    boundary = "----EE" + uuid.uuid4().hex
    parts = []
    for name, value in fields:
        parts.append(
            ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, name, value)).encode()
        )
    parts.append(
        ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n" % (
            boundary, file_field, filename, content_type
        )).encode() + raw + b"\r\n"
    )
    parts.append(("--%s--\r\n" % boundary).encode())
    return boundary, b"".join(parts)


def _http_post(url, data, headers, timeout=45):
    import urllib.request
    import ssl
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read(), resp.status


def _upload_foto_bytes(raw, filename="foto.jpg"):
    """Sube la imagen fuera de Turso. Prueba varios servicios por si uno falla."""
    import urllib.parse
    errors = []

    # 1) Cloudinary (si está configurado en Render)
    cloud = (os.environ.get("CLOUDINARY_CLOUD_NAME") or "").strip()
    preset = (os.environ.get("CLOUDINARY_UPLOAD_PRESET") or "").strip()
    if cloud and preset:
        try:
            boundary, body = _multipart_body(
                [("upload_preset", preset)],
                "file",
                filename,
                raw,
            )
            data, status = _http_post(
                "https://api.cloudinary.com/v1_1/%s/image/upload" % cloud,
                body,
                {"Content-Type": "multipart/form-data; boundary=%s" % boundary},
            )
            info = json.loads(data.decode("utf-8"))
            url = info.get("secure_url") or info.get("url")
            if url:
                return url
            errors.append("Cloudinary: respuesta sin URL")
        except Exception as e:
            errors.append("Cloudinary: " + str(e)[:80])

    # 2) 0x0.st
    try:
        boundary, body = _multipart_body([], "file", filename, raw)
        data, status = _http_post(
            "https://0x0.st",
            body,
            {"Content-Type": "multipart/form-data; boundary=%s" % boundary, "User-Agent": "EnlaceEscolar/1.0"},
        )
        url = data.decode("utf-8").strip()
        if url.startswith("http"):
            return url
        errors.append("0x0.st: " + url[:60])
    except Exception as e:
        errors.append("0x0.st: " + str(e)[:80])

    # 3) litterbox (catbox temporal 24h-72h) — mejor que nada para pruebas
    try:
        boundary, body = _multipart_body(
            [("reqtype", "fileupload"), ("time", "72h")],
            "fileToUpload",
            filename,
            raw,
        )
        data, status = _http_post(
            "https://litterbox.catbox.moe/resources/internals/api.php",
            body,
            {"Content-Type": "multipart/form-data; boundary=%s" % boundary, "User-Agent": "EnlaceEscolar/1.0"},
        )
        url = data.decode("utf-8").strip()
        if url.startswith("http"):
            return url
        errors.append("litterbox: " + url[:60])
    except Exception as e:
        errors.append("litterbox: " + str(e)[:80])

    # 4) catbox permanente
    try:
        boundary, body = _multipart_body(
            [("reqtype", "fileupload")],
            "fileToUpload",
            filename,
            raw,
        )
        data, status = _http_post(
            "https://catbox.moe/user/api.php",
            body,
            {"Content-Type": "multipart/form-data; boundary=%s" % boundary, "User-Agent": "EnlaceEscolar/1.0"},
        )
        url = data.decode("utf-8").strip()
        if url.startswith("http"):
            return url
        errors.append("catbox: " + url[:60])
    except Exception as e:
        errors.append("catbox: " + str(e)[:80])

    # 5) ImgBB si hay clave
    imgbb = (os.environ.get("IMGBB_API_KEY") or "").strip()
    if imgbb:
        try:
            import base64
            b64 = base64.b64encode(raw).decode("ascii")
            body = urllib.parse.urlencode({"key": imgbb, "image": b64}).encode()
            data, status = _http_post(
                "https://api.imgbb.com/1/upload",
                body,
                {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "EnlaceEscolar/1.0"},
            )
            info = json.loads(data.decode("utf-8"))
            url = (((info.get("data") or {}).get("url")) or "")
            if url:
                return url
            errors.append("imgbb: sin URL")
        except Exception as e:
            errors.append("imgbb: " + str(e)[:80])

    # Último recurso: guardar imagen pequeña como data-URL (solo si es liviana)
    # Así la foto sí se ve aunque fallen los servicios externos.
    if len(raw) <= 90000:
        import base64 as _b64
        return "data:image/jpeg;base64," + _b64.b64encode(raw).decode("ascii")

    raise ApiError(
        500,
        "No se pudo subir la foto. Prueba una imagen más pequeña o configura Cloudinary. "
        + " | ".join(errors)[:160],
    )


def h_subir_foto(params, body):
    try:
        from db import ensure_foto_columns
        ensure_foto_columns()
    except Exception:
        pass
    data_url = (body.get("dataUrl") or body.get("data_url") or "").strip()
    if not data_url.startswith("data:image/"):
        raise ApiError(400, "Imagen inválida.")
    try:
        header, b64 = data_url.split(",", 1)
    except ValueError:
        raise ApiError(400, "Imagen inválida.")
    import base64
    try:
        raw = base64.b64decode(b64)
    except Exception:
        raise ApiError(400, "No se pudo leer la imagen.")
    if len(raw) > 1200000:
        raise ApiError(400, "La foto es muy pesada. Elige una más pequeña.")
    if len(raw) < 100:
        raise ApiError(400, "Imagen vacía.")
    url = _upload_foto_bytes(raw, "enlace-escolar.jpg")
    return 200, {"url": url, "ok": True}


ROUTES = [
    ('POST', r'^/api/fotos$', h_subir_foto),
    ('GET', r'^/api/push/vapid-public-key$', h_push_vapid_public),
    ('POST', r'^/api/push/subscribe$', h_push_subscribe),
    ('POST', r'^/api/push/unsubscribe$', h_push_unsubscribe),
    ('POST', r'^/api/push/test$', h_push_test),

    ('POST', r'^/api/tutores$', h_crear_tutor),
    ('POST', r'^/api/tutores/login$', h_login_tutor),
    ('DELETE', r'^/api/tutores/(?P<id>[^/]+)$', h_borrar_tutor),

    ('POST', r'^/api/estudiantes$', h_crear_estudiante),
    ('PATCH', r'^/api/estudiantes/(?P<id>[^/]+)$', h_editar_estudiante),
    ('DELETE', r'^/api/estudiantes/(?P<id>[^/]+)$', h_borrar_estudiante),

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
    ('POST', r'^/api/usuarios/(?P<id>[^/]+)/cambiar-clave$', h_cambiar_clave),

    ('POST', r'^/api/mensajes$', h_crear_mensaje),
    ('POST', r'^/api/docentes/mensajes-tutor$', h_mensaje_docente_a_tutor),
    ('GET', r'^/api/docentes/(?P<id>[^/]+)/mensajes-tutor-enviados$', h_mensajes_docente_enviados_tutor),
    ('POST', r'^/api/mensajes/curso$', h_mensaje_a_curso),
    ('PATCH', r'^/api/mensajes/(?P<id>[^/]+)/confirmar$', h_confirmar_mensaje),
    ('GET', r'^/api/tutores/(?P<id>[^/]+)/mensajes$', h_mensajes_tutor),
    ('GET', r'^/api/tutores/(?P<id>[^/]+)/mensajes-enviados$', h_mensajes_enviados_tutor),
    ('POST', r'^/api/tutores/mensajes-docente$', h_mensaje_tutor_a_docente),
    ('POST', r'^/api/tutores/mensajes-docentes-todos$', h_mensaje_tutor_a_todos_docentes),
    ('GET', r'^/api/cursos/(?P<id>[^/]+)/docentes$', h_docentes_de_curso),

    ('POST', r'^/api/autoridades$', h_crear_autoridad),
    ('POST', r'^/api/autoridades/login$', h_login_autoridad),
    ('GET', r'^/api/autoridades/tutores$', h_lista_tutores),
    ('GET', r'^/api/autoridades/docentes$', h_lista_docentes),
    ('POST', r'^/api/autoridades/mensajes$', h_crear_mensaje_institucional),
    ('GET', r'^/api/autoridades/(?P<id>[^/]+)/mensajes$', h_mensajes_enviados_autoridad),
    ('GET', r'^/api/usuarios/(?P<id>[^/]+)/mensajes-institucionales$', h_mensajes_institucionales_recibidos),

    ('POST', r'^/api/borrar-todo$', h_borrar_todo),
    ('POST', r'^/api/admin/login$', h_admin_login),
    ('POST', r'^/api/admin/resumen$', h_admin_resumen),
    ('POST', r'^/api/admin/autoridades$', h_admin_crear_autoridad),
    ('POST', r'^/api/admin/autoridades/editar$', h_admin_editar_autoridad),
    ('POST', r'^/api/admin/autoridades/borrar$', h_admin_borrar_autoridad),
    ('POST', r'^/api/admin/borrar-todo$', h_admin_borrar_todo),
    ('POST', r'^/api/admin/borrar-usuario$', h_admin_borrar_usuario),
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

# db.py
# Capa de acceso a datos. Usa sqlite3, incluido en la librería estándar de Python,
# así que no requiere instalar ninguna dependencia externa (no hace falta pip install).
import sqlite3
import os
import secrets
import string

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'enlace-escolar.db'))

_con = sqlite3.connect(DB_PATH, check_same_thread=False)
_con.row_factory = sqlite3.Row
_con.execute('PRAGMA foreign_keys = ON')


def get_conn():
    return _con


def init_db():
    _con.executescript('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id            TEXT PRIMARY KEY,
            nombre        TEXT NOT NULL,
            rol           TEXT NOT NULL CHECK(rol IN ('tutor','docente','representante')),
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
            token_fcm    TEXT NOT NULL,
            plataforma   TEXT NOT NULL
        );
    ''')
    _con.commit()


def uid(prefix):
    return f"{prefix}_{secrets.token_hex(6)}"


def codigo6():
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # sin caracteres ambiguos
    return ''.join(secrets.choice(alphabet) for _ in range(6))

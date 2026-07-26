# Guía de despliegue — Enlace Escolar

Esta guía te lleva de la mano para publicar la app en internet, gratis, usando
GitHub (para guardar el código) y Render (para que corra en un servidor real
con una dirección propia). No necesitas saber programar ni usar la terminal:
todo se hace desde el navegador.

Al terminar tendrás **dos enlaces**:
- Uno para el **servidor** (la API) — no lo compartes con nadie, es interno.
- Uno para la **app** — este es el que le das a Tutores, Docentes y Representantes.

⚠️ **Recuerda:** elegiste empezar en el plan gratuito. Esto significa que los
datos (cuentas, estudiantes, mensajes) pueden borrarse si el servidor se
reinicia o se actualiza. Es perfecto para probar con tu institución antes de
decidir si vale la pena pasar al plan pago (~$7-8/mes) para que los datos
queden guardados para siempre. Te explico cómo hacer ese cambio al final.

---

## Parte 1 — Subir el código a GitHub

1. Ve a [github.com](https://github.com) y crea una cuenta gratuita si no tienes una.
2. Arriba a la derecha, haz clic en el **+** → **New repository**.
3. Ponle de nombre `enlace-escolar` (o el que prefieras) → **Create repository**.
4. En la página del repositorio, haz clic en **Add file → Upload files**.
5. Arrastra **toda la carpeta** que te compartí (con `backend/`, `frontend/`,
   `render.yaml` y `.gitignore` adentro) a esa ventana.
6. Baja y haz clic en **Commit changes**.

Ya tienes el código guardado en GitHub. No vuelvas a tocar esto salvo cuando
tengas que actualizar la dirección del servidor (Parte 3).

---

## Parte 2 — Desplegar en Render

1. Ve a [render.com](https://render.com) y crea una cuenta gratuita (puedes
   entrar directo con tu cuenta de GitHub, es más rápido).
2. Arriba a la derecha, haz clic en **New +** → **Blueprint**.
3. Conecta el repositorio `enlace-escolar` que acabas de crear.
4. Render va a leer el archivo `render.yaml` automáticamente y te va a mostrar
   **dos servicios** listos para crear:
   - `enlace-escolar-api` (el servidor)
   - `enlace-escolar-app` (la app)
5. Confirma con **Apply** / **Create**. Espera unos minutos mientras Render
   construye y enciende ambos servicios (verás el progreso en pantalla).
6. Cuando `enlace-escolar-api` termine, haz clic en él y copia su dirección,
   arriba de la página (algo como `https://enlace-escolar-api.onrender.com`).

Si tu cuenta de Render no ofrece la opción "Blueprint" (algunas cuentas nuevas
no la muestran de inmediato), puedes crear los dos servicios a mano:
- **New + → Web Service** → conecta el repo → en "Root Directory" escribe
  `backend` → "Build Command" escribe `pip install -r requirements.txt` → "Start Command"
  `python3 server.py` → plan **Free** → Create.
- **New + → Static Site** → conecta el repo → en "Root Directory" escribe
  `frontend` → "Publish Directory" escribe `.` → Create.

---

## Parte 3 — Conectar la app con el servidor

1. Abre el archivo `frontend/index.html` en tu computadora (con el Bloc de
   notas, VS Code, o cualquier editor de texto).
2. Busca esta línea, cerca del inicio:
   ```js
   const API_BASE = 'http://localhost:3000';
   ```
3. Reemplázala por la dirección real de tu servidor que copiaste en la Parte 2:
   ```js
   const API_BASE = 'https://enlace-escolar-api.onrender.com';
   ```
4. Guarda el archivo.
5. Vuelve a GitHub, entra a la carpeta `frontend`, haz clic en `index.html` →
   ícono del lápiz (editar) → borra el contenido y pega el archivo actualizado
   (o usa de nuevo "Upload files" y reemplázalo) → **Commit changes**.
6. Render detecta el cambio solo y vuelve a publicar la app en 1-2 minutos.

---

## Parte 4 — Probarla de verdad

1. Ve a Render → tu servicio `enlace-escolar-app` → copia su dirección
   (algo como `https://enlace-escolar-app.onrender.com`).
2. Ábrela en el navegador de tu celular. **Este es el enlace que compartes.**
3. Prueba registrar un Tutor, un Docente y un Representante desde distintos
   dispositivos o navegadores, como ya lo hicimos juntos.

**Nota sobre la primera carga:** en el plan gratuito, el servidor "se
duerme" después de 15 minutos sin uso. La primera vez que alguien abre la
app después de ese tiempo, puede tardar hasta 1 minuto en responder — es
normal, no está fallando, solo está "despertando".

---

## Cuándo pasar al plan pago (persistencia real)

Cuando decidas que los datos deben quedar guardados para siempre:
1. En Render, entra a `enlace-escolar-api` → **Settings** → cambia el plan
   de Free a **Starter** (~$7/mes).
2. En la misma página, busca **Disks** → **Add Disk** → nómbralo `data`,
   móntalo en la ruta `/data`, tamaño 1 GB (~$0.25/mes extra).
3. En **Environment**, agrega una variable: `DB_PATH` = `/data/enlace-escolar.db`.
4. Guarda y espera a que redepliegue.

No necesitas cambiar nada más en el código: `db.py` ya está preparado para
usar esa variable si existe.

---

## Parte 5 — Instalarla en el celular como una app de verdad

Esto es lo que la hace sentir "normal", con su propio ícono, sin necesitar
tienda de aplicaciones:

**En Android (Chrome):**
1. Abre el enlace de la app (`enlace-escolar-app.onrender.com`).
2. Te va a aparecer un aviso "Agregar Enlace Escolar a la pantalla de inicio"
   — acéptalo. Si no aparece solo, toca los tres puntos (⋮) arriba a la
   derecha → **"Instalar aplicación"** o **"Agregar a pantalla de inicio"**.
3. Listo — el ícono con el escudo de la institución queda en el celular,
   se abre a pantalla completa, como cualquier otra app.

**En iPhone (Safari):**
1. Abre el enlace de la app en **Safari** (tiene que ser Safari, no Chrome).
2. Toca el ícono de compartir (el cuadrado con la flecha hacia arriba).
3. Baja y toca **"Agregar a pantalla de inicio"**.
4. Confirma el nombre ("Enlace Escolar") y toca **"Agregar"**.

**Para compartirla por WhatsApp:** solo envía el enlace
(`https://enlace-escolar-app.onrender.com`) por un chat o grupo. La persona
que lo reciba toca el enlace, y luego sigue los pasos de arriba para
instalarla en su propio celular.


#!/usr/bin/env python3
"""Links de subida directa a una carpeta del Drive de Diego, sin que el que sube tenga cuenta.

  sesion.py login            autoriza en el navegador (alcance drive.file: solo lo que crea esta app)
  sesion.py nuevo "Nombre"   crea una sesion de subida (dura 1 semana, sirve para 1 archivo) e imprime el link
  sesion.py estado           lista lo que hay en la carpeta
  sesion.py renombrar ID "Nombre.mov" video/quicktime
  sesion.py papelera ID      manda a la papelera un archivo creado por esta app

El token queda en ~/.config/subir-video (fuera del repo). La sesion viaja en el #fragmento del link:
no queda en el repo ni llega a GitHub.
"""
import base64
import hashlib
import http.server
import json
import pathlib
import secrets
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

# El Python de python.org no trae certificados: se usan los del sistema.
urllib.request.install_opener(urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile="/etc/ssl/cert.pem"))))

CLIENT = pathlib.Path.home() / "code/gphotos-mcp/credentials/client_secret.json"
DIR = pathlib.Path.home() / ".config/subir-video"
TOKEN = DIR / "token.json"
CARPETA_ID = DIR / "carpeta.txt"
CARPETA = "Programas para subir"
SCOPE = "https://www.googleapis.com/auth/drive.file"
PAGINA = "https://diegocheein.github.io/subir-video/"
ORIGEN = "https://diegocheein.github.io"


def cliente():
    d = json.loads(CLIENT.read_text())
    return d[next(iter(d))]


def post_token(datos):
    c = cliente()
    datos = {**datos, "client_id": c["client_id"], "client_secret": c["client_secret"]}
    req = urllib.request.Request(c["token_uri"], urllib.parse.urlencode(datos).encode())
    return json.load(urllib.request.urlopen(req))


def login():
    verificador = secrets.token_urlsafe(48)
    estado = secrets.token_urlsafe(16)
    recibido = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if "state" not in q:
                self.send_error(404)
                return
            recibido.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h1>Listo, ya podés cerrar esta pestaña.</h1>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    srv.timeout = 600
    redirect = f"http://localhost:{srv.server_port}"
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    url = cliente()["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": cliente()["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": estado,
        "code_challenge": desafio, "code_challenge_method": "S256",
        "login_hint": "diego@solucionesdigitales.com.ar",
    })
    subprocess.run(["open", url], check=True)
    print("Abrí este link para autorizar (espera 10 min):", url, flush=True)
    while "state" not in recibido:
        srv.handle_request()
        if not recibido:
            sys.exit("No llegó la autorización a tiempo.")
    if recibido.get("state") != estado or "code" not in recibido:
        sys.exit(f"Autorización rechazada: {recibido.get('error', 'estado inválido')}")
    t = post_token({"grant_type": "authorization_code", "code": recibido["code"],
                    "redirect_uri": redirect, "code_verifier": verificador})
    DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    TOKEN.touch(mode=0o600)
    TOKEN.write_text(json.dumps({"refresh_token": t["refresh_token"], "scope": t.get("scope")}))
    print("Autorizado. Alcance:", t.get("scope"))


def acceso():
    return post_token({"grant_type": "refresh_token",
                       "refresh_token": json.loads(TOKEN.read_text())["refresh_token"]})["access_token"]


def api(metodo, url, cuerpo=None, extra=None):
    h = {"Authorization": f"Bearer {acceso()}", "Content-Type": "application/json; charset=UTF-8", **(extra or {})}
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    return urllib.request.urlopen(urllib.request.Request(url, datos, h, method=metodo))


def carpeta():
    if CARPETA_ID.exists():
        return CARPETA_ID.read_text().strip()
    r = json.load(api("POST", "https://www.googleapis.com/drive/v3/files?fields=id",
                      {"name": CARPETA, "mimeType": "application/vnd.google-apps.folder"}))
    CARPETA_ID.write_text(r["id"])
    return r["id"]


def nuevo(nombre):
    r = api("POST", "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable",
            {"name": nombre, "parents": [carpeta()]},
            {"Origin": ORIGEN, "X-Upload-Content-Type": "video/mp4"})
    sesion = r.headers["Location"].encode()
    # Link corto: en el repo publico queda la sesion cifrada; la clave solo viaja en el #fragmento.
    clave = secrets.token_urlsafe(12)
    flujo = b""
    while len(flujo) < len(sesion):
        flujo += hashlib.sha256(f"{clave}:{len(flujo) // 32}".encode()).digest()
    cifrada = bytes(a ^ b for a, b in zip(sesion, flujo)).hex()
    ident = hashlib.sha256(clave.encode()).hexdigest()[:16]
    repo = pathlib.Path(__file__).parent
    destino = repo / "s" / f"{ident}.txt"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(cifrada)
    subprocess.run(["git", "-C", str(repo), "add", str(destino)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                    "Nueva sesion de subida\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q"], check=True)
    print(PAGINA + "#" + clave)


def estado():
    q = urllib.parse.urlencode({"q": f"'{carpeta()}' in parents and trashed=false",
                                "fields": "files(id,name,size,mimeType,createdTime,md5Checksum,webViewLink)"})
    print(json.dumps(json.load(api("GET", "https://www.googleapis.com/drive/v3/files?" + q))["files"], indent=1))


def renombrar(fid, nombre, mime):
    api("PATCH", f"https://www.googleapis.com/drive/v3/files/{fid}", {"name": nombre, "mimeType": mime})
    print("ok")


def papelera(fid):
    api("PATCH", f"https://www.googleapis.com/drive/v3/files/{fid}", {"trashed": True})
    print("ok")


if __name__ == "__main__":
    ordenes = {"login": login, "nuevo": nuevo, "estado": estado, "renombrar": renombrar, "papelera": papelera}
    try:
        ordenes[sys.argv[1]](*sys.argv[2:])
    except urllib.error.HTTPError as e:
        sys.exit(f"Error {e.code}: {e.read().decode()[:600]}")

import os, json, urllib.request
import unicodedata
from datetime import datetime, timezone

ASSET_UID = "avkyXRNMhnGTPqmNwdj3C4"
BASE = "https://eu.kobotoolbox.org"
TOKEN = os.environ["KOBO_API_TOKEN"]


def get_json(url):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Token {TOKEN}",
        "Accept": "application/json"
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


FIELD_PRIORITY = {
    "municipio":   ["municipio", "municipio_sel"],
    "comunidad":   ["comunidad_nombre", "comunidad"],
    "caserio":     ["caserio_nombre", "caserio"],
    "responsable": ["responsable", "informante"],
    "fecha":       ["fecha"],
    "mm":          ["mm", "lluvia", "precipit"],
}


def pick_exact(r, candidates):
    keys_lower = {k.lower().split("/")[-1]: k for k in r.keys() if not k.startswith("_")}
    for cand in candidates:
        if cand in keys_lower:
            v = r[keys_lower[cand]]
            if v not in (None, ""):
                return v
    for cand in candidates:
        for k, v in r.items():
            if not k.startswith("_") and cand in k.lower() and v not in (None, ""):
                return v
    return ""


def nombre_limpio(valor):
    valor = str(valor or "").strip()
    return valor.replace("_", " ").title()


def limpiar_caserio(caserio_limpio, comunidad_limpia):
    prefijo = comunidad_limpia + " "
    if caserio_limpio.startswith(prefijo) and len(caserio_limpio) > len(prefijo):
        return caserio_limpio[len(prefijo):]
    return caserio_limpio


def normalizar_clave(texto):
    """Versión sin acentos y en minúsculas, solo para COMPARAR — nunca se guarda tal cual."""
    texto = unicodedata.normalize("NFD", str(texto or ""))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.strip().lower()


# Nombres "canónicos" preferidos cuando existen variantes con/sin acento
# (ej. "Tasharja" vs "Tasharjá"). La clave va normalizada (sin acentos,
# minúsculas); el valor es la forma correcta que se guarda en datos.json.
# Si aparecen más casos con el tiempo, agrégalos aquí.
NOMBRES_CANONICOS = {
    "tasharja": "Tasharjá",
    "el cerron": "El Cerrón",
}


def forma_preferida(valor_normalizado, valor_original):
    return NOMBRES_CANONICOS.get(valor_normalizado, valor_original)


# ---------------------------------------------------------------------------
# Descarga de todos los registros desde Kobo (paginado)
# ---------------------------------------------------------------------------
url = f"{BASE}/api/v2/assets/{ASSET_UID}/data/?limit=1000"
raw = []
while url:
    page = get_json(url)
    raw.extend(page.get("results", []))
    url = page.get("next")


# ---------------------------------------------------------------------------
# Procesamiento: extracción de campos + deduplicación por envío más reciente
# ---------------------------------------------------------------------------
registros_por_clave = {}  # clave_normalizada -> (submission_time, registro)

for r in raw:
    mm = pick_exact(r, FIELD_PRIORITY["mm"])
    try:
        mm = float(mm or 0)
    except (TypeError, ValueError):
        mm = 0.0

    fecha = str(pick_exact(r, FIELD_PRIORITY["fecha"]))[:10]

    comunidad = nombre_limpio(pick_exact(r, FIELD_PRIORITY["comunidad"]))
    caserio = nombre_limpio(pick_exact(r, FIELD_PRIORITY["caserio"]))
    caserio = limpiar_caserio(caserio, comunidad)

    municipio = str(pick_exact(r, FIELD_PRIORITY["municipio"])).strip()
    responsable = str(pick_exact(r, FIELD_PRIORITY["responsable"])).strip()

    comunidad_norm = normalizar_clave(comunidad)
    caserio_norm = normalizar_clave(caserio)
    comunidad = forma_preferida(comunidad_norm, comunidad)
    caserio = forma_preferida(caserio_norm, caserio)

    registro = {
        "municipio": municipio,
        "comunidad": comunidad,
        "caserio": caserio,
        "responsable": responsable,
        "fecha": fecha,
        "mm": mm,
    }

    # La clave de agrupación usa versiones normalizadas de municipio/comunidad/
    # caserío (para fundir variantes de acentuación), pero el RESPONSABLE se
    # deja tal cual viene: un cambio de informante NO se fusiona
    # automáticamente, porque podría ser un relevo real de la persona que
    # reporta en ese punto.
    clave = (
        normalizar_clave(municipio),
        comunidad_norm,
        caserio_norm,
        responsable,
        fecha,
    )
    submission_time = r.get("_submission_time", "") or ""

    if clave in registros_por_clave:
        time_anterior, registro_anterior = registros_por_clave[clave]
        if registro_anterior["mm"] != mm:
            print(f"AVISO: envios duplicados con mm distinto para {clave}: "
                  f"{registro_anterior['mm']} mm ({time_anterior}) vs "
                  f"{mm} mm ({submission_time}). Se conserva el envio mas reciente.")
        if submission_time >= time_anterior:
            registros_por_clave[clave] = (submission_time, registro)
    else:
        registros_por_clave[clave] = (submission_time, registro)

datos = [registro for _, registro in registros_por_clave.values()]


# ---------------------------------------------------------------------------
# Aviso informativo (no bloquea nada): posibles relevos de informante en un
# mismo punto de monitoreo -- misma comunidad+caserío, responsable distinto.
# ---------------------------------------------------------------------------
punto_a_responsables = {}
for d in datos:
    punto = (d["municipio"], d["comunidad"], d["caserio"])
    punto_a_responsables.setdefault(punto, set()).add(d["responsable"])

for punto, responsables in punto_a_responsables.items():
    if len(responsables) > 1:
        print(f"AVISO: el punto {punto} tiene mas de un responsable en el "
              f"historico: {sorted(responsables)}. Verificar si es un relevo "
              f"real o el mismo informante con nombre distinto.")


# ---------------------------------------------------------------------------
# Orden final y guardado
# ---------------------------------------------------------------------------
datos.sort(key=lambda x: (x["fecha"], x["comunidad"], x["caserio"], x["responsable"]))

payload = {
    "actualizado_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "total_registros": len(datos),
    "datos": datos
}

with open("datos.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

print(f"OK: {len(datos)} registros")

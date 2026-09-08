import os, json, urllib.request
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

url = f"{BASE}/api/v2/assets/{ASSET_UID}/data/?limit=1000"
raw = []
while url:
    page = get_json(url)
    raw.extend(page.get("results", []))
    url = page.get("next")

datos = []
for r in raw:
    mm = pick_exact(r, FIELD_PRIORITY["mm"])
    try:
        mm = float(mm or 0)
    except (TypeError, ValueError):
        mm = 0.0
    fecha = str(pick_exact(r, FIELD_PRIORITY["fecha"]))[:10]
    datos.append({
        "municipio": str(pick_exact(r, FIELD_PRIORITY["municipio"])).strip(),
        "comunidad": nombre_limpio(pick_exact(r, FIELD_PRIORITY["comunidad"])),
        "caserio": nombre_limpio(pick_exact(r, FIELD_PRIORITY["caserio"])),
        "responsable": str(pick_exact(r, FIELD_PRIORITY["responsable"])).strip(),
        "fecha": fecha,
        "mm": mm
    })

datos.sort(key=lambda x: (x["fecha"], x["comunidad"], x["caserio"], x["responsable"]))
payload = {
    "actualizado_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "total_registros": len(datos),
    "datos": datos
}
with open("datos.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
print(f"OK: {len(datos)} registros")

import os, json, urllib.request
from datetime import datetime, timezone

ASSET_UID = "avkyXRNMhnGTPqmNwdj3C4"
BASE = "https://kf.kobotoolbox.org"
TOKEN = os.environ["KOBO_API_TOKEN"]

def get_json(url):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Token {TOKEN}",
        "Accept": "application/json"
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def pick(r, words):
    for k, v in r.items():
        kl = k.lower()
        if not k.startswith("_") and any(w in kl for w in words) and v not in (None, ""):
            return v
    return ""

url = f"{BASE}/api/v2/assets/{ASSET_UID}/data/?limit=1000"
raw = []
while url:
    page = get_json(url)
    raw.extend(page.get("results", []))
    url = page.get("next")

datos = []
for r in raw:
    mm = pick(r, ["mm", "lluvia", "precipit"])
    try:
        mm = float(mm or 0)
    except (TypeError, ValueError):
        mm = 0.0
    fecha = str(pick(r, ["fecha"]))[:10]
    datos.append({
        "municipio": str(pick(r, ["municipio"])).strip(),
        "comunidad": str(pick(r, ["comunidad"])).strip(),
        "caserio": str(pick(r, ["caserio", "caserío"])).strip(),
        "responsable": str(pick(r, ["responsable", "informante"])).strip(),
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

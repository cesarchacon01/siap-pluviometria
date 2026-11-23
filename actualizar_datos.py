import os, sys, json, re, zipfile, urllib.request
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, date, timedelta

ASSET_UID = "avkyXRNMhnGTPqmNwdj3C4"
BASE = "https://eu.kobotoolbox.org"
TOKEN = os.environ.get("KOBO_API_TOKEN", "")
ARCHIVO_MANUAL = "cargas_manuales.xlsx"   # registros de WhatsApp, MAGA y hojas escritas
CAIDA_MAXIMA = 0.05                        # si los registros bajan más de 5 %, no se sobrescribe datos.json


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


def normalizar_clave(texto):
    """Versión sin acentos y en minúsculas, solo para COMPARAR — nunca se guarda tal cual."""
    texto = unicodedata.normalize("NFD", str(texto or ""))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.strip().lower()


def limpiar_caserio(caserio_limpio, comunidad_limpia):
    comunidad_norm = normalizar_clave(comunidad_limpia)
    prefijo_norm = comunidad_norm + " "
    while True:
        caserio_norm = normalizar_clave(caserio_limpio)
        if caserio_norm.startswith(prefijo_norm) and len(caserio_norm) > len(prefijo_norm):
            caserio_limpio = caserio_limpio[len(prefijo_norm):]
        else:
            break
    return caserio_limpio


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


# Cambios de informante que sabemos son la MISMA persona/punto de monitoreo,
# no un relevo real -- se fusionan bajo el nombre correcto indicado.
# La clave va normalizada (sin acentos, minúsculas); el valor es el nombre
# correcto que se guarda en datos.json.
RESPONSABLES_CANONICOS = {
    "sucely villeda": "Marta Mendez",
}
# ---------------------------------------------------------------------------
# Descarga de todos los registros desde Kobo (paginado)
# ---------------------------------------------------------------------------
raw = []
if os.environ.get("SIAP_KOBO_PRUEBA"):          # solo para pruebas locales: lee los registros de un archivo
    raw = json.load(open(os.environ["SIAP_KOBO_PRUEBA"], encoding="utf-8"))
else:
    url = f"{BASE}/api/v2/assets/{ASSET_UID}/data/?limit=1000"
    while url:
        page = get_json(url)
        raw.extend(page.get("results", []))
        url = page.get("next")

print(f"DEBUG: se descargaron {len(raw)} registros crudos de Kobo")
if raw:
    print(f"DEBUG: primer registro tiene estas llaves: {list(raw[0].keys())}")

# ---------------------------------------------------------------------------
# Procesamiento: extracción de campos + deduplicación por envío más reciente
# ---------------------------------------------------------------------------
registros_por_clave = {}  # clave_normalizada -> (submission_time, registro)

sin_lluvia = 0
for r in raw:
    mm = pick_exact(r, FIELD_PRIORITY["mm"])
    # un valor de lluvia vacío o no numérico NO es 0 mm: el registro se descarta
    # y ese día queda sin dato (el visor lo trata como día sin reporte)
    try:
        if mm in (None, ""):
            raise ValueError
        mm = float(str(mm).replace(",", "."))
    except (TypeError, ValueError):
        sin_lluvia += 1
        continue

    fecha = str(pick_exact(r, FIELD_PRIORITY["fecha"]))[:10]

    comunidad = nombre_limpio(pick_exact(r, FIELD_PRIORITY["comunidad"]))
    caserio = nombre_limpio(pick_exact(r, FIELD_PRIORITY["caserio"]))
    caserio = limpiar_caserio(caserio, comunidad)

    municipio = str(pick_exact(r, FIELD_PRIORITY["municipio"])).strip()
    responsable = str(pick_exact(r, FIELD_PRIORITY["responsable"])).strip()
    responsable_norm = normalizar_clave(responsable)
    responsable = RESPONSABLES_CANONICOS.get(responsable_norm, responsable)

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
if sin_lluvia:
    print(f"AVISO: {sin_lluvia} envíos de Kobo sin dato de lluvia se descartaron (no se cuentan como 0 mm).")


# ---------------------------------------------------------------------------
# Cargas manuales (Excel): WhatsApp, hojas de MAGA y hojas escritas.
# Se leen con la biblioteca estándar (sin instalar nada en la tarea automática).
# Si un punto y día ya están en Kobo, vale Kobo; el Excel solo llena los vacíos.
# ---------------------------------------------------------------------------
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def leer_xlsx(ruta):
    """Devuelve {nombre_hoja: [[valores de la fila], ...]} con los valores ya calculados."""
    hojas = {}
    with zipfile.ZipFile(ruta) as z:
        comunes = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                comunes.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        libro = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        destino = {r.get("Id"): r.get("Target") for r in rels}
        for s in libro.find("m:sheets", NS):
            rid = s.get(f"{{{NS['r']}}}id")
            ruta_hoja = destino[rid].lstrip("/")
            ruta_hoja = ruta_hoja if ruta_hoja.startswith("xl/") else "xl/" + ruta_hoja
            filas = []
            for fila in ET.fromstring(z.read(ruta_hoja)).iter(f"{{{NS['m']}}}row"):
                valores = {}
                for c in fila.findall("m:c", NS):
                    col = re.match(r"[A-Z]+", c.get("r")).group(0)
                    idx = 0
                    for ch in col:
                        idx = idx * 26 + ord(ch) - 64
                    tipo, v = c.get("t"), c.find("m:v", NS)
                    if tipo == "s" and v is not None:
                        val = comunes[int(v.text)]
                    elif tipo == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
                    elif v is not None:
                        val = v.text
                    else:
                        val = None
                    valores[idx - 1] = val
                if valores:
                    filas.append([valores.get(i) for i in range(max(valores) + 1)])
            hojas[s.get("name")] = filas
    return hojas


def fecha_excel(v):
    """Acepta número de serie de Excel, AAAA-MM-DD o DD/MM/AAAA. Devuelve AAAA-MM-DD o None."""
    if v in (None, ""):
        return None
    s = str(v).strip()
    try:
        n = float(s)
        return (date(1899, 12, 30) + timedelta(days=int(n))).isoformat()
    except ValueError:
        pass
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s[:10], formato).date().isoformat()
        except ValueError:
            continue
    return None


def punto_texto(m, co, ca, inf):
    return f"{m} · {co} · {ca} · {inf}"


if os.path.exists(ARCHIVO_MANUAL):
    try:
        hojas = leer_xlsx(ARCHIVO_MANUAL)
        # catálogo de puntos: el texto de la lista se arma con municipio · comunidad · caserío · informante
        catalogo = {}
        for fila in hojas.get("Puntos", [])[1:]:
            fila = (fila + [None] * 6)[:6]
            if all(fila[1:5]):
                m, co, ca, inf = (str(x).strip() for x in fila[1:5])
                catalogo[normalizar_clave(punto_texto(m, co, ca, inf))] = (m, co, ca, inf)
        claves_kobo = {(normalizar_clave(d["municipio"]), normalizar_clave(d["comunidad"]),
                        normalizar_clave(d["caserio"]), normalizar_clave(d["responsable"]), d["fecha"]) for d in datos}
        agregados, repetidos, corregidos, invalidos, vistos = 0, 0, 0, [], set()
        filas = list(enumerate(hojas.get("Registros", [])[1:], start=2))
        # los "Estimado" van al final: cualquier dato real (de Kobo o del Excel) para ese día les gana
        filas.sort(key=lambda t: normalizar_clave(str((t[1] + [None] * 4)[3] or "")).startswith("estimad"))
        for n, fila in filas:
            fila = (fila + [None] * 5)[:5]
            if not any(x not in (None, "") for x in fila[:3]):
                continue                                   # fila vacía
            fecha = fecha_excel(fila[0])
            punto = catalogo.get(normalizar_clave(fila[1] or ""))
            try:
                mm = float(str(fila[2]).replace(",", ".")) if fila[2] not in (None, "") else None
            except ValueError:
                mm = None
            if not fecha or not punto or mm is None or mm < 0 or mm > 300:
                invalidos.append(n)
                continue
            m, co, ca, inf = punto
            clave = (normalizar_clave(m), normalizar_clave(co), normalizar_clave(ca), normalizar_clave(inf), fecha)
            fuente = str(fila[3] or "Carga manual").strip()
            if clave in vistos:
                repetidos += 1                             # repetido en el Excel: vale el primero
                continue
            if clave in claves_kobo:
                if normalizar_clave(fuente).startswith("correcc"):
                    # "Corrección": el dato revisado reemplaza al de Kobo (por ejemplo, un dato corrido un día)
                    for d in datos:
                        if (normalizar_clave(d["municipio"]), normalizar_clave(d["comunidad"]), normalizar_clave(d["caserio"]),
                                normalizar_clave(d["responsable"]), d["fecha"]) == clave:
                            d["mm_kobo"], d["mm"], d["fuente"] = d["mm"], mm, fuente
                    vistos.add(clave)
                    corregidos += 1
                else:
                    repetidos += 1                         # ya está en Kobo: se conserva Kobo
                continue
            vistos.add(clave)
            datos.append({"municipio": m, "comunidad": co, "caserio": ca, "responsable": inf,
                          "fecha": fecha, "mm": mm, "fuente": fuente})
            agregados += 1
        print(f"Cargas manuales: {agregados} registros agregados desde {ARCHIVO_MANUAL}; "
              f"{repetidos} ya estaban en Kobo o repetidos (se conserva Kobo); "
              f"{corregidos} datos de Kobo corregidos (fuente 'Corrección').")
        if invalidos:
            print(f"AVISO: {len(invalidos)} filas del Excel no se cargaron por fecha, punto o lluvia no válidos. "
                  f"Filas: {', '.join(map(str, invalidos[:50]))}{' ...' if len(invalidos) > 50 else ''}")
    except Exception as e:                                  # un Excel dañado no debe detener la actualización de Kobo
        print(f"AVISO: no se pudo leer {ARCHIVO_MANUAL}: {e}. Se publican solo los datos de Kobo.")


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

# Protección: si Kobo devolvió pocos registros (error, límite o cambio en la boleta),
# no se borra lo que ya está publicado. La tarea queda en rojo para que se revise.
anterior = 0
try:
    anterior = int(json.load(open("datos.json", encoding="utf-8")).get("total_registros", 0))
except Exception:
    pass
if anterior and len(datos) < anterior * (1 - CAIDA_MAXIMA):
    print(f"ERROR: los registros bajarían de {anterior} a {len(datos)} (más de {int(CAIDA_MAXIMA * 100)} %). "
          f"No se sobrescribe datos.json. Revisa Kobo o, si la baja es correcta (limpieza de datos), "
          f"ejecuta la tarea con FORZAR=1.")
    if not os.environ.get("FORZAR"):
        sys.exit(1)

with open("datos.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

print(f"OK: {len(datos)} registros")

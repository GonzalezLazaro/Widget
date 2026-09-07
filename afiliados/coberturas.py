"""Catálogo de coberturas especiales (API CoberturaTipo).

Reemplaza al catálogo local de preexistencias que se eliminó en el commit
`refactor: coberturas especiales por ID de Gecros`. El match contra Gecros
es por `id_externo`.
"""
from datetime import date

import requests
from django.conf import settings
from django.core.cache import cache

from .utils import parsear_coberturas

URL_CATALOGO = "https://gestionafiliaciones.nobissalud.com:5003/api/CoberturaTipo"
ID_CATEGORIA_AGUDA = 4
MESES_FALLBACK = 3

def periodo_proximo(hoy=None):
    """Periodo "YYYYMM" del mes siguiente: el que se cotiza."""
    hoy = hoy or date.today()
    anio, mes = hoy.year, hoy.month + 1
    if mes > 12:
        anio, mes = anio + 1, 1
    return f"{anio}{mes:02d}"

def periodo_anterior(periodo):
    """Periodo "YYYYMM" inmediatamente anterior al dado."""
    anio, mes = int(periodo[:4]), int(periodo[4:])
    mes -= 1
    if mes < 1:
        anio, mes = anio - 1, 12
    return f"{anio}{mes:02d}"

# De peor a mejor: el semáforo de una cápita es el peor de sus coberturas.
ORDEN_SEMAFORO = {"rojo": 0, "amarillo": 1, "verde": 2}

def semaforo(cob):
    """Color de una cobertura. Se deriva de los booleanos, no de categoria_nombre.

    Verificado contra el catálogo: los booleanos y `categoria_nombre` coinciden
    exacto (Roja=41, Amarilla=28, Verde+Aguda=45), pero los booleanos son la
    fuente semántica y no dependen de un string.
    """
    if not cob.get("permite_ingreso"):
        return "rojo"
    if cob.get("requiere_empresa_cliente"):
        return "amarillo"
    return "verde"

def es_pago_unico(cob):
    """Las "Aguda" se pagan una vez: no son sobrecuota mensual."""
    return cob.get("id_categoria") == ID_CATEGORIA_AGUDA

def normalizar(cob):
    """Registro crudo del catálogo -> el shape que consumen vista, template y JS."""
    return {
        "id": cob.get("id_externo"),
        "nombre": cob.get("cbtNombre") or "",
        "categoria": cob.get("categoria_nombre") or "",
        "semaforo": semaforo(cob),
        "valor": cob.get("valor_actual") or 0,
        "pago_unico": es_pago_unico(cob),
        "mensaje": cob.get("mensaje_rechazo") or "",
        "activo": cob.get("cbtActivo") == 1,
        "sin_catalogo": False,
    }

def semaforo_grupo(coberturas):
    """Peor semáforo de una cápita.

    Las que no resolvieron contra el catálogo no participan del cálculo: se ven
    al abrir el modal, que es donde el agente puede accionarlas. Devuelve
    'gris' sólo si ninguna resolvió, y None si la cápita no tiene ninguna.
    """
    if not coberturas:
        return None
    resueltas = [c for c in coberturas if not c.get("sin_catalogo")]
    if not resueltas:
        return "gris"
    return min((c["semaforo"] for c in resueltas),
               key=lambda s: ORDEN_SEMAFORO.get(s, 99))

def indice_por_id_externo(catalogo):
    """Mapa id -> cobertura normalizada.

    Trabaja sobre lo que devuelve `obtener_catalogo()`, que ya viene
    normalizado. Ante id repetido gana la activa: en el catálogo real el
    id_externo 1 aparece dos veces, como "FALSEO B_RINITIS" (activa) y como
    "NO USAR- COBERTURA GENERAL" (inactiva).
    """
    indice = {}
    for cob in catalogo:
        clave = cob.get("id")
        if clave is None:
            continue
        previo = indice.get(clave)
        if previo is None or (cob.get("activo") and not previo.get("activo")):
            indice[clave] = cob
    return indice

def resolver(ids_gecros, nombres, indice):
    """Coberturas crudas de Gecros -> lista normalizada.

    Lo que no está en el catálogo se conserva con el nombre que mandó Gecros y
    semáforo gris: el agente tiene que ver que la cobertura existe aunque no se
    pueda tarifar.
    """
    resueltas = []
    for cruda in parsear_coberturas(ids_gecros, nombres):
        del_catalogo = indice.get(cruda["id"])
        if del_catalogo is None:
            resueltas.append({
                "id": cruda["id"], "nombre": cruda["nombre"], "categoria": "",
                "semaforo": "gris", "valor": 0, "pago_unico": False,
                "mensaje": "", "activo": False, "sin_catalogo": True,
            })
        else:
            # Copia: dos cápitas con la misma cobertura no deben compartir el
            # mismo objeto, porque el JS las trata como entradas independientes.
            resueltas.append(dict(del_catalogo))
    return resueltas

CACHE_KEY_CATALOGO = "coberturas_catalogo"
TTL_VIGENTE = 6 * 3600      # precios del periodo próximo ya cargados
TTL_FALLBACK = 15 * 60      # se está usando un periodo viejo: reintentar pronto
TTL_ERROR = 60              # catálogo caído: no martillar la API en cada request

def _clave_cache(proximo=None):
    """Clave de cache del catálogo, atada al periodo próximo.

    Incluir el periodo evita que, al cruzar de mes, se siga sirviendo una
    entrada cacheada con el `periodo_proximo()` del mes anterior.
    """
    if proximo is None:
        proximo = periodo_proximo()
    return f"{CACHE_KEY_CATALOGO}_{proximo}"

def _pedir_periodo(periodo):
    """Una consulta al catálogo. Devuelve [] si la respuesta no es una lista.

    Filtra los elementos que no son dicts: la promesa de `obtener_catalogo`
    es no levantar nunca, y un `[null]` o un `["error"]` haría estallar el
    `.get()` de más abajo con un AttributeError que el `except` no cubre.
    """
    respuesta = requests.get(URL_CATALOGO, params={"periodo": periodo},
                             timeout=settings.REQUESTS_TIMEOUT)
    respuesta.raise_for_status()
    datos = respuesta.json()
    if not isinstance(datos, list):
        return []
    return [c for c in datos if isinstance(c, dict)]

def obtener_catalogo():
    """(coberturas, periodo_usado, precios_vigentes).

    Pide el periodo próximo. Si ningún registro trae `valor_actual`, los precios
    del mes que viene todavía no se cargaron: retrocede mes a mes hasta
    MESES_FALLBACK veces y avisa con precios_vigentes=False.

    Nunca levanta: ante cualquier error devuelve el catálogo normalizado que se
    haya podido bajar (o vacío si ni eso) para que la vista de retención siga
    cargando. El resultado degradado también se cachea con TTL_ERROR para no
    martillar la API en cada request mientras el catálogo está caído.
    """
    proximo = periodo_proximo()
    clave = _clave_cache(proximo)

    cacheado = cache.get(clave)
    if cacheado:
        return cacheado["coberturas"], cacheado["periodo"], cacheado["precios_vigentes"]

    periodo = proximo
    datos = []

    try:
        for _ in range(MESES_FALLBACK + 1):
            datos = _pedir_periodo(periodo)
            if any(c.get("valor_actual") for c in datos):
                break
            periodo = periodo_anterior(periodo)
        else:
            print("Ningún periodo del catálogo de coberturas tiene precios cargados.")
            # `datos` trae el último periodo consultado: nombres, categorías y
            # booleanos válidos, sólo sin precios. Se normaliza igual para no
            # perder los semáforos (en particular el 🔴 de permite_ingreso=False).
            coberturas = [normalizar(c) for c in datos]
            cache.set(
                clave,
                {"coberturas": coberturas, "periodo": proximo, "precios_vigentes": False},
                TTL_ERROR,
            )
            return coberturas, proximo, False
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"Error al consultar el catálogo de coberturas: {e}")
        cache.set(
            clave,
            {"coberturas": [], "periodo": proximo, "precios_vigentes": False},
            TTL_ERROR,
        )
        return [], proximo, False

    coberturas = [normalizar(c) for c in datos]
    vigentes = periodo == proximo
    cache.set(
        clave,
        {"coberturas": coberturas, "periodo": periodo, "precios_vigentes": vigentes},
        TTL_VIGENTE if vigentes else TTL_FALLBACK,
    )
    return coberturas, periodo, vigentes

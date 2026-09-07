"""Catálogo de coberturas especiales (API CoberturaTipo).

Reemplaza al catálogo local de preexistencias que se eliminó en el commit
`refactor: coberturas especiales por ID de Gecros`. El match contra Gecros
es por `id_externo`.
"""
from datetime import date

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

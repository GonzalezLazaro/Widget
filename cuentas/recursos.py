"""
Catálogo de recursos protegibles por RBAC — fuente ÚNICA de verdad.

Lo consumen: el gate (permisos.py / decoradores.py), la pantalla de superadmin
(views_admin.py) y la validación de datos. Nadie debe hardcodear estas keys sueltas.

Cada entrada: recurso_key -> (etiqueta_humana, tipo, seccion)
  tipo:    "vista" | "accion"
  seccion: agrupador para la UI de la matriz
"""
from collections import OrderedDict

RECURSOS = OrderedDict([
    ("vista.atencion",            ("Ver pantalla Atención",           "vista",  "Vistas")),
    ("vista.retencion",           ("Ver pantalla Retención",          "vista",  "Vistas")),
    ("vista.mesa",                ("Ver pantalla Mesa de Entrada",    "vista",  "Vistas")),
    ("accion.cotizar",            ("Cotizar",                         "accion", "Retención")),
    ("accion.enviar_bonificacion",("Enviar bonificación a Gecros",    "accion", "Retención")),
    ("accion.evaluar_bonificacion",("Evaluar reglas comerciales (RC)", "accion", "Retención")),
    ("accion.crear_expediente",   ("Crear expediente",                "accion", "Mesa de Entrada")),
    ("accion.subir_archivo",      ("Subir archivo a expediente",      "accion", "Mesa de Entrada")),
    ("accion.crear_remito",       ("Crear remito",                    "accion", "Mesa de Entrada")),
    ("accion.asignar_generador",  ("Asignar generador a expediente",  "accion", "Mesa de Entrada")),
])

RECURSO_KEYS = frozenset(RECURSOS)

# Recurso implícito de la pantalla de administración. NO va en RECURSOS
# (no se asigna a grupos): solo el superadmin lo satisface.
ADMIN_RECURSO = "admin.rbac"


def es_recurso_valido(key: str) -> bool:
    return key in RECURSO_KEYS


def catalogo_serializable():
    """Lista de dicts para hidratar la UI de superadmin."""
    return [
        {"key": k, "etiqueta": v[0], "tipo": v[1], "seccion": v[2]}
        for k, v in RECURSOS.items()
    ]

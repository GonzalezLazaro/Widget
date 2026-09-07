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

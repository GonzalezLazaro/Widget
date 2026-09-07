# Semáforo de Coberturas Especiales — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cada integrante del grupo familiar muestre un semáforo según su cobertura especial, con un modal de detalle donde el agente pueda agregar coberturas por búsqueda, y que el valor de esas coberturas se sume a la cuota a cotizar.

**Architecture:** Un módulo nuevo `afiliados/coberturas.py` concentra toda la regla de negocio en funciones puras (semáforo, resolución por `id_externo`, pago único vs mensual) más un único fetch aislado y cacheado contra la API de `CoberturaTipo`. La vista de retención resuelve el estado inicial server-side; un endpoint proxy sirve el catálogo al buscador del front. El JavaScript sólo hace aritmética y pintado.

**Tech Stack:** Django 5.1.3, `requests`, cache de Django (LocMemCache), JS vanilla (no hay jQuery ni select2 en este template — se quitaron en `a8ad4a4`).

**Spec:** `docs/superpowers/specs/2026-09-07-coberturas-especiales-semaforo-design.md`

## Global Constraints

- Rama de trabajo: `feat/coberturas-semaforo` (ya creada, sobre `refactor/coberturas-id-gecros`). No commitear en `master`.
- Todo request saliente usa `timeout=settings.REQUESTS_TIMEOUT`. Sin excepciones — hubo un incidente de 503 por hilos de mod_wsgi clavados.
- **La vista de retención nunca debe romperse por una API externa.** Ya acumuló dos 500 por eso (`aef36dc`, `51f1d3c`). `obtener_catalogo()` captura todo y devuelve catálogo vacío.
- Archivos `.py` y `.html` del repo usan **CRLF**. `core.autocrlf=true` normaliza al commitear, pero al editar con scripts hay que preservar `\r\n`.
- Los tests corren con `python manage.py test afiliados`.
- Match Gecros → catálogo: **sólo `id_externo`**, exacto. Sin fallback por nombre.
- URL del catálogo: `https://gestionafiliaciones.nobissalud.com:5003/api/CoberturaTipo` — el certificado TLS **valida bien**, no hace falta `verify=False`.
- `ID_CATEGORIA_AGUDA = 4` (categoría "Aguda" = pago único, no se suma a la cuota).
- `MESES_FALLBACK = 3`.

---

### Task 1: Periodos

**Files:**
- Create: `afiliados/coberturas.py`
- Create: `afiliados/test_coberturas.py`

**Interfaces:**
- Consumes: nada.
- Produces: `periodo_proximo(hoy=None) -> str` ("YYYYMM" del mes siguiente), `periodo_anterior(periodo: str) -> str`.

- [ ] **Step 1: Write the failing test**

Crear `afiliados/test_coberturas.py`:

```python
from datetime import date

from django.test import SimpleTestCase

from afiliados.coberturas import periodo_anterior, periodo_proximo


class PeriodoTests(SimpleTestCase):
    """El cotizador pide siempre el periodo siguiente al actual."""

    def test_periodo_proximo(self):
        self.assertEqual(periodo_proximo(date(2026, 9, 7)), "202610")

    def test_periodo_proximo_cruza_anio(self):
        self.assertEqual(periodo_proximo(date(2026, 12, 15)), "202701")

    def test_periodo_anterior(self):
        self.assertEqual(periodo_anterior("202610"), "202609")

    def test_periodo_anterior_cruza_anio(self):
        self.assertEqual(periodo_anterior("202601"), "202512")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test afiliados.test_coberturas -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'afiliados.coberturas'`.

Si en cambio dice `Ran 0 tests`, el descubrimiento del archivo falló: verificar que el nombre sea `test_coberturas.py` (el runner de Django descubre `test*.py`).

- [ ] **Step 3: Write minimal implementation**

Crear `afiliados/coberturas.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test afiliados.test_coberturas -v 2`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add afiliados/coberturas.py afiliados/test_coberturas.py
git commit -m "feat: modulo de coberturas con calculo de periodos"
```

---

### Task 2: Semáforo y clasificación

**Files:**
- Modify: `afiliados/coberturas.py`
- Modify: `afiliados/test_coberturas.py`

**Interfaces:**
- Consumes: nada de Task 1 más allá del módulo.
- Produces: `semaforo(cob) -> 'rojo'|'amarillo'|'verde'`, `es_pago_unico(cob) -> bool`, `normalizar(cob) -> dict`, `semaforo_grupo(coberturas) -> 'rojo'|'amarillo'|'verde'|'gris'|None`, constante `ORDEN_SEMAFORO`.

El dict que devuelve `normalizar` es el contrato que consumen la vista, el template y el JS:

```python
{"id": 643, "nombre": "AG_AMIGDALITIS SIN TRATAR", "categoria": "Aguda",
 "semaforo": "verde", "valor": 425567.0, "pago_unico": True,
 "mensaje": "Esta cobertura es válida para...", "activo": True}
```

- [ ] **Step 1: Write the failing test**

Agregar a `afiliados/test_coberturas.py` (y sumar los imports al `from afiliados.coberturas import ...` de arriba):

```python
from afiliados.coberturas import (
    es_pago_unico,
    normalizar,
    periodo_anterior,
    periodo_proximo,
    semaforo,
    semaforo_grupo,
)


def cob(**kwargs):
    """Registro del catálogo con valores por defecto benignos."""
    base = {
        "cbtId": 1, "cbtNombre": "X", "cbtActivo": 1, "id_externo": 100,
        "id_categoria": None, "categoria_nombre": "", "permite_ingreso": True,
        "requiere_empresa_cliente": False, "mensaje_rechazo": "", "valor_actual": 0.0,
    }
    base.update(kwargs)
    return base


class SemaforoTests(SimpleTestCase):
    """Se deriva de los booleanos, no del string de categoria_nombre."""

    def test_rojo_cuando_no_permite_ingreso(self):
        self.assertEqual(semaforo(cob(permite_ingreso=False)), "rojo")

    def test_amarillo_cuando_requiere_empresa_cliente(self):
        self.assertEqual(semaforo(cob(requiere_empresa_cliente=True)), "amarillo")

    def test_rojo_gana_sobre_amarillo(self):
        malo = cob(permite_ingreso=False, requiere_empresa_cliente=True)
        self.assertEqual(semaforo(malo), "rojo")

    def test_verde_por_defecto(self):
        self.assertEqual(semaforo(cob()), "verde")


class PagoUnicoTests(SimpleTestCase):
    def test_aguda_es_pago_unico(self):
        self.assertTrue(es_pago_unico(cob(id_categoria=4, categoria_nombre="Aguda")))

    def test_el_resto_no(self):
        for categoria in (None, 1, 2, 3):
            with self.subTest(categoria=categoria):
                self.assertFalse(es_pago_unico(cob(id_categoria=categoria)))


class NormalizarTests(SimpleTestCase):
    def test_shape(self):
        crudo = cob(cbtNombre="AG_X", id_externo=643, id_categoria=4,
                    categoria_nombre="Aguda", mensaje_rechazo="pago UNICO",
                    valor_actual=425567.0)
        self.assertEqual(normalizar(crudo), {
            "id": 643, "nombre": "AG_X", "categoria": "Aguda", "semaforo": "verde",
            "valor": 425567.0, "pago_unico": True, "mensaje": "pago UNICO",
            "activo": True, "sin_catalogo": False,
        })

    def test_valor_nulo_cae_a_cero(self):
        self.assertEqual(normalizar(cob(valor_actual=None))["valor"], 0)

    def test_inactivo(self):
        self.assertFalse(normalizar(cob(cbtActivo=0))["activo"])


class SemaforoGrupoTests(SimpleTestCase):
    """Manda el peor de las coberturas que sí resolvieron."""

    def test_sin_coberturas(self):
        self.assertIsNone(semaforo_grupo([]))

    def test_manda_el_peor(self):
        lista = [{"semaforo": "verde"}, {"semaforo": "rojo"}, {"semaforo": "amarillo"}]
        self.assertEqual(semaforo_grupo(lista), "rojo")

    def test_amarillo_gana_a_verde(self):
        self.assertEqual(semaforo_grupo([{"semaforo": "verde"}, {"semaforo": "amarillo"}]), "amarillo")

    def test_una_resuelta_gana_a_la_sin_catalogo(self):
        lista = [{"semaforo": "gris", "sin_catalogo": True}, {"semaforo": "verde"}]
        self.assertEqual(semaforo_grupo(lista), "verde")

    def test_todas_sin_catalogo(self):
        lista = [{"semaforo": "gris", "sin_catalogo": True},
                 {"semaforo": "gris", "sin_catalogo": True}]
        self.assertEqual(semaforo_grupo(lista), "gris")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test afiliados.test_coberturas -v 2`
Expected: FAIL — `ImportError: cannot import name 'semaforo' from 'afiliados.coberturas'`.

- [ ] **Step 3: Write minimal implementation**

Agregar a `afiliados/coberturas.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test afiliados.test_coberturas -v 2`
Expected: PASS, 18 tests.

- [ ] **Step 5: Commit**

```bash
git add afiliados/coberturas.py afiliados/test_coberturas.py
git commit -m "feat: semaforo y clasificacion de coberturas"
```

---

### Task 3: Resolución contra Gecros

**Files:**
- Modify: `afiliados/coberturas.py`
- Modify: `afiliados/test_coberturas.py`

**Interfaces:**
- Consumes: `normalizar()` de Task 2; `parsear_coberturas(ids_gecros, nombres)` de `afiliados/utils.py` (ya existe — devuelve `[{"id": int, "nombre": str}]` con pareo posicional y `.strip()`).
- Produces: `indice_por_id_externo(catalogo) -> dict`, `resolver(ids_gecros, nombres, indice) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

Agregar a `afiliados/test_coberturas.py` (sumar `indice_por_id_externo` y `resolver` al import):

```python
class IndiceTests(SimpleTestCase):
    """El índice trabaja sobre coberturas ya normalizadas."""

    def test_indexa_por_id(self):
        indice = indice_por_id_externo([normalizar(cob(id_externo=643)),
                                        normalizar(cob(id_externo=174))])
        self.assertEqual(sorted(indice), [174, 643])

    def test_id_duplicado_gana_la_activa(self):
        """En el catálogo real id_externo=1 aparece activo e inactivo."""
        indice = indice_por_id_externo([
            normalizar(cob(id_externo=1, cbtActivo=0, cbtNombre="NO USAR- COBERTURA GENERAL")),
            normalizar(cob(id_externo=1, cbtActivo=1, cbtNombre="FALSEO B_RINITIS")),
        ])
        self.assertEqual(indice[1]["nombre"], "FALSEO B_RINITIS")


class ResolverTests(SimpleTestCase):
    def setUp(self):
        self.indice = indice_por_id_externo([
            normalizar(cob(id_externo=643, cbtNombre="AG_AMIGDALITIS", id_categoria=4,
                           categoria_nombre="Aguda", valor_actual=425567.0)),
            normalizar(cob(id_externo=551, cbtNombre="FALSEO_V_ANEMIA", id_categoria=1,
                           categoria_nombre="Verde", valor_actual=20247.0)),
        ])

    def test_resuelve_por_id_externo(self):
        r = resolver("643", "AG_AMIGDALITIS", self.indice)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["nombre"], "AG_AMIGDALITIS")
        self.assertEqual(r[0]["valor"], 425567.0)
        self.assertTrue(r[0]["pago_unico"])
        self.assertFalse(r[0]["sin_catalogo"])

    def test_sin_match_conserva_el_nombre_de_gecros(self):
        """Caso real del afiliado 38407571: sus IDs no están en el catálogo."""
        r = resolver("471", "ANTICONCEPTIVOS ORALES", self.indice)
        self.assertEqual(r, [{
            "id": 471, "nombre": "ANTICONCEPTIVOS ORALES", "categoria": "",
            "semaforo": "gris", "valor": 0, "pago_unico": False,
            "mensaje": "", "activo": False, "sin_catalogo": True,
        }])

    def test_mezcla_resuelta_y_sin_match(self):
        r = resolver("643, 471", "AG_AMIGDALITIS, ANTICONCEPTIVOS ORALES", self.indice)
        self.assertEqual([c["sin_catalogo"] for c in r], [False, True])

    def test_gecros_manda_padding_en_los_nombres(self):
        r = resolver("551", "FALSEO_V_ANEMIA          ", self.indice)
        self.assertEqual(r[0]["nombre"], "FALSEO_V_ANEMIA")

    def test_sin_coberturas(self):
        self.assertEqual(resolver(None, None, self.indice), [])

    def test_indice_vacio_todo_sin_catalogo(self):
        """Si el catálogo está caído, nada resuelve pero nada rompe."""
        r = resolver("643", "AG_AMIGDALITIS", {})
        self.assertTrue(r[0]["sin_catalogo"])
        self.assertEqual(r[0]["valor"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test afiliados.test_coberturas -v 2`
Expected: FAIL — `ImportError: cannot import name 'indice_por_id_externo'`.

- [ ] **Step 3: Write minimal implementation**

Agregar el import al inicio de `afiliados/coberturas.py`:

```python
from .utils import parsear_coberturas
```

y las funciones:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test afiliados.test_coberturas -v 2`
Expected: PASS, 26 tests.

- [ ] **Step 5: Commit**

```bash
git add afiliados/coberturas.py afiliados/test_coberturas.py
git commit -m "feat: resolucion de coberturas de Gecros contra el catalogo"
```

---

### Task 4: Fetch del catálogo con fallback de periodo y cache

**Files:**
- Modify: `afiliados/coberturas.py`
- Modify: `afiliados/test_coberturas.py`

**Interfaces:**
- Consumes: `periodo_proximo`, `periodo_anterior`, `normalizar` de Tasks 1-2.
- Produces: `obtener_catalogo() -> (list[dict], str, bool)` — `(coberturas_normalizadas, periodo_usado, precios_vigentes)`. Devuelve **los 573 registros**, activos e inactivos: `resolver()` necesita los inactivos porque una cobertura que manda Gecros se considera vigente sin importar `cbtActivo`. El filtrado por `activo` lo hace el endpoint proxy (Task 5).
- Produces: constantes `CACHE_KEY_CATALOGO`, `TTL_VIGENTE`, `TTL_FALLBACK`.

- [ ] **Step 1: Write the failing test**

Agregar a `afiliados/test_coberturas.py` (y arriba `from unittest.mock import Mock, patch` más `from django.core.cache import cache`):

```python
class ObtenerCatalogoTests(SimpleTestCase):
    """Un periodo sin precios cargados devuelve todos los valor_actual en 0."""

    def setUp(self):
        cache.delete(CACHE_KEY_CATALOGO)

    def tearDown(self):
        cache.delete(CACHE_KEY_CATALOGO)

    def _respuesta(self, payload):
        r = Mock()
        r.raise_for_status.return_value = None
        r.json.return_value = payload
        return r

    def test_usa_el_periodo_proximo_si_tiene_precios(self):
        con_precios = [cob(id_externo=1, valor_actual=1000.0)]
        with patch("afiliados.coberturas.requests.get",
                   return_value=self._respuesta(con_precios)) as get:
            coberturas, periodo, vigentes = obtener_catalogo()

        self.assertEqual(periodo, periodo_proximo())
        self.assertTrue(vigentes)
        self.assertEqual(coberturas[0]["valor"], 1000.0)
        self.assertEqual(get.call_count, 1)

    def test_cae_al_periodo_anterior_si_no_hay_precios(self):
        sin_precios = [cob(id_externo=1, valor_actual=0.0)]
        con_precios = [cob(id_externo=1, valor_actual=999.0)]
        with patch("afiliados.coberturas.requests.get",
                   side_effect=[self._respuesta(sin_precios),
                                self._respuesta(con_precios)]):
            coberturas, periodo, vigentes = obtener_catalogo()

        self.assertEqual(periodo, periodo_anterior(periodo_proximo()))
        self.assertFalse(vigentes)
        self.assertEqual(coberturas[0]["valor"], 999.0)

    def test_ningun_periodo_con_precios(self):
        sin_precios = self._respuesta([cob(id_externo=1, valor_actual=0.0)])
        with patch("afiliados.coberturas.requests.get", return_value=sin_precios):
            coberturas, periodo, vigentes = obtener_catalogo()

        self.assertEqual(coberturas, [])
        self.assertFalse(vigentes)

    def test_api_caida_no_rompe(self):
        """La vista de retención tiene que cargar igual."""
        import requests as _requests
        with patch("afiliados.coberturas.requests.get",
                   side_effect=_requests.exceptions.ConnectionError("boom")):
            coberturas, periodo, vigentes = obtener_catalogo()

        self.assertEqual(coberturas, [])
        self.assertEqual(periodo, periodo_proximo())
        self.assertFalse(vigentes)

    def test_respuesta_no_lista_no_rompe(self):
        with patch("afiliados.coberturas.requests.get",
                   return_value=self._respuesta({"error": "nope"})):
            coberturas, _, vigentes = obtener_catalogo()

        self.assertEqual(coberturas, [])
        self.assertFalse(vigentes)

    def test_segunda_llamada_usa_cache(self):
        con_precios = [cob(id_externo=1, valor_actual=1000.0)]
        with patch("afiliados.coberturas.requests.get",
                   return_value=self._respuesta(con_precios)) as get:
            obtener_catalogo()
            obtener_catalogo()

        self.assertEqual(get.call_count, 1)

    def test_devuelve_tambien_los_inactivos(self):
        """resolver() los necesita: Gecros manda coberturas dadas de baja."""
        datos = [cob(id_externo=1, cbtActivo=0, valor_actual=0.0),
                 cob(id_externo=2, cbtActivo=1, valor_actual=500.0)]
        with patch("afiliados.coberturas.requests.get",
                   return_value=self._respuesta(datos)):
            coberturas, _, _ = obtener_catalogo()

        self.assertEqual(len(coberturas), 2)
        self.assertEqual([c["activo"] for c in coberturas], [False, True])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test afiliados.test_coberturas -v 2`
Expected: FAIL — `ImportError: cannot import name 'obtener_catalogo'`.

- [ ] **Step 3: Write minimal implementation**

Agregar los imports al inicio de `afiliados/coberturas.py`:

```python
import requests
from django.conf import settings
from django.core.cache import cache
```

y al final:

```python
CACHE_KEY_CATALOGO = "coberturas_catalogo"
TTL_VIGENTE = 6 * 3600      # precios del periodo próximo ya cargados
TTL_FALLBACK = 15 * 60      # se está usando un periodo viejo: reintentar pronto


def _pedir_periodo(periodo):
    """Una consulta al catálogo. Devuelve [] si la respuesta no es una lista."""
    respuesta = requests.get(URL_CATALOGO, params={"periodo": periodo},
                             timeout=settings.REQUESTS_TIMEOUT)
    respuesta.raise_for_status()
    datos = respuesta.json()
    return datos if isinstance(datos, list) else []


def obtener_catalogo():
    """(coberturas, periodo_usado, precios_vigentes).

    Pide el periodo próximo. Si ningún registro trae `valor_actual`, los precios
    del mes que viene todavía no se cargaron: retrocede mes a mes hasta
    MESES_FALLBACK veces y avisa con precios_vigentes=False.

    Nunca levanta: ante cualquier error devuelve catálogo vacío para que la
    vista de retención siga cargando.
    """
    cacheado = cache.get(CACHE_KEY_CATALOGO)
    if cacheado:
        return cacheado["coberturas"], cacheado["periodo"], cacheado["precios_vigentes"]

    proximo = periodo_proximo()
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
            return [], proximo, False
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"Error al consultar el catálogo de coberturas: {e}")
        return [], proximo, False

    coberturas = [normalizar(c) for c in datos]
    vigentes = periodo == proximo
    cache.set(
        CACHE_KEY_CATALOGO,
        {"coberturas": coberturas, "periodo": periodo, "precios_vigentes": vigentes},
        TTL_VIGENTE if vigentes else TTL_FALLBACK,
    )
    return coberturas, periodo, vigentes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test afiliados.test_coberturas -v 2`
Expected: PASS, 33 tests.

- [ ] **Step 5: Verificar contra la API real**

Run:

```bash
python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from afiliados.coberturas import obtener_catalogo
c, p, v = obtener_catalogo()
print(f'{len(c)} coberturas, periodo {p}, precios_vigentes={v}')
print(f'activas: {sum(1 for x in c if x[\"activo\"])}')
print(f'con valor: {sum(1 for x in c if x[\"valor\"])}')
"
```

Expected: `573 coberturas, periodo 202610, precios_vigentes=True`, `activas: 156`, `con valor: 114`.

- [ ] **Step 6: Commit**

```bash
git add afiliados/coberturas.py afiliados/test_coberturas.py
git commit -m "feat: fetch del catalogo con fallback de periodo y cache"
```

---

### Task 5: Endpoint proxy del catálogo

**Files:**
- Modify: `afiliados/views.py:7` (imports) y al final del archivo
- Modify: `afiliados/urls.py`
- Modify: `afiliados/test_coberturas.py`

**Interfaces:**
- Consumes: `obtener_catalogo()` de Task 4.
- Produces: ruta `afiliados:coberturas_catalogo` en `/widget/api/coberturas/`, vista `coberturas_catalogo(request)`.

Respuesta:

```json
{"periodo": "202610", "precios_vigentes": true, "coberturas": [ ...sólo activo=true... ]}
```

- [ ] **Step 1: Write the failing test**

Agregar a `afiliados/test_coberturas.py`:

```python
from django.test import TestCase
from django.urls import reverse


@override_settings(WIDGET_AUTH_ENABLED=False)
class CatalogoEndpointTests(TestCase):
    """Sólo las activas: son las que el agente puede agregar desde el buscador.

    `requiere_permiso_json` hace passthrough con WIDGET_AUTH_ENABLED=False
    (ver `cuentas/decoradores.py`), así que el test no depende del entorno.
    """

    def setUp(self):
        cache.delete(CACHE_KEY_CATALOGO)

    def test_devuelve_solo_activas(self):
        catalogo = [normalizar(cob(id_externo=1, cbtActivo=0)),
                    normalizar(cob(id_externo=2, cbtActivo=1))]
        with patch("afiliados.views.obtener_catalogo",
                   return_value=(catalogo, "202610", True)):
            respuesta = self.client.get(reverse("afiliados:coberturas_catalogo"))

        self.assertEqual(respuesta.status_code, 200)
        cuerpo = respuesta.json()
        self.assertEqual(cuerpo["periodo"], "202610")
        self.assertTrue(cuerpo["precios_vigentes"])
        self.assertEqual([c["id"] for c in cuerpo["coberturas"]], [2])

    def test_catalogo_caido_devuelve_lista_vacia(self):
        with patch("afiliados.views.obtener_catalogo",
                   return_value=([], "202610", False)):
            respuesta = self.client.get(reverse("afiliados:coberturas_catalogo"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["coberturas"], [])
```

Y sumar al import de Django del archivo: `from django.test import SimpleTestCase, TestCase, override_settings`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test afiliados.test_coberturas -v 2`
Expected: FAIL — `NoReverseMatch: 'coberturas_catalogo' is not a valid view function or pattern name`.

- [ ] **Step 3: Write minimal implementation**

En `afiliados/views.py`, agregar el import cerca del de `.utils` (línea 7):

```python
from .coberturas import obtener_catalogo, resolver, semaforo_grupo
```

Agregar la vista al final del archivo, junto a las otras vistas `api/`:

```python
@requiere_permiso_json("accion.cotizar")
def coberturas_catalogo(request):
    """Catálogo de coberturas especiales para el buscador del modal.

    Sólo las activas: son las que el agente puede agregar. Las inactivas
    igual llegan a la vista de retención, porque Gecros puede tener cargada
    una cobertura dada de baja y hay que mostrarla.
    """
    coberturas, periodo, precios_vigentes = obtener_catalogo()
    return JsonResponse({
        "periodo": periodo,
        "precios_vigentes": precios_vigentes,
        "coberturas": [c for c in coberturas if c["activo"]],
    })
```

En `afiliados/urls.py`, sumar `coberturas_catalogo` al import de `.views` y agregar la ruta junto a `api/cotizar/`:

```python
    path('api/coberturas/', coberturas_catalogo, name='coberturas_catalogo'),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test afiliados.test_coberturas -v 2`
Expected: PASS, 35 tests.

- [ ] **Step 5: Verificar a mano**

Run: `python manage.py runserver` y en otra terminal:

```bash
curl -s "http://127.0.0.1:8000/widget/api/coberturas/" | python -c "import json,sys; d=json.load(sys.stdin); print(d['periodo'], d['precios_vigentes'], len(d['coberturas']))"
```

Expected: `202610 True 156`. Si devuelve 403, hace falta estar logueado — abrir la URL en el navegador con la sesión del widget iniciada.

- [ ] **Step 6: Commit**

```bash
git add afiliados/views.py afiliados/urls.py afiliados/test_coberturas.py
git commit -m "feat: endpoint proxy del catalogo de coberturas"
```

---

### Task 6: La vista de retención resuelve las coberturas

**Files:**
- Modify: `afiliados/views.py:700-706` (bloque de coberturas), `:626` (dict inicial), `:796` (contexto del render)

**Interfaces:**
- Consumes: `obtener_catalogo()`, `resolver()`, `semaforo_grupo()`, `indice_por_id_externo()`.
- Produces: en el contexto del template, cada `item` de `data` gana `Coberturas` (lista normalizada) y `Semaforo`; y el contexto gana `coberturas_periodo`, `coberturas_precios_vigentes` y `coberturas_iniciales`.

- [ ] **Step 1: Completar el import**

En `afiliados/views.py` línea 7 el import de `.coberturas` ya se agregó en Task 5. Sumarle `indice_por_id_externo`:

```python
from .coberturas import indice_por_id_externo, obtener_catalogo, resolver, semaforo_grupo
```

- [ ] **Step 2: Escribir el bloque en la vista**

En `afiliados/views.py`, antes de `resultados_combinados = []` (línea 488):

```python
                # Catálogo de coberturas especiales: una sola consulta para todo
                # el grupo. Si está caído el índice queda vacío, todas las
                # coberturas se muestran en gris y la vista carga igual.
                catalogo, coberturas_periodo, coberturas_precios_vigentes = obtener_catalogo()
                indice_coberturas = indice_por_id_externo(catalogo)
```

Reemplazar el bloque de coberturas (líneas 700-706, el que hoy llama a `parsear_coberturas`):

```python
                            # Coberturas especiales: se resuelven contra el
                            # catálogo por id_externo. Lo que no está queda en
                            # gris con el nombre que mandó Gecros.
                            patologia = data_p[0].get('cobertura_especial')
                            patologias_id = data_p[0].get('id_cobertura_especial')

                            afiliado_info['Coberturas'] = resolver(patologias_id, patologia, indice_coberturas)
                            afiliado_info['Semaforo'] = semaforo_grupo(afiliado_info['Coberturas'])
                            afiliado_info['Cobertura_especial'] = patologia or 'Sin cobertura especial'
```

En el dict inicial de `afiliado_info` (línea 626), sumar `Semaforo` junto a `Coberturas`:

```python
                        "Coberturas": [],
                        "Semaforo": None
```

En el `render` final (línea 796), agregar las tres claves nuevas:

```python
                return render(request, self.template_name, {
                    'data': resultados_combinados, 'data_aportes': all_aportes,
                    'data_fpago': forma_de_pago_bonif, "convenio_id": convenio_id,
                    "convenio_nom": condicion_inicial, "tipo_nom": tipo_beneficiario,
                    "grupo": grupo, "dni_titular": dni_titular,
                    "coberturas_periodo": coberturas_periodo,
                    "coberturas_precios_vigentes": coberturas_precios_vigentes,
                    "coberturas_iniciales": [i.get('Coberturas', []) for i in resultados_combinados],
                })
```

`coberturas_iniciales` viaja aparte de `data` porque el JS lo consume como un único bloque `json_script` y lo reparte por posición de fila.

- [ ] **Step 3: Verificar que la vista no rompe**

Run: `python manage.py check` → sin issues.
Run: `python manage.py test afiliados` → todo verde.

Abrir `/widget/retencion/38407571/`. La página tiene que cargar. Los chips todavía son los viejos (el template se toca en Task 7), pero no debe haber error 500.

- [ ] **Step 4: Commit**

```bash
git add afiliados/views.py
git commit -m "feat: la vista de retencion resuelve coberturas contra el catalogo"
```

---

### Task 7: Chip semáforo en el modal de cotización

**Files:**
- Modify: `afiliados/templates/rete_produccion.html` — CSS (líneas 587-637), `agregarFila()` (~984-1017), `quitarCobertura()` (~1019-1026), la celda de la tabla (~1049-1058)

**Interfaces:**
- Consumes: `item.Semaforo`, `item.Coberturas` y `coberturas_iniciales` del contexto (Task 6).
- Produces: `td._coberturas` (lista por cápita), `semaforoDe(lista)`, `pintarSemaforo(td)`, `totalesCoberturas()`, `recalcularCoberturas()`, `abrirCoberturas(btn)` (stub en esta task, se completa en Task 8), `fmtARS(n)`.

Sin infraestructura de tests JS en el repo: la verificación es a mano.

- [ ] **Step 1: Reemplazar el CSS de chips por el del semáforo**

Sustituir el bloque `.cob-lista` / `.cob-chip` / `.cob-chip-txt` / `.cob-chip-x` / `.cob-chip-x:hover` / `.cob-vacio` (líneas 587-637) por:

```css
        /* Coberturas especiales: semáforo por cápita. */
        .cob-semaforo {
            background: transparent;
            border: none;
            cursor: pointer;
            font-size: 15px;
            line-height: 1;
            padding: 0 4px;
        }

        .cob-rojo     { color: #c62828; }
        .cob-amarillo { color: #f9a825; }
        .cob-verde    { color: #2e7d32; }
        .cob-gris     { color: #9e9e9e; }
        .cob-nada     { color: #bdbdbd; font-weight: bold; }

        /* La usa el botón de quitar del modal de detalle (Task 8). */
        .cob-chip-x {
            background: transparent;
            border: none;
            color: #666;
            cursor: pointer;
            flex: 0 0 auto;
            font-size: 10px;
            line-height: 1;
            padding: 0;
        }

        .cob-chip-x:hover { color: #cc0000; }

        .cob-aviso-precios {
            background: #fff8e1;
            border: 1px solid #f9a825;
            border-radius: 4px;
            color: #7a5c00;
            font-size: 11px;
            margin: 4px 0;
            padding: 4px 8px;
        }
```

- [ ] **Step 2: Reemplazar la celda de las filas precargadas**

Sustituir el `<td>` de coberturas (líneas 1049-1058, el que hoy tiene `<div class="cob-lista">` con los chips) por:

```django
                                <td class="td-coberturas">
                                    <button type="button"
                                            class="cob-semaforo cob-{{ item.Semaforo|default:'nada' }}"
                                            onclick="abrirCoberturas(this)"
                                            title="Ver coberturas especiales">{% if item.Semaforo %}●{% else %}-{% endif %}</button>
                                </td>
```

- [ ] **Step 3: Actualizar `agregarFila()` y borrar `quitarCobertura()`**

En `agregarFila()`, reemplazar la celda de coberturas del template literal:

```javascript
                <td class="td-coberturas">
                    <button type="button" class="cob-semaforo cob-nada"
                            onclick="abrirCoberturas(this)"
                            title="Ver coberturas especiales">-</button>
                </td>
```

Después de `tbody.appendChild(newRow);`, inicializar el estado de la fila nueva:

```javascript
            newRow.querySelector('.td-coberturas')._coberturas = [];
```

Borrar la función `quitarCobertura()` entera: los chips que la usaban ya no existen. Quitar coberturas ahora se hace desde el modal de detalle (Task 8).

En `eliminarFila()`, agregar el recálculo al final:

```javascript
        function eliminarFila(button) {
            let row = button.parentNode.parentNode;
            row.parentNode.removeChild(row);
            recalcularCoberturas();
        }
```

- [ ] **Step 4: Agregar el bloque de estado y cálculo**

Reemplazar el `<script>` del final que hoy sólo tiene `document.addEventListener('DOMContentLoaded', actualizarPlanes);` (~línea 2554) por:

```html
    {{ coberturas_iniciales|json_script:"coberturas-iniciales" }}
    <script>
        // ===== Coberturas especiales =====
        // El estado de cada cápita vive en td._coberturas: propiedad del nodo,
        // no atributo. Sin escapado de JSON, y muere con la fila.
        const ORDEN_SEMAFORO = { rojo: 0, amarillo: 1, verde: 2 };
        const FMT_ARS = new Intl.NumberFormat('es-AR', {
            style: 'currency', currency: 'ARS', minimumFractionDigits: 0
        });

        function fmtARS(n) { return FMT_ARS.format(n || 0); }

        function celdasCoberturas() {
            return document.querySelectorAll('#secondary-modal td.td-coberturas');
        }

        // Manda el peor de las que resolvieron. Las sin catálogo no participan:
        // se ven al abrir el modal, que es donde el agente puede accionarlas.
        function semaforoDe(lista) {
            if (!lista || !lista.length) return null;
            const resueltas = lista.filter(function (c) { return !c.sin_catalogo; });
            if (!resueltas.length) return 'gris';
            return resueltas.map(function (c) { return c.semaforo; })
                .sort(function (a, b) { return ORDEN_SEMAFORO[a] - ORDEN_SEMAFORO[b]; })[0];
        }

        function pintarSemaforo(td) {
            const lista = td._coberturas || [];
            const color = semaforoDe(lista);
            const btn = td.querySelector('.cob-semaforo');
            if (!btn) return;
            btn.className = 'cob-semaforo cob-' + (color || 'nada');
            btn.textContent = color ? '●' : '-';
            btn.title = lista.length
                ? lista.length + ' cobertura(s) especial(es)'
                : 'Sin cobertura especial';
        }

        function totalesCoberturas() {
            let mensual = 0, unico = 0;
            celdasCoberturas().forEach(function (td) {
                (td._coberturas || []).forEach(function (c) {
                    if (c.pago_unico) unico += c.valor || 0;
                    else mensual += c.valor || 0;
                });
            });
            return { mensual: mensual, unico: unico };
        }

        function recalcularCoberturas() {
            celdasCoberturas().forEach(pintarSemaforo);
            const t = totalesCoberturas();
            const elMensual = document.getElementById('cob-total-mensual');
            const elUnico = document.getElementById('cob-total-unico');
            const filaUnico = document.getElementById('cob-fila-unico');
            if (elMensual) elMensual.textContent = fmtARS(t.mensual);
            if (elUnico) elUnico.textContent = fmtARS(t.unico);
            if (filaUnico) filaUnico.style.display = t.unico ? '' : 'none';
        }

        document.addEventListener('DOMContentLoaded', function () {
            const bloque = document.getElementById('coberturas-iniciales');
            const inicial = bloque ? JSON.parse(bloque.textContent) : [];
            document.querySelectorAll('#secondary-modal tbody tr').forEach(function (tr, i) {
                const td = tr.querySelector('td.td-coberturas');
                if (td) td._coberturas = inicial[i] || [];
            });
            recalcularCoberturas();
            actualizarPlanes();
        });
    </script>
```

- [ ] **Step 5: Verificar a mano**

Run: `python manage.py runserver`, abrir `/widget/retencion/38407571/` y el modal de cotización.

Verificar:
- Cada cápita muestra un círculo o un `-`, no los chips viejos.
- El titular (3 coberturas sin catálogo) muestra ⚪ gris.
- Consola del navegador sin errores.
- "Agregar capita" agrega una fila con `-` gris.
- El ✕ de eliminar fila sigue funcionando.
- En la consola: `totalesCoberturas()` devuelve `{mensual: 0, unico: 0}` para este afiliado.

- [ ] **Step 6: Commit**

```bash
git add afiliados/templates/rete_produccion.html
git commit -m "feat: chip semaforo por capita en el modal de cotizacion"
```

---

### Task 8: Modal de detalle con buscador

**Files:**
- Modify: `afiliados/templates/rete_produccion.html` — CSS nuevo, markup del modal después de `#secondary-modal`, y JS

**Interfaces:**
- Consumes: `td._coberturas`, `recalcularCoberturas()`, `fmtARS()` de Task 7; endpoint `/widget/api/coberturas/` de Task 5.
- Produces: `abrirCoberturas(btn)` (implementación real), `cerrarCoberturas()`, `renderCoberturasCapita()`, `buscarCoberturas()`, `agregarCobertura(id)`, `quitarCoberturaDeCapita(indice)`, `catalogoCoberturas` (array global).

- [ ] **Step 1: CSS del modal**

Agregar después del bloque `.cob-aviso-precios` de Task 7:

```css
        /* ===== Modal de detalle de coberturas (encima del de cotización) ===== */
        #coberturas-modal-overlay { z-index: 1050; }
        #coberturas-modal {
            z-index: 1100;
            width: 420px;
            max-height: 88vh;
            overflow-y: auto;
            padding: 12px;
            border-radius: 12px;
        }

        #coberturas-modal h3 {
            font-size: 14px;
            margin: 0 0 8px;
        }

        .cob-item {
            border-bottom: 1px solid #eee;
            display: flex;
            align-items: flex-start;
            gap: 6px;
            padding: 6px 0;
            text-align: left;
        }

        .cob-item-datos { flex: 1; min-width: 0; }
        .cob-item-nombre { font-size: 12px; font-weight: 600; }
        .cob-item-meta { color: #666; font-size: 11px; }
        .cob-item-msg { color: #7a5c00; font-size: 10px; margin-top: 2px; }
        .cob-item-valor { font-size: 12px; white-space: nowrap; }

        .cob-badge-unico {
            background: #e3f2fd;
            border-radius: 3px;
            color: #1565c0;
            font-size: 9px;
            padding: 1px 4px;
        }

        #cob-buscador {
            box-sizing: border-box;
            font-size: 12px;
            margin-top: 8px;
            padding: 5px;
            width: 100%;
        }

        #cob-resultados {
            max-height: 160px;
            overflow-y: auto;
        }

        .cob-resultado {
            border-bottom: 1px solid #f0f0f0;
            cursor: pointer;
            font-size: 12px;
            padding: 4px 2px;
            text-align: left;
        }

        .cob-resultado:hover { background: #f5f5f5; }

        .cob-subtotales {
            border-top: 2px solid #ddd;
            font-size: 12px;
            margin-top: 8px;
            padding-top: 6px;
            text-align: right;
        }
```

- [ ] **Step 2: Markup del modal**

Agregar inmediatamente después del cierre de `#secondary-modal` (antes del `<!-- ... -->` o del bloque que le siga):

```html
    <!-- Modal de detalle de coberturas especiales -->
    <div class="modal-overlay" id="coberturas-modal-overlay"></div>
    <div class="modal" id="coberturas-modal">
        <button class="close-button" onclick="cerrarCoberturas()">X</button>
        <h3 id="cob-modal-titulo">Coberturas</h3>
        {% if not coberturas_precios_vigentes %}
            <div class="cob-aviso-precios">
                Los precios del periodo próximo no están cargados.
                Se usan los de {{ coberturas_periodo }}.
            </div>
        {% endif %}
        <div id="cob-lista-capita"></div>
        <div class="cob-subtotales">
            Mensual: <strong id="cob-sub-mensual">$0</strong><br>
            <span id="cob-sub-unico-fila">Pago único: <strong id="cob-sub-unico">$0</strong></span>
        </div>
        <input type="text" id="cob-buscador" placeholder="Buscar cobertura para agregar..."
               oninput="buscarCoberturas()" autocomplete="off">
        <div id="cob-resultados"></div>
    </div>
```

- [ ] **Step 3: JS del modal**

Agregar dentro del `<script>` de coberturas de Task 7, después de `recalcularCoberturas()`:

```javascript
        // ===== Modal de detalle =====
        let catalogoCoberturas = [];   // sólo activas: lo que se puede agregar
        let capitaActual = null;       // el <td> cuya lista se está editando

        function cargarCatalogoCoberturas() {
            fetch('/widget/api/coberturas/')
                .then(function (r) { return r.json(); })
                .then(function (d) { catalogoCoberturas = d.coberturas || []; })
                .catch(function () { catalogoCoberturas = []; });
        }

        function abrirCoberturas(btn) {
            capitaActual = btn.closest('td');
            const fila = capitaActual.closest('tr');
            const select = fila.querySelector('.select-parentesco');
            const quien = select
                ? (select.value || 'Nueva cápita')
                : fila.cells[0].textContent.trim();
            document.getElementById('cob-modal-titulo').textContent = 'Coberturas — ' + quien;
            document.getElementById('cob-buscador').value = '';
            document.getElementById('cob-resultados').innerHTML = '';
            renderCoberturasCapita();
            document.getElementById('coberturas-modal').style.display = 'block';
            document.getElementById('coberturas-modal-overlay').style.display = 'block';
        }

        function cerrarCoberturas() {
            document.getElementById('coberturas-modal').style.display = 'none';
            document.getElementById('coberturas-modal-overlay').style.display = 'none';
            capitaActual = null;
        }

        function renderCoberturasCapita() {
            const lista = (capitaActual && capitaActual._coberturas) || [];
            const cont = document.getElementById('cob-lista-capita');
            cont.innerHTML = '';
            let mensual = 0, unico = 0;

            if (!lista.length) {
                cont.innerHTML = '<div class="cob-item-meta">Sin coberturas especiales.</div>';
            }

            lista.forEach(function (c, i) {
                if (c.pago_unico) unico += c.valor || 0;
                else mensual += c.valor || 0;

                const item = document.createElement('div');
                item.className = 'cob-item';

                const punto = document.createElement('span');
                punto.className = 'cob-semaforo cob-' + c.semaforo;
                punto.textContent = '●';
                item.appendChild(punto);

                const datos = document.createElement('div');
                datos.className = 'cob-item-datos';

                const nombre = document.createElement('div');
                nombre.className = 'cob-item-nombre';
                nombre.textContent = c.nombre;
                datos.appendChild(nombre);

                const meta = document.createElement('div');
                meta.className = 'cob-item-meta';
                meta.textContent = c.sin_catalogo
                    ? 'Sin datos en el catálogo de precios'
                    : (c.categoria || 'Sin categoría');
                datos.appendChild(meta);

                if (c.pago_unico) {
                    const badge = document.createElement('span');
                    badge.className = 'cob-badge-unico';
                    badge.textContent = 'pago único';
                    datos.appendChild(badge);
                }

                if (c.mensaje) {
                    const msg = document.createElement('div');
                    msg.className = 'cob-item-msg';
                    msg.textContent = c.mensaje;
                    datos.appendChild(msg);
                }

                item.appendChild(datos);

                const valor = document.createElement('span');
                valor.className = 'cob-item-valor';
                valor.textContent = fmtARS(c.valor);
                item.appendChild(valor);

                const quitar = document.createElement('button');
                quitar.type = 'button';
                quitar.className = 'cob-chip-x';
                quitar.textContent = '✕';
                quitar.title = 'Quitar';
                quitar.onclick = function () { quitarCoberturaDeCapita(i); };
                item.appendChild(quitar);

                cont.appendChild(item);
            });

            document.getElementById('cob-sub-mensual').textContent = fmtARS(mensual);
            document.getElementById('cob-sub-unico').textContent = fmtARS(unico);
            document.getElementById('cob-sub-unico-fila').style.display = unico ? '' : 'none';
        }

        function buscarCoberturas() {
            const termino = document.getElementById('cob-buscador').value.trim().toLowerCase();
            const cont = document.getElementById('cob-resultados');
            cont.innerHTML = '';
            if (termino.length < 2) return;

            const yaTiene = ((capitaActual && capitaActual._coberturas) || [])
                .map(function (c) { return c.id; });

            catalogoCoberturas
                .filter(function (c) {
                    return c.nombre.toLowerCase().indexOf(termino) !== -1
                        && yaTiene.indexOf(c.id) === -1;
                })
                .slice(0, 20)
                .forEach(function (c) {
                    const fila = document.createElement('div');
                    fila.className = 'cob-resultado';
                    fila.innerHTML = '<span class="cob-semaforo cob-' + c.semaforo + '">●</span> ';
                    fila.appendChild(document.createTextNode(
                        c.nombre + '  ' + fmtARS(c.valor) + (c.pago_unico ? ' (único)' : '')
                    ));
                    fila.onclick = function () { agregarCobertura(c.id); };
                    cont.appendChild(fila);
                });
        }

        function agregarCobertura(id) {
            const cob = catalogoCoberturas.find(function (c) { return c.id === id; });
            if (!cob || !capitaActual) return;
            capitaActual._coberturas = (capitaActual._coberturas || []).concat([
                Object.assign({}, cob)
            ]);
            document.getElementById('cob-buscador').value = '';
            document.getElementById('cob-resultados').innerHTML = '';
            renderCoberturasCapita();
            recalcularCoberturas();
        }

        function quitarCoberturaDeCapita(indice) {
            if (!capitaActual) return;
            const lista = (capitaActual._coberturas || []).slice();
            lista.splice(indice, 1);
            capitaActual._coberturas = lista;
            renderCoberturasCapita();
            recalcularCoberturas();
        }
```

Y en el `DOMContentLoaded` de Task 7, agregar antes de `recalcularCoberturas();`:

```javascript
            cargarCatalogoCoberturas();
```

- [ ] **Step 4: Verificar a mano**

`python manage.py runserver`, abrir `/widget/retencion/38407571/` → modal de cotización → click en el círculo del titular.

Verificar:
- El modal abre por encima del de cotización, con el parentesco en el título.
- Las 3 coberturas del titular aparecen en gris con "Sin datos en el catálogo de precios" y valor $0.
- Escribir "anemia" en el buscador trae resultados; escribir 1 sola letra no.
- Click en un resultado lo agrega: aparece en la lista, cambia el subtotal, y al cerrar el modal el círculo de la cápita cambió de color.
- Agregar una Aguda (buscar "AG_") suma al subtotal de pago único, **no** al mensual.
- Agregar una Roja (buscar "FALSEO_R_") pone el círculo en 🔴 y suma al mensual.
- El ✕ de cada ítem lo quita y recalcula.
- Una cobertura ya agregada no vuelve a aparecer en los resultados de búsqueda.
- Consola sin errores.

- [ ] **Step 5: Commit**

```bash
git add afiliados/templates/rete_produccion.html
git commit -m "feat: modal de detalle de coberturas con buscador"
```

---

### Task 9: Totales en la cotización y payload

**Files:**
- Modify: `afiliados/templates/rete_produccion.html` — markup de totales (~línea 1063, después de "Agregar capita"), payload normal (~2281) y escalonado (~2242), banner de precios

**Interfaces:**
- Consumes: `totalesCoberturas()` de Task 7; `coberturas_periodo` y `coberturas_precios_vigentes` del contexto (Task 6).
- Produces: nada que consuman otras tasks.

- [ ] **Step 1: Agregar las filas de totales**

Después del botón `➕ Agregar capita` (línea 1063), antes de `<div class="table_2">`:

```html
                {% if not coberturas_precios_vigentes %}
                    <div class="cob-aviso-precios">
                        Los precios del periodo próximo no están cargados.
                        Se usan los de {{ coberturas_periodo }}.
                    </div>
                {% endif %}
                <div class="cob-subtotales">
                    Coberturas especiales (mensual): <strong id="cob-total-mensual">$0</strong><br>
                    <span id="cob-fila-unico">
                        Pagos únicos: <strong id="cob-total-unico">$0</strong>
                        <span class="cob-item-meta">(no se suman a la cuota)</span>
                    </span>
                </div>
```

- [ ] **Step 2: Reemplazar el payload del modo escalonado**

Sustituir (línea ~2242):

```javascript
                // TODO: valor de coberturas especiales — pendiente nueva API
                cotizarEscalonado({ plan: plan, gestion: gestion, ubicacion: ubicacion, edades: edades, aportes: aporte, patologias: 0 });
```

por:

```javascript
                cotizarEscalonado({ plan: plan, gestion: gestion, ubicacion: ubicacion, edades: edades, aportes: aporte, patologias: totalesCoberturas().mensual });
```

- [ ] **Step 3: Reemplazar el payload normal**

Sustituir (línea ~2281):

```javascript
                patologias: 0 // TODO: valor de coberturas especiales — pendiente nueva API
```

por:

```javascript
                // Sólo el mensual: las Agudas son pago único, no sobrecuota.
                patologias: totalesCoberturas().mensual
```

- [ ] **Step 4: Verificar a mano**

`python manage.py runserver`, abrir el modal de cotización.

Verificar:
- La fila "Coberturas especiales (mensual)" muestra $0 con este afiliado.
- La fila "Pagos únicos" está oculta cuando el total es 0.
- Agregar una cobertura verde con valor → el total mensual sube.
- Agregar una Aguda → aparece la fila de pagos únicos, el mensual **no** cambia.
- Cotizar: en la consola del servidor, el `print` de `cotizar_actual` muestra el mismo número que la fila mensual.
- En la pestaña Network, el body del POST a `/widget/api/cotizar/` tiene ese `patologias`.
- Modo escalonado manda el mismo valor en todos los tramos.
- Eliminar una fila con coberturas baja el total.

- [ ] **Step 5: Verificar el camino de fallo**

Bloquear el catálogo para comprobar que nada rompe: en `afiliados/coberturas.py` cambiar temporalmente `URL_CATALOGO` a `"https://127.0.0.1:9/nope"`, reiniciar el server, limpiar el cache (reiniciar alcanza con LocMemCache) y recargar la vista.

Expected: la página carga, todas las cápitas en ⚪, el buscador no trae resultados, los totales en $0, y en la consola del servidor `Error al consultar el catálogo de coberturas: ...`. **Revertir el cambio de URL.**

- [ ] **Step 6: Correr toda la suite**

Run: `python manage.py test afiliados`
Expected: PASS.
Run: `python manage.py check`
Expected: sin issues.

- [ ] **Step 7: Commit**

```bash
git add afiliados/templates/rete_produccion.html
git commit -m "feat: sumar coberturas especiales a la cuota cotizada"
```

---

## Notas de cierre

- **El afiliado 38407571 no sirve para probar el camino feliz**: sus tres IDs de Gecros (471, 504, 505) no están en el catálogo. Todo lo que se verifica con valores hay que hacerlo agregando coberturas desde el buscador, o buscando un afiliado cuyos IDs sí estén cargados.
- Queda pendiente, fuera de este plan: escalar la carga de esos IDs faltantes en `CoberturaTipo`.
- Al terminar, la rama `feat/coberturas-semaforo` se mergea a `refactor/coberturas-id-gecros` o directo a `master` según cómo se decida integrar el refactor previo.

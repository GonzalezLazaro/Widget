from datetime import date
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from afiliados.coberturas import (
    MESES_FALLBACK,
    _clave_cache,
    es_pago_unico,
    indice_por_id_externo,
    normalizar,
    obtener_catalogo,
    periodo_anterior,
    periodo_proximo,
    resolver,
    semaforo,
    semaforo_grupo,
)

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

class ObtenerCatalogoTests(SimpleTestCase):
    """Un periodo sin precios cargados devuelve todos los valor_actual en 0."""

    def setUp(self):
        cache.delete(_clave_cache())

    def tearDown(self):
        cache.delete(_clave_cache())

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
        """El catálogo se conserva (con valor 0): no debe desaparecer el 🔴.

        `datos` trae los registros del último periodo consultado -- nombres,
        categorías y booleanos válidos, sólo sin precios -- y eso es lo que
        tiene que llegar a la vista, no una lista vacía.
        """
        sin_precios = self._respuesta([
            cob(id_externo=1, valor_actual=0.0, permite_ingreso=False),
            cob(id_externo=2, valor_actual=0.0),
        ])
        with patch("afiliados.coberturas.requests.get", return_value=sin_precios):
            coberturas, periodo, vigentes = obtener_catalogo()

        self.assertEqual(len(coberturas), 2)
        self.assertEqual(periodo, periodo_proximo())
        self.assertFalse(vigentes)
        self.assertEqual([c["valor"] for c in coberturas], [0, 0])
        # El semáforo se deriva de los booleanos, no del precio: sigue el 🔴.
        self.assertEqual(coberturas[0]["semaforo"], "rojo")

    def test_ningun_periodo_con_precios_cachea_el_resultado(self):
        """El fallback agotado también se cachea: no repetir las 4 requests."""
        sin_precios = self._respuesta([cob(id_externo=1, valor_actual=0.0)])
        with patch("afiliados.coberturas.requests.get",
                   return_value=sin_precios) as get:
            obtener_catalogo()
            obtener_catalogo()

        self.assertEqual(get.call_count, MESES_FALLBACK + 1)

    def test_api_caida_no_rompe(self):
        """La vista de retención tiene que cargar igual."""
        import requests as _requests
        with patch("afiliados.coberturas.requests.get",
                   side_effect=_requests.exceptions.ConnectionError("boom")):
            coberturas, periodo, vigentes = obtener_catalogo()

        self.assertEqual(coberturas, [])
        self.assertEqual(periodo, periodo_proximo())
        self.assertFalse(vigentes)

    def test_api_caida_cachea_el_resultado_degradado(self):
        """Con el catálogo caído, la segunda carga no vuelve a pegarle a la API."""
        import requests as _requests
        with patch("afiliados.coberturas.requests.get",
                   side_effect=_requests.exceptions.ConnectionError("boom")) as get:
            obtener_catalogo()
            obtener_catalogo()

        self.assertEqual(get.call_count, 1)

    def test_http_error_no_rompe(self):
        """raise_for_status() puede levantar HTTPError ante un 4xx/5xx."""
        import requests as _requests
        respuesta = Mock()
        respuesta.raise_for_status.side_effect = _requests.exceptions.HTTPError("500")
        with patch("afiliados.coberturas.requests.get", return_value=respuesta):
            coberturas, periodo, vigentes = obtener_catalogo()

        self.assertEqual(coberturas, [])
        self.assertEqual(periodo, periodo_proximo())
        self.assertFalse(vigentes)

    def test_json_invalido_no_rompe(self):
        """.json() puede levantar ValueError con un cuerpo que no es JSON."""
        respuesta = Mock()
        respuesta.raise_for_status.return_value = None
        respuesta.json.side_effect = ValueError("Expecting value")
        with patch("afiliados.coberturas.requests.get", return_value=respuesta):
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

    def test_lista_con_elementos_no_dict_no_rompe(self):
        """La promesa es no levantar nunca, ni con un cuerpo malformado."""
        with patch("afiliados.coberturas.requests.get",
                   return_value=self._respuesta([None, "error", 123])):
            coberturas, periodo, vigentes = obtener_catalogo()

        self.assertEqual(coberturas, [])
        self.assertFalse(vigentes)

@override_settings(WIDGET_AUTH_ENABLED=False)
class CatalogoEndpointTests(TestCase):
    """Solo las activas: son las que el agente puede agregar desde el buscador.

    `requiere_permiso_json` hace passthrough con WIDGET_AUTH_ENABLED=False
    (ver `cuentas/decoradores.py`), asi que el test no depende del entorno.
    """

    def setUp(self):
        cache.delete(_clave_cache())

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

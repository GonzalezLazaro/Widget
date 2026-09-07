from unittest.mock import patch, Mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from afiliados.utils import primer_valor
from afiliados.views import BuscarRetencionView


class ObtenerPreexTests(TestCase):
    """La API de preexistencias puede responder 200 con cuerpo `null`."""

    def setUp(self):
        cache.delete('verificacion_diaria_preex')
        cache.delete('clave_dinamica_preex')

    def _mock_response(self, payload):
        respuesta = Mock()
        respuesta.raise_for_status.return_value = None
        respuesta.json.return_value = payload
        return respuesta

    def test_respuesta_null_no_rompe_y_usa_archivo_local(self):
        with patch('afiliados.views.requests.get', return_value=self._mock_response(None)):
            datos = BuscarRetencionView().obtener_preex()

        self.assertTrue(datos, "Debe devolver las preexistencias del archivo local")

    def test_respuesta_vacia_no_rompe(self):
        with patch('afiliados.views.requests.get', return_value=self._mock_response({})):
            datos = BuscarRetencionView().obtener_preex()

        self.assertTrue(datos)

    def test_respuesta_null_no_marca_verificacion_diaria(self):
        """Si la API falló, hay que reintentar en la próxima request, no esperar 24 h."""
        with patch('afiliados.views.requests.get', return_value=self._mock_response(None)):
            BuscarRetencionView().obtener_preex()

        self.assertIsNone(cache.get('verificacion_diaria_preex'))


class PrimerValorTests(SimpleTestCase):
    """Varias APIs internas responden 200 con `[]` cuando no hay datos."""

    def test_lista_vacia_no_rompe(self):
        """El caso que tiraba IndexError en dni_agecta y acechaba en tipo_ben."""
        self.assertIsNone(primer_valor([], "doc_id"))
        self.assertEqual(primer_valor([], "doc_id", 0), 0)

    def test_devuelve_el_campo(self):
        self.assertEqual(primer_valor([{"doc_id": 30111222}], "doc_id"), 30111222)

    def test_toma_el_primer_registro(self):
        self.assertEqual(primer_valor([{"a": 1}, {"a": 2}], "a"), 1)

    def test_campo_ausente_o_nulo_cae_al_default(self):
        self.assertEqual(primer_valor([{"otro": 1}], "doc_id", 0), 0)
        self.assertEqual(primer_valor([{"doc_id": None}], "doc_id", 0), 0)

    def test_payload_inesperado_cae_al_default(self):
        for payload in (None, {}, "", [None], [{}], ["x"], 42):
            with self.subTest(payload=payload):
                self.assertEqual(primer_valor(payload, "doc_id", 0), 0)

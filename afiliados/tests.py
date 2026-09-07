from django.test import SimpleTestCase

from afiliados.utils import extraer_doc_id, parsear_coberturas


class ParsearCoberturasTests(SimpleTestCase):
    """Los campos de Gecros llegan como strings separados por coma."""

    def test_pareo_posicional(self):
        self.assertEqual(
            parsear_coberturas("12,45", "HTA, DIABETES"),
            [{"id": 12, "nombre": "HTA"}, {"id": 45, "nombre": "DIABETES"}],
        )

    def test_longitudes_distintas_cae_a_ids(self):
        """Sin correspondencia 1 a 1 no se puede afirmar qué nombre va con qué ID."""
        resultado = parsear_coberturas("12,45", "HTA")

        self.assertEqual([c["nombre"] for c in resultado], ["ID 12", "ID 45"])
        self.assertEqual([c["id"] for c in resultado], [12, 45])

    def test_sin_nombres_usa_los_ids(self):
        self.assertEqual(
            parsear_coberturas("7", None),
            [{"id": 7, "nombre": "ID 7"}],
        )

    def test_sin_ids_devuelve_vacio(self):
        for entrada in (None, "", "   ", "abc", ","):
            self.assertEqual(parsear_coberturas(entrada, "HTA"), [])


class ExtraerDocIdTests(SimpleTestCase):
    """dni_agecta responde 200 con `[]` si el grupo no tiene agente de cuenta."""

    def test_lista_vacia_no_rompe(self):
        """El caso que tiraba IndexError: 200 con cuerpo vacío."""
        self.assertEqual(extraer_doc_id([]), 0)

    def test_devuelve_el_doc_id(self):
        self.assertEqual(extraer_doc_id([{"doc_id": 30111222}]), 30111222)

    def test_payload_inesperado_cae_a_cero(self):
        for payload in (None, {}, "", [None], [{}], [{"doc_id": None}]):
            with self.subTest(payload=payload):
                self.assertEqual(extraer_doc_id(payload), 0)

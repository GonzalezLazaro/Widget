from django.test import SimpleTestCase

from afiliados.utils import parsear_coberturas, primer_valor


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

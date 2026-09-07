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

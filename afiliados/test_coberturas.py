from datetime import date

from django.test import SimpleTestCase

from afiliados.coberturas import (
    es_pago_unico,
    normalizar,
    periodo_anterior,
    periodo_proximo,
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
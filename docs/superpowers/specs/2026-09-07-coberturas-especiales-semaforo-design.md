# Semáforo de coberturas especiales en el modal de cotización

Fecha: 2026-09-07
Rama: `feat/coberturas-semaforo` (sobre `refactor/coberturas-id-gecros`)

## Contexto

El modal de cotización de la vista de retención (`afiliados/templates/rete_produccion.html`)
muestra hoy las coberturas especiales de cada integrante del grupo familiar como chips
read-only con el ID de Gecros en `data-id-gecros`, sin valor monetario. El payload de
cotización manda `patologias: 0` con un TODO, a la espera de una fuente de precios.

El commit anterior (`refactor: coberturas especiales por ID de Gecros`) desmanteló el
catálogo local — `home/static/preexistencias.json`, `homologacion.xlsx`, el endpoint
`api/preex` y toda la traducción por nombre — justamente para dejar el hueco listo.

Este trabajo llena ese hueco con la API de `CoberturaTipo`, y agrega tres cosas que hoy
no existen: un semáforo por cápita, un modal de detalle con buscador, y la suma real del
valor al monto a cotizar.

## La API

```
GET https://gestionafiliaciones.nobissalud.com:5003/api/CoberturaTipo?periodo=YYYYMM
```

Devuelve una lista plana de 573 objetos. Responde UTF-8 válido (el `charset=utf-8`
declarado es correcto). Ejemplo:

```json
{
  "cbtId": 558,
  "cbtNombre": "AG_AMIGDALITIS SIN TRATAR",
  "cbtActivo": 1,
  "id_externo": 643,
  "id_categoria": 4,
  "categoria_nombre": "Aguda",
  "permite_ingreso": true,
  "requiere_empresa_cliente": false,
  "mensaje_rechazo": "Esta cobertura es válida para ventas... Debe realizar pago ÚNICO sin sobrecuota posterior",
  "fecha_update": "",
  "valor_actual": 425567.0
}
```

### Lo que se verificó sobre los datos (2026-09-07, periodo 202610)

- **573 registros, 156 con `cbtActivo == 1`.**
- **`categoria_nombre` y los booleanos son redundantes y coinciden exacto** entre los
  activos — no hay ni un caso que discrepe:

  | Categoría | n | `permite_ingreso` | `requiere_empresa_cliente` | Semáforo |
  |---|---|---|---|---|
  | Roja | 41 | `false` | `false` | 🔴 |
  | Amarilla | 28 | `true` | `true` | 🟡 |
  | Verde | 30 | `true` | `false` | 🟢 |
  | Aguda | 15 | `true` | `false` | 🟢 |
  | *(sin categoría)* | 42 | `true` | `false` | 🟢 |

  Se deriva el semáforo de los **booleanos**, no de `categoria_nombre`: son la fuente
  semántica y no dependen del string.

- **Los 114 registros con `valor_actual > 0` son exactamente los 114 categorizados.**
  Los 42 activos sin categoría (tipo `"Afiliados Agro"`, administrativas) valen 0.
- **Las 15 "Aguda" son pago único**, no sobrecuota: su `mensaje_rechazo` dice
  literalmente *"Debe realizar pago ÚNICO sin sobrecuota posterior"*.
- **`id_externo` es el campo que matchea contra el `id_cobertura_especial` de Gecros.**
  Los 185 IDs históricos del viejo `homologacion.xlsx` matchean 185/185.
- `id_externo` va de 1 a 663 con **91 huecos**. Hay coberturas que Gecros devuelve y no
  están en el catálogo (ver Riesgos).
- Fallback de periodo: un periodo sin precios cargados devuelve los 573 registros con
  `valor_actual: 0.0` en **todos** (verificado con 202608 y 202512). 202609 tiene 113
  con valor; 202610 y 202611 tienen 114.

## Decisiones

| Tema | Decisión |
|---|---|
| Match Gecros → catálogo | **Sólo `id_externo`**, exacto. Sin fallback por nombre. |
| Vigencia | Todo lo que manda Gecros se considera vigente. `cbtActivo` sólo filtra qué se puede **agregar** desde el buscador. |
| Agudas | **No** se suman a la cuota. Total de pagos únicos aparte. |
| Rojas | **Sí** se suman. El 🔴 es señal visual, no bloquea cotizar. |
| Sin match en el catálogo | Chip ⚪ con el nombre crudo de Gecros y aviso "sin datos en el catálogo de precios", valor 0. Distinto del `-` de "no tiene ninguna". |
| Semáforo por cápita | Manda el peor de las que **sí** resolvieron: 🔴 > 🟡 > 🟢. ⚪ sólo si ninguna resolvió. |
| Entrega del catálogo al front | Proxy Django. El widget se embebe en portales de terceros y `gestionafiliaciones.nobissalud.com:5003` no va a tener CORS para esos orígenes, además de ser puerto no estándar. |
| Coberturas agregadas por el agente | Simulación para la cotización. No persisten en Gecros. |

## Arquitectura

### `afiliados/coberturas.py` (nuevo)

Módulo aparte para no volver a convertir `utils.py` en un cajón de sastre. Todo función
pura salvo un fetch aislado.

```python
URL_CATALOGO = "https://gestionafiliaciones.nobissalud.com:5003/api/CoberturaTipo"
ID_CATEGORIA_AGUDA = 4
MESES_FALLBACK = 3

periodo_proximo(hoy=None) -> "202610"     # mes siguiente; maneja diciembre -> enero
periodo_anterior("202601") -> "202512"

semaforo(cob) -> 'rojo' | 'amarillo' | 'verde'
es_pago_unico(cob) -> bool                # id_categoria == 4
normalizar(cob) -> dict
resolver(ids_gecros, nombres, indice) -> list[dict]
semaforo_grupo(coberturas) -> 'rojo' | 'amarillo' | 'verde' | 'gris' | None
indice_por_id_externo(catalogo) -> dict
obtener_catalogo() -> (coberturas, periodo_usado, precios_vigentes)
```

`semaforo()`:

```python
if not cob.get('permite_ingreso'):        return 'rojo'
if cob.get('requiere_empresa_cliente'):   return 'amarillo'
return 'verde'
```

`semaforo_grupo()` toma el peor de las coberturas **resueltas** (`rojo` > `amarillo` >
`verde`) e ignora las ⚪ para ese cálculo; devuelve `'gris'` sólo cuando ninguna resolvió,
y `None` cuando la cápita no tiene ninguna cobertura. Una cápita con una verde y una sin
catálogo muestra 🟢 — la ⚪ se ve al abrir el modal, que es donde el agente la puede
accionar.

`resolver()` reusa `parsear_coberturas()` de `utils.py`, que ya hace el pareo posicional
de `id_cobertura_especial` con `cobertura_especial` y el `.strip()` de cada nombre —
necesario, porque Gecros manda padding (`"DISPENSA CRONICO                    "`).
Después enriquece cada entrada buscándola por `id_externo` en el índice.

`obtener_catalogo()`:

1. Pide `periodo_proximo()`.
2. Si **ningún** registro tiene `valor_actual`, retrocede un mes y reintenta, hasta
   `MESES_FALLBACK` veces.
3. Devuelve el periodo que efectivamente usó y `precios_vigentes = (periodo_usado == periodo_proximo())`.

Cache Django (mismo patrón que `gecros_token`): **TTL 6 h** cuando hay precios del
periodo próximo, **TTL 15 min** cuando cayó al fallback — para detectar la carga de
precios pronto en vez de esperar horas. Mismo criterio que el fix de
`verificacion_diaria_preex`, que a propósito no marcaba la verificación cuando la API
fallaba.

**Resiliencia:** ante cualquier error de red, HTTP o parseo, devuelve
`([], periodo_proximo(), False)`. Nunca levanta. Índice vacío significa que todas las
coberturas caen a ⚪ y **la vista de retención sigue cargando**. Esa vista ya acumuló dos
500 por asumir que las APIs internas responden bien (`aef36dc`, `51f1d3c`); no se le
agrega un tercero.

### Endpoint proxy

```
GET /widget/api/coberturas/        @requiere_permiso_json("accion.cotizar")

{"periodo": "202610", "precios_vigentes": true, "coberturas": [ ...156 activos... ]}
```

Sólo `cbtActivo == 1`: es lo que alimenta el buscador. Mismo permiso que cotizar — quien
puede cotizar puede ver el catálogo. Ruta en `afiliados/urls.py` junto a `api/cotizar/`.

### `BuscarRetencionView.get()`

Pide el catálogo **una vez** antes del loop de afiliados. Por cápita, donde hoy hay:

```python
afiliado_info['Coberturas'] = parsear_coberturas(patologias_id, patologia)
```

pasa a:

```python
afiliado_info['Coberturas'] = resolver(patologias_id, patologia, indice)
afiliado_info['Semaforo'] = semaforo_grupo(afiliado_info['Coberturas'])
```

Al contexto del template se agregan `coberturas_periodo` y `coberturas_precios_vigentes`.

### Contrato de una cobertura normalizada

```json
{
  "id": 643,                       // id_externo — la clave contra Gecros
  "nombre": "AG_AMIGDALITIS SIN TRATAR",
  "categoria": "Aguda",
  "semaforo": "verde",
  "valor": 425567.0,
  "pago_unico": true,
  "mensaje": "Esta cobertura es válida para...",
  "activo": true
}
```

Sin match en el catálogo:

```json
{"id": 471, "nombre": "ANTICONCEPTIVOS ORALES", "semaforo": "gris",
 "valor": 0, "pago_unico": false, "sin_catalogo": true}
```

## Frontend

### Chip

Reemplaza los chips actuales de `rete_produccion.html`. Un botón por cápita con el
círculo de color, **siempre cliqueable** — incluso en estado `-`, para poder agregarle
coberturas a una cápita que no tiene. El `title` lleva el resumen (cantidad y peor
categoría).

### Modal de detalle

`#coberturas-modal`, tercer nivel sobre `#secondary-modal` (que a su vez está sobre
`#modal`). Cuidado con el z-index.

Por cápita:

- Lista de sus coberturas: círculo, nombre, categoría, valor formateado, badge
  *pago único* cuando corresponde, `mensaje_rechazo` cuando lo hay, botón quitar.
- Las ⚪ sin catálogo se muestran en gris con el aviso.
- Input de búsqueda que filtra los 156 activos por nombre, case-insensitive, tope 20
  resultados.
- Pie con subtotal mensual y subtotal de pagos únicos **de esa cápita**.

### Estado en JS

Cada `<td>` de coberturas guarda su lista en `td._coberturas`, propiedad del nodo y no
atributo: sin escapado de JSON, y muere sola con la fila cuando `eliminarFila()` la
borra. Los totales se calculan recorriendo
`document.querySelectorAll('#secondary-modal .td-coberturas')`.

El estado inicial viaja en un único `{{ ...|json_script:"coberturas-iniciales" }}`
(Django 5.1 lo escapa correctamente) que el JS reparte por posición de fila al cargar.

### Totales en el modal de cotización

| Fila | Destino |
|---|---|
| Coberturas especiales (mensual) | → `patologias` del payload |
| Pagos únicos (Agudas) | informativo, **no** va al payload |

Reemplaza el `patologias: 0 // TODO` en los dos caminos: el normal y el escalonado.

Banner cuando `precios_vigentes == false`:
*"Los precios de 202610 no están cargados. Se usan los de 202609."*

## Testing

`coberturas.py` es todo función pura salvo el fetch, así que la suite cubre:

- **Periodo:** `periodo_proximo` y `periodo_anterior`, incluyendo el cruce de año
  (diciembre → enero, enero → diciembre).
- **Semáforo:** los tres casos, y la precedencia del peor en `semaforo_grupo`
  (grupos mixtos, una resuelta + una sin catálogo → gana la resuelta, todas sin
  catálogo → `'gris'`, y el caso vacío → `None`).
- **`resolver`:** match por `id_externo`; sin match → `sin_catalogo` con semáforo gris y
  valor 0; pareo posicional; cantidades desparejas de nombres vs ids.
- **Clasificación:** una Aguda queda fuera del total mensual y entra en el de pagos únicos.
- **`obtener_catalogo` con mock de `requests`:** periodo con precios; periodo vacío que
  cae al anterior; **API caída → catálogo vacío sin excepción**.

Sin infraestructura de tests JS en el repo: el front se verifica a mano.

### Verificación end-to-end

1. `/widget/retencion/38407571/` carga sin error y muestra ⚪ en las 3 coberturas del
   titular (ver Riesgos).
2. Click en el chip abre el modal de detalle con las 3 en gris y el aviso.
3. El buscador encuentra coberturas por nombre parcial y las agrega; el chip y los
   totales se actualizan.
4. Agregar una Aguda suma al total de pagos únicos, **no** al mensual.
5. Agregar una Roja suma al mensual y pone el chip en 🔴.
6. Cotizar manda el total mensual en `patologias`; se verifica en el `print` de
   `cotizar_actual`.
7. Modo escalonado manda el mismo total.
8. `agregarFila` / `eliminarFila` mantienen los totales coherentes.
9. Con el catálogo inalcanzable (host bloqueado), la página carga igual y todo queda ⚪.

## Riesgos conocidos

- **El afiliado 38407571 va a mostrar ⚪ en sus 3 coberturas.** Sus IDs de Gecros
  (471, 504, 505) caen en un hueco de `id_externo` y no están en el catálogo; además
  `"DISPENSA CRONICO"` y `"DISPENSA DIABETES"` no aparecen en `cbtNombre` bajo ninguna
  forma — parecen dispensas, no preexistencias tarifadas. El camino feliz sólo se ve
  agregando desde el buscador, o con un afiliado cuyos IDs sí estén cargados. **Queda
  pendiente escalar la carga de esos IDs en `CoberturaTipo`.**
- **`MESES_FALLBACK = 3`:** si hace más de tres meses que no cargan precios, el catálogo
  sale sin valores y todo suma 0. Se prefirió eso a encadenar 12 requests de 160 KB.
- **`id_externo` duplicado:** el valor 1 aparece en `FALSEO B_RINITIS` (activo) y en
  `NO USAR- COBERTURA GENERAL` (inactivo). El índice se arma priorizando el activo.

## Fuera de alcance

- Persistir en Gecros las coberturas que agrega el agente.
- Bloquear la cotización ante una cobertura roja.
- Reemplazar el `print()` por logging estructurado en `views.py`.

import requests
from datetime import datetime

from django.conf import settings  # REQUESTS_TIMEOUT: timeout único para HTTP saliente

def actualizar_token_wise():
    """Obtiene un nuevo token desde la API y lo guarda."""
    url_token = 'https://api.wcx.cloud/core/v1/authenticate'
    query_params = {'user': 'apinobis'}
    headers = {
        'Content-Type': 'application/json',
        'x-api-key': 'be9dd08a9cd8422a9af1372a445ec8e4',
    }
    try:
        response = requests.get(url_token, headers=headers, params=query_params, timeout=settings.REQUESTS_TIMEOUT)
        response.raise_for_status()  # Lanza un error si la solicitud falla
       
        # Imprimir la respuesta completa para verificar la estructura
        #print(f"Respuesta de la API: {response.text}")
       
        # Suponiendo que el token está en la clave 'token'
        response_json = response.json()
        token = response_json.get('token', None)
       
        if token:
            #print(f"Nuevo token obtenido y guardado: {token}")
            return token
        else:
            raise Exception("No se encontró el token en la respuesta.")
    except requests.exceptions.RequestException as e:
        print(f"Error al obtener el token: {e}")
 
 
def actualizar_token_gecros():
    url = "https://appmobile.nobissalud.com.ar/connect/token"
    payload = {
        'userName': 'fastapi',
        'password': 'SistemApi25',
        'grant_type': 'password',
        'client_id': 'gecros'
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
        response.raise_for_status()  # Lanza un error si la solicitud falla
       
        # Imprimir la respuesta completa para verificar la estructura
        #print(f"Respuesta de la API: {response.text}")
       
        # Suponiendo que el token está en la clave 'access_token'
        response_json = response.json()
        token = response_json.get('access_token', None)
       
        if token:
            #print(f"Nuevo token obtenido y guardado: {token}")
            return token
        else:
            raise Exception("No se encontró el token en la respuesta.")
    except requests.exceptions.RequestException as e:
        print(f"Error al obtener el token: {e}")
        return None


def primer_valor(payload, campo, default=None):
    """Primer registro de una respuesta JSON tipo lista -> `campo`.

    Varias APIs internas (dni_agecta, tipo_ben) responden 200 con `[]`
    cuando no hay datos, así que mirar sólo el status_code no alcanza:
    hay que mirar el cuerpo.
    """
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        valor = payload[0].get(campo)
        if valor is not None:
            return valor
    return default


# Filtrar condición del grupo familiar
def condicion_grupal(convenio):
    if convenio:
        #print("Entrante " + convenio)
        if 'PREPAGO' in convenio.upper() or 'PP -' in convenio.upper():
            #print("Prepago")
            return "3"
        elif 'MONOTRIBUTO' in convenio.upper() or 'MT -' in convenio.upper():
            #print("MO")
            return "2"
        elif 'NO USAR' in convenio.upper():
            #print("NO USAR")
            return "4"
        elif 'SD -' in convenio.upper():
            #print("MO")
            return "5"
        else:
            #print("RD")
            return "1" # Relacion de dependencia
    #print(convenio)
    return "1"


# Coberturas especiales: el dato viene crudo de Gecros, sin catálogo local.
def parsear_coberturas(ids_gecros, nombres=None):
    """Convierte los campos crudos de Gecros en una lista de coberturas.

    `id_cobertura_especial` y `cobertura_especial` llegan como strings
    separados por coma. Se asume correspondencia posicional; si las
    longitudes no coinciden, se etiqueta cada cobertura sólo con el ID para
    no mostrar un nombre que no corresponde.
    """
    ids = [i.strip() for i in (ids_gecros or "").split(",") if i.strip().isdigit()]
    if not ids:
        return []

    etiquetas = [n.strip() for n in (nombres or "").split(",") if n.strip()]
    if len(etiquetas) != len(ids):
        etiquetas = [f"ID {i}" for i in ids]

    return [{"id": int(i), "nombre": n} for i, n in zip(ids, etiquetas)]


def obtener_expedientes_grupo_familiar(ben_ids, token_gecros, benid_to_dni, benid_to_nombre):
    url_base = "https://appmobile.nobissalud.com.ar/api/Expedientes"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token_gecros}"
    }
    expedientes = []
    for ben_id in ben_ids:
        url = f"{url_base}?BenId={ben_id}"
        response = requests.get(url, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
        if response.status_code == 200:
            data = response.json().get("data", [])
            for exp in data:
                exp["DNI"] = benid_to_dni.get(ben_id, "")
                exp["Nombre"] = benid_to_nombre.get(ben_id, "")
            # Ordenar y tomar los 10 más recientes de este ben_id
            data.sort(
                key=lambda x: datetime.strptime(x["fechaIngreso"], "%d/%m/%Y"),
                reverse=True
            )
            expedientes.extend(data[:10])
    return expedientes

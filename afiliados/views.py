from django.views import View
from django.shortcuts import render
from datetime import datetime
import requests
import json
from pandas import json_normalize
from .utils import actualizar_token_wise, actualizar_token_gecros, actualizar_preexistencias, buscar_cobertura, condicion_grupal, buscar_preexistencias, obtener_expedientes_grupo_familiar
from django.core.cache import cache
import pytz
from dateutil.relativedelta import relativedelta
from django.http import HttpResponseBadRequest
import os
from django.shortcuts import render
from django.utils.safestring import mark_safe
from django.conf import settings
from collections import defaultdict
from django.utils.decorators import method_decorator
from cuentas.decoradores import requiere_permiso_iframe, requiere_permiso_json
from cuentas.permisos import usuario_puede, grupo_que_concede
from .audit import registrar, etiqueta_usuario, usuario_para_api


class ManejoErroresAPIMixin:
    """
    Red de seguridad para las vistas HTML que consultan APIs externas.

    Muchas llamadas requests.* dentro de get() no están envueltas en try/except.
    Con timeout, ante un backend caído dejan de colgar el hilo pero levantan
    RequestException/Timeout. Este mixin la captura a nivel dispatch y devuelve un
    error controlado (502) renderizado en el template, en vez de romper la vista con
    un 500 crudo. El decorador de permisos envuelve este dispatch, así que el chequeo
    de acceso queda FUERA del try (sus respuestas no se ven afectadas).
    """
    template_error = None  # si es None se usa self.template_name

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except requests.exceptions.RequestException as e:
            print(f"Error de conexión con una API externa: {e}")
            template = self.template_error or getattr(self, "template_name", None)
            contexto = {'error': 'No se pudo conectar con el sistema. Intentá nuevamente en unos minutos.'}
            if template:
                return render(request, template, contexto, status=502)
            from django.http import HttpResponse
            return HttpResponse(contexto['error'], status=502)


@method_decorator(requiere_permiso_iframe("vista.atencion"), name="dispatch")
class BuscarAfiliadoView(ManejoErroresAPIMixin, View):
    template_name = 'ate_produccion.html'
 
    def obtener_token_wise(self):
        """
        Obtener el token desde el caché o actualizarlo si no existe o ha expirado.
        """
        # Verifica si el token está en el caché
        token = cache.get('api_token')
 
        if token is None:
            # Si el token no está en el caché o ha expirado, actualizar el token
            token = actualizar_token_wise()
            # Almacenar el token en caché con un tiempo de expiración de 50 minutos
            cache.set('api_token', token, timeout=50 * 60)  # 50 minutos = 50 * 60 segundos
 
        return token
   
    def obtener_token_gecros(self):
        # Verifica si el token está en el caché
        token = cache.get('gecros_token')
 
        if token is None:
            # Si el token no está en el caché o ha expirado, actualizar el token
            token = actualizar_token_gecros()
            # Almacenar el token en caché con un tiempo de expiración
            cache.set('gecros_token', token, timeout=1296000)
 
        return token
 
    def get(self, request, dni=None, *args, **kwargs):

        print("Cargando vista de afiliado...")

        # Validación de entrada de DNI
        if not dni:
            return HttpResponseBadRequest("Debe proporcionar un DNI.")
        elif len(dni) != 8:
            return render(request, self.template_name, {'error': 'Verifique el número de DNI, debe ser de 8 dígitos.'}, status=400)
        
        fecha_actual = datetime.now().strftime("%Y-%m-%d")
        #print(f"La fecha actual es {fecha_actual} y el DNI {dni}")
 
        url_afiliado = "https://appmobile.nobissalud.com.ar/api/afiliados/gestionAfiliados"
       
        payload_afiliado = {
            "numero": None,
            "apellido": None,
            "nombre": None,
            "dni": dni,
            "cuil": None,
            "fecha": fecha_actual,
            "incluirGrupoFam": True
        }
 
        payload_json_afiliado = json.dumps(payload_afiliado)
        token_gecros = self.obtener_token_gecros()
 
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token_gecros}"
            }
       
        # Solicitud a la API de afiliados
        response_afiliado = requests.post(url_afiliado, data=payload_json_afiliado, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
        #print(response_afiliado.text)
 
        if response_afiliado.status_code == 200:
            data_afiliado = response_afiliado.json().get('data', [])
            if data_afiliado:
                df_afiliado = json_normalize(data_afiliado)
                df_selected = df_afiliado[["nombre", "nroAfi", "parentesco"]]
                df_selected.columns = ["Nombre", "DNI", "Parentesco"]

                dni_aux = 0
                # Solicitud a la API de deuda
                for x in data_afiliado:
                    #if x.get("esTitular") == True:
                    #    dni_aux = x.get("nroAfi")
                    #    print(f"Titular detectado: {dni_aux}")
                    #    break
                    #print(x)
                    if x.get("benGrId"):
                        grupo_id = x.get("benGrId")
                        #print(f"Grupo familiar: {grupo_id}")

                        url_agegru = f"https://api.nobis.com.ar/agente_por_grupo/{grupo_id}"
                        response_agegru = requests.get(url_agegru, timeout=settings.REQUESTS_TIMEOUT)
                        try:
                            data_agegru = response_agegru.json()
                        except ValueError:
                            data_agegru = []

                        if response_agegru.status_code == 200 and data_agegru != []:
                            for x in data_agegru:
                                dni_aux = x.get("doc_id")
                                if dni_aux != 0:
                                    #print(x)
                                    print(f"DNI Agente de cuenta: {dni_aux}")
                                    break
                            break
                    else:
                        pass
                
                total_deuda = 0
                if dni_aux != 0:
                    url_deuda = f"https://appmobile.nobissalud.com.ar/api/AgentesCuenta/Deuda/{dni_aux}"
                    response_deuda = requests.get(url_deuda, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
                    try:
                        data_deuda = response_deuda.json()
                    except ValueError:
                        data_deuda = []

                    #print(data_deuda)

                    hoy = datetime.today()

                    if response_deuda.status_code == 200 and data_deuda != []:
                        for deuda in data_deuda:
                            fecven_dt = datetime.strptime(deuda.get("fecven"), "%d/%m/%Y")
                            if fecven_dt < hoy:
                                total_deuda = sum(float(item.get("monto", 0)) for item in data_deuda)
                                total_deuda = "SI" if total_deuda > 0 else "NO"
                                break
                            else:
                                total_deuda = "NO"
                    
                    elif response_deuda.status_code != 200:
                        total_deuda = "Sin dato"

                    else:
                        total_deuda = "NO"
                else:
                    print("GRUPO FAMILIAR SIN TITULAR, REVISAR!")
                    total_deuda = "Sin titular"
 
                df_selected = df_selected.copy()
                df_selected["Deuda"] = total_deuda
 
                # Ordenar por parentesco
                orden_parentesco = {"TITULAR": 1, "CONYUGE": 2, "HIJO/A": 3, "FAMILIAR A CARGO": 4}
                df_selected["Parentesco_Orden"] = df_selected["Parentesco"].map(orden_parentesco)
                df_selected = df_selected.sort_values(by=["Parentesco_Orden"]).drop("Parentesco_Orden", axis=1)
                #data = df_selected.to_dict(orient="records")
               
                # Obtener el token desde el caché o actualizarlo si ha expirado
                #print("Obteniendo token de Wise...")
                token = self.obtener_token_wise()
                #print("Token de Wise obtenido.", token)
 
                # Solicitud a la API de casos
                headers_wise = headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {token}',
                'x-api-key': 'be9dd08a9cd8422a9af1372a445ec8e4'
                }
 
                all_cases = []
 
                zona_horaria = pytz.timezone('America/Argentina/Buenos_Aires')
               
                # Fecha actual
                fecha_actual = datetime.now(zona_horaria)
 
                # Restar 3 meses a la fecha actual
                tres_meses_antes = fecha_actual - relativedelta(months=3)

                fechas_alta_dict = {}

                for afiliado in data_afiliado:
                    nro_afi = afiliado.get("nroAfi")
                    nom_afi = afiliado.get("nombre")

                    headers_interno = {
                    "Content-Type": "application/json"
                    }
                    #print(f"Consultando casos para {nro_afi} - {nom_afi}")

                    # Solicitud a la API interna para obtener fecha de alta y patologia
                    url_patologias = f"https://api.nobis.com.ar/fecha_alta_y_patologias/{nro_afi}"
                    response_p = requests.get(url_patologias, headers=headers_interno, timeout=settings.REQUESTS_TIMEOUT)
                    data_p = response_p.json()
                    #print(f"Datos de patologías para {nro_afi}: {data_p}")

                    if isinstance(data_p, list) and data_p:
                        fecha_alta = data_p[0].get('fecha_alta')
                        fecha_alta_format = datetime.strptime(fecha_alta, '%Y-%m-%dT%H:%M:%S.000')
                        fecha_formateada = fecha_alta_format.strftime('%d-%m-%Y')
                        fechas_alta_dict[nro_afi] = fecha_formateada
                    else:
                        fechas_alta_dict[nro_afi] = "Sin dato"
 
                    url_contacto = f'https://api.wcx.cloud/core/v1/contacts/?filtering=[{{"field":"contacts.personal_id","operator":"EQUAL","value":"{nro_afi}"}}]&fields=id,personal_id&sort=desc&sort_field=id&limit=5&page=1'
 
                    response_contacto = requests.request("GET", url_contacto, headers=headers_wise, timeout=settings.REQUESTS_TIMEOUT)
 
                    # Convertir la respuesta a un diccionario de Python
                    data_contacto = response_contacto.json()
 
                    # Acceder a uno de los datos, por ejemplo, el valor del campo 'id'
                    if 'data' in data_contacto and len(data_contacto['data']) > 0:
                        first_item = data_contacto['data'][0]  # Primer elemento de la lista de resultados
                        contact_id = first_item.get('id')
                        print(f"Contacto_ID: {contact_id}")
 
                        # Consulta de casos
                        url_casos = f'https://api.wcx.cloud/core/v1/cases?sort=asc&sort_field=last_update&limit=10&page=1&filtering=[{{"field":"case.contact_id","operator":"EQUAL","value":"{contact_id}"}}]&fields=id,number,user_id,status,created_at,user_id,source_channel,type_id,tags,channel_account'
                        response_casos = requests.get(url_casos, headers=headers_wise, timeout=settings.REQUESTS_TIMEOUT)
 
                        if response_casos.status_code == 200:
                            data_casos = response_casos.json().get('data', [])
 
                            # Crear un diccionario para almacenar los nombres de usuarios por user_id
                            user_names = {}
                            type_names = {}
 
                            for caso in data_casos:
                                user_id = caso.get('user_id')
                                type_id = caso.get('type_id')
 
                                caso['channel_account'] = nom_afi
 
                                fecha_str = caso.get('created_at')
                                if fecha_str:
                                    # Convertir y reformatear la fecha
                                    fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d %H:%M:%S')
 
                                    # Formato corto: solo día y mes
                                    caso['created_at'] = fecha_obj.strftime('%d-%m')
 
                                    # Formato largo: dia, mes, hora y minutos
                                    caso['created_at_full'] = fecha_obj.strftime('%d-%m | %H:%M')
                                else:
                                    caso['created_at'] = "Fecha no disponible"
                                    caso['created_at_full'] = "Fecha no disponible"
                               
                                # Traducción de estados.
                                status_str = caso.get('status')
                                if status_str:
                                    if caso['status'].lower() == 'closed':
                                        caso['status'] = 'Cerrado'
                                    elif caso['status'].lower() == 'open':
                                        caso['status'] = 'Abierto'
                                    elif caso['status'].lower() == 'onhold':
                                        caso['status'] = 'Espera'
                                    elif caso['status'].lower() == 'solved':
                                        caso['status'] = 'Resuelto'
                                    elif caso['status'].lower() == 'pending':
                                        caso['status'] = 'Pendiente'
                                    else:
                                        pass
 
                                # Usuario/Asesor
                                if user_id not in user_names:
                                    # Realizar solicitud a la API para obtener el nombre del usuario
                                    url_usuario = f'https://api.wcx.cloud/core/v1/users?filtering=[{{"field":"users.id","operator":"EQUAL","value":"{user_id}"}}]&fields=nick,id'
                                    response_usuario = requests.get(url_usuario, headers=headers_wise, timeout=settings.REQUESTS_TIMEOUT)
 
                                    data_user = response_usuario.json()
 
                                    if response_usuario.status_code == 200:
                                        if 'data' in data_user and len(data_user['data']) > 0:
                                            first_item = data_user['data'][0]  # Primer elemento de la lista de resultados
                                            contact_id = first_item.get('id')
                                            contact_nom = first_item.get('nick')
 
                                            # Reemplazar user_id con el nombre del usuario en la data de casos
                                            caso['user_id'] = contact_nom if user_id == contact_id else "Nombre no disponible"
                                        else:
                                            print("No se encontraron datos del nombre de usuario.")
 
                                    elif response_usuario.status_code == 400:
                                        caso['user_id'] = 'Sin asesor'
 
                                    else:
                                        user_names[user_id] = 'N/A'
 
                                # Tipos
                                if type_id not in type_names:
                                    url_tipos = f'https://api.wcx.cloud/core/v1/cases/types?filtering=[{{"field":"types.id","operator":"EQUAL","value":"{type_id}"}}]&fields=id,name'
                                    response_tipos = requests.get(url_tipos, headers=headers_wise, timeout=settings.REQUESTS_TIMEOUT)
 
                                    data_tipos = response_tipos.json()
 
                                    if response_tipos.status_code == 200:
                                        if 'data' in data_tipos and len(data_tipos['data']) > 0:
                                            first_item = data_tipos['data'][0]  # Primer elemento de la lista de resultados
                                            tipo_id = first_item.get('id')
                                            tipo_nom = first_item.get('name')
 
                                            # Reemplazar user_id con el nombre del tipos en la data de casos
                                            caso['type_id'] = tipo_nom if type_id == tipo_id else "Tipo no disponible"
                                        else:
                                            print("No se encontraron datos del nombre de tipos.")
 
                                    elif response_tipos.status_code == 400:
                                        caso['type_id'] = 'Sin tipo'
 
                                    else:
                                        type_names[type_id] = 'N/A'
                               
                                # Convertir fecha_obj a timezone-aware
                                fecha_obj = zona_horaria.localize(fecha_obj)
 
                                # Comparar fechas
                                if tres_meses_antes <= fecha_obj <= fecha_actual:
                                    #print("Fecha OK.")
                                    # Añadir el caso reformateado a la lista
                                    all_cases.append(caso)
                                else:
                                    pass
                                    #print("Fecha INVALIDA.")
                        else:
                            pass
                       
                        # Ordenar los casos por la fecha 'created_at'
                        all_cases = sorted(all_cases, key=lambda x: datetime.strptime(x['created_at_full'], '%d-%m | %H:%M'), reverse=True)


                    else:
                        print(f"No se encontraron datos de contacto para {nro_afi}.")
                
                # Asignar columna al DataFrame
                df_selected["Fecha_alta"] = df_selected["DNI"].map(fechas_alta_dict)

                data = df_selected.to_dict(orient="records")

                for item in data:
                    try:
                        fecha_alta_dt = datetime.strptime(item["Fecha_alta"], "%d-%m-%Y")
                        un_anio_despues = fecha_alta_dt + relativedelta(years=1)
                        hoy = datetime.now()

                        if hoy >= un_anio_despues:
                            item["color_class"] = "texto-verde"
                            item["simbolo"] = "+"
                        else:
                            item["color_class"] = "texto-rojo"
                            item["simbolo"] = "-"

                    except:
                        # Si la fecha está mal o vacía
                        item["color_class"] = "texto-verde"
                        item["simbolo"] = "?"

                # Renderiza la plantilla con ambos conjuntos de datos
                return render(request, self.template_name, {'data': data, 'data_casos': all_cases, 'agentes': data_agegru})
 
            else:
                return render(request, self.template_name, {'error': 'No se encontraron datos para el DNI proporcionado.'}, status=404)
       
        elif response_afiliado.status_code == 400:
            return render(request, self.template_name, {'error': f'El servidor retornó un error 400. Comprueba los parámetros de la solicitud.'}, status=400)
       
        else:
            return render(request, self.template_name, {'error': f'Error en la solicitud. Código de estado: {response_afiliado.status_code}'}, status=500)
       

@method_decorator(requiere_permiso_iframe("vista.retencion"), name="dispatch")
class BuscarRetencionView(ManejoErroresAPIMixin, View):
    template_name = 'rete_produccion.html'
    #template_name = 'rete_reserva.html'

    def obtener_token_wise(self):
        """
        Obtener el token desde el caché o actualizarlo si no existe o ha expirado.
        """
        # Verifica si el token está en el caché
        token = cache.get('api_token')
 
        if token is None:
            # Si el token no está en el caché o ha expirado, actualizar el token
            token = actualizar_token_wise()
            # Almacenar el token en caché con un tiempo de expiración de 50 minutos
            cache.set('api_token', token, timeout=50 * 60)  # 50 minutos = 50 * 60 segundos
 
        return token
   
    def obtener_token_gecros(self):
        # Verifica si el token está en el caché
        token = cache.get('gecros_token')
 
        if token is None:
            # Si el token no está en el caché o ha expirado, actualizar el token
            token = actualizar_token_gecros()
            # Almacenar el token en caché con un tiempo de expiración
            cache.set('gecros_token', token, timeout=1296000)
 
        return token

    def obtener_preex(self):
        # Endpoint y configuración
        url_api = "https://cotizador.nobis.com.ar/api/preex"
        headers = {
            "Content-Type": "application/json",
            'Authorization': 'Bearer 496ae7b9-0787-482e-bbe2-235279237940'
        }

        # Obtener la clave dinámica almacenada en caché
        clave_dinamica_cache = cache.get('clave_dinamica_preex')
        verificacion_diaria = cache.get('verificacion_diaria_preex')

        # Si no se realizó la verificación diaria
        if not verificacion_diaria:
            try:
                # Obtener la clave dinámica actual desde la API
                response = requests.get(url_api, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
                response.raise_for_status()
                response_json = response.json()
                clave_dinamica_actual = next(iter(response_json.keys()))

                # Si no hay clave almacenada o si cambió, actualizar el archivo
                if clave_dinamica_cache != clave_dinamica_actual:
                    print("La clave dinámica ha cambiado. Actualizando archivo...")
                    actualizar_preexistencias()
                    cache.set('clave_dinamica_preex', clave_dinamica_actual, timeout=30 * 24 * 3600)  # 30 días

                # Registrar que se realizó la verificación diaria
                cache.set('verificacion_diaria_preex', True, timeout=24 * 3600)  # 24 horas

            except requests.exceptions.RequestException as e:
                print(f"Error al consultar la clave dinámica: {e}")
                return None

        # Ruta absoluta al archivo preexistencias.json
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        json_path = os.path.join(base_dir, 'home', 'static', 'preexistencias.json')

        # Leer y devolver el contenido del archivo actualizad
        try:
           with open(json_path, 'r', encoding='utf-8') as archivo:
              return json.load(archivo)
        except FileNotFoundError:
           print("Archivo local no encontrado. Ejecutando actualización...")
           actualizar_preexistencias()
           with open(json_path, 'r', encoding='utf-8') as archivo:
              return json.load(archivo)

 
    def get(self, request, dni=None, *args, **kwargs):

        self.obtener_preex() # Verificar lista de patologias

        # Validación de entrada de DNI
        if not dni:
            return HttpResponseBadRequest("Debe proporcionar un DNI.")
        elif len(dni) != 8:
            return render(request, self.template_name, {'error': 'Verifique el número de DNI, debe ser de 8 dígitos.'}, status=400)

        fecha_actual = datetime.now().strftime("%Y-%m-%d")
        #print(f"La fecha actual es {fecha_actual} y el DNI {dni}")
 
        url_afiliado = "https://appmobile.nobissalud.com.ar/api/afiliados/gestionAfiliados"
       
        payload_afiliado = {
            "numero": None,
            "apellido": None,
            "nombre": None,
            "dni": dni,
            "cuil": None,
            "fecha": fecha_actual,
            "incluirGrupoFam": True
        }
 
        payload_json_afiliado = json.dumps(payload_afiliado)
        token_gecros = self.obtener_token_gecros()
 
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token_gecros}"
            }
        
        headers_interno = {
            "Content-Type": "application/json"
            }
       
        # Solicitud a la API de afiliados
        response_afiliado = requests.post(url_afiliado, data=payload_json_afiliado, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
        #print(response_afiliado.text)
 
        if response_afiliado.status_code == 200:
            data_afiliado = response_afiliado.json().get('data', [])
            if data_afiliado:
                df_afiliado = json_normalize(data_afiliado)
                #print(df_afiliado)
                df_selected = df_afiliado[["nombre", "nroAfi", "parentesco", "estadoBenef", "benGrId", "convenio.value"]]
                df_selected.columns = ["Nombre", "DNI", "Parentesco", "Estado", "Grupo", "Condicion"]

                # Crear lista de resultados combinados
                resultados_combinados = []

                # Crear lista de resultado bonificacion y forma de pago
                forma_de_pago_bonif = []

                # Grupo familiar
                grupo = data_afiliado[0].get("benGrId")
                #print(f"Numero de grupo: {grupo}")

                convenio_id = "4"
                for index, row in df_selected.iterrows():
                    titular = row["Parentesco"]
                    if titular == "TITULAR":
                        condicion_inicial = row["Condicion"]
                        #print(f"Condición inicial: {condicion_inicial}")
                        break

                # Tomar DNI del titular
                for x in data_afiliado:
                    if x.get("esTitular"):
                        dni_titular = x.get("nroAfi")
                        break
                        #print(f"Titular encontrado: {dni_titular}")
                    else:
                        dni_titular = 0

                
                url_dniage = f"https://api.nobis.com.ar/dni_agecta/{grupo}"
                response_dniage = requests.get(url_dniage, headers=headers_interno, timeout=settings.REQUESTS_TIMEOUT)
                dni_aux = 0

                if response_dniage.status_code == 200:
                    data_dniage = response_dniage.json()

                    dni_aux = data_dniage[0].get("doc_id") #DNI de agente de cuenta

                else:
                    print("No hay dni de agente de cuenta.")

                hoy = datetime.today()

                if dni_titular != 0:
                    url_deuda = f"https://appmobile.nobissalud.com.ar/api/AgentesCuenta/Deuda/{dni_aux}"
                    response_deuda = requests.get(url_deuda, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
                    if response_deuda.status_code == 200:
                        data_deuda = response_deuda.json()
                        #print(f"Datos de deuda: {data_deuda}")

                        #print(f"dni buscado: {dni_aux}")
                        total_deuda = 0
                        for deuda in data_deuda:
                            fecvenc_dt = datetime.strptime(deuda.get("fecven"), "%d/%m/%Y")
                            if fecvenc_dt < hoy:
                                total_deuda += float(deuda.get("monto"))
                            else:
                                pass
                        total_deuda = total_deuda if total_deuda > 0 else "Sin deuda"
                    else:
                        total_deuda = "Sin dato"
                    
                    # Forma de pago y Bonificacion/Recargo
                    url_bf = f"https://api.nobis.com.ar/fpago_bonif/{grupo}"
                    response_bf = requests.get(url_bf, headers=headers_interno, timeout=settings.REQUESTS_TIMEOUT)

                    if response_bf.status_code == 200:
                        data_bf = response_bf.json()
                        #print(data_bf)

                        if data_bf:

                            fpago = data_bf[0].get("fpago_nombre").replace("  ","")
                            #print(fpago)
                            data_bf[0]["fpago_nombre"] = fpago
                            #print(data_bf[0]["fpago_nombre"])

                            # Bonificacion / Recargo
                            porcentaje = data_bf[0].get("porcentaje")
                            rg_id = data_bf[0].get("rg_id")
                            if porcentaje is not None:
                                # Magnitud para el input "Vigente": solo autollenar bonificaciones manuales (sin rg_id).
                                # Si tiene rg_id (regla/generador automatico, ej. "EMPRESAS AGRO") -> no autorellenar.
                                if rg_id is None and porcentaje < 0:
                                    # int: evita que USE_L10N renderice "5,0" (coma) e invalide el input type=number
                                    data_bf[0]["bonif_vigente_valor"] = int(abs(porcentaje))
                                else:
                                    data_bf[0]["bonif_vigente_valor"] = 0
                                #print(porcentaje)
                                porcentaje = f"{porcentaje / 100:.0%}"
                                #print(porcentaje.replace("-",""))
                                data_bf[0]["porcentaje"] = porcentaje

                                periodo_hasta = data_bf[0].get("peri_hasta")
                                #print(periodo_hasta)
                                periodo_hasta = datetime.strptime(periodo_hasta, '%Y%m') # Formato en el que viene el dato
                                periodo_hasta_format = periodo_hasta.strftime("%m/%Y") # Formato a mostrar
                                #print(periodo_hasta_format)
                                data_bf[0]["peri_hasta"] = periodo_hasta_format

                                # Detalles
                                if data_bf[0]["rg_id"] is not None:
                                    data_bf[0]["BonficaRec_obs"] = data_bf[0]["rg_nombre"]
                                elif data_bf[0]["BonficaRec_obs"] is None:
                                    data_bf[0]["BonficaRec_obs"] = "Manual - Sin observación"
                                else:
                                    pass

                            else:
                                data_bf[0]["peri_hasta"] = 0
                                data_bf[0]["bonif_vigente_valor"] = 0

                            forma_de_pago_bonif.append(data_bf[0])
                            #print(data_bf[0])
                        else:
                            print("La API devolvió una lista vacía.")
                    else:
                        pass

                else:
                    print("GRUPO FAMILIAR SIN TITULAR, REVISAR!")
                    total_deuda = "Sin titular"

                # DESARROLLAR INFO FALTANTE
                cobertura = 0

                for afiliado in data_afiliado:
                    nro_afi = afiliado.get("nroAfi")
                    afiliado_info = {
                        "Nombre": afiliado.get("nombre"),
                        "DNI": nro_afi,
                        "Parentesco": afiliado.get("parentesco"),
                        "Estado": afiliado.get("estadoBenef"),
                        "Deuda": total_deuda,
                        "Patologias": []
                    }

                    if 'con' in afiliado_info["Estado"].lower():
                        #print(f"Con cobertura: {afiliado_info["DNI"]}")

                        cobertura += 1
 
                        if afiliado.get("esTitular"):
                            # Tomar tipo de beneficiario del titular
                            #print(nro_afi)

                            url_tipoben = f"https://api.nobis.com.ar/tipo_ben/{nro_afi}"
                            response_tipoben = requests.get(url_tipoben, headers=headers_interno, timeout=settings.REQUESTS_TIMEOUT)

                            if response_tipoben.status_code == 200:
                                data_tipoben = response_tipoben.json()
                                #print(nro_afi)

                                tipo_beneficiario = data_tipoben[0].get("tipoBen_nom") #DNI de agente de cuenta
                                #print(f"Tipo afi: {tipo_beneficiario}")

                                if tipo_beneficiario:
                                    convenio_id = condicion_grupal(tipo_beneficiario) # Reevaluar convenio y matchear con el ID del html
                                else:
                                    convenio_id = condicion_grupal(condicion_inicial)

                            else:
                                #print("No hay dni de agente de cuenta.")
                                pass
                            
                        else:
                            pass

                        # Solicitud a la API de afiliados para obtener edad y provincia
                        url_afiliado_extra = f"https://appmobile.nobissalud.com.ar/api/afiliados?numero={nro_afi}"
                        response_afiliado_extra = requests.get(url_afiliado_extra, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
                        data_afiliado_extra = response_afiliado_extra.json().get('data', [])
    
                        if data_afiliado_extra:
                            fecha_nacimiento = data_afiliado_extra[0].get('fechaNacimiento')
                            df_afiliado_extra = json_normalize(data_afiliado_extra, record_path=['domicilios'], meta=['benId', 'nombreAfiliado', 'fechaNacimiento'])
                        
                            # Provincia
                            if 'provincia' in df_afiliado_extra.columns:
                                provincia = df_afiliado_extra['provincia'].iloc[0]
                                #print(f"Provincia inicial: {provincia}")
                                afiliado_info['Provincia'] = provincia
                            else:
                                afiliado_info['Provincia'] = 'Sin provincia'
    
                            # Calcular edad
                            if fecha_nacimiento:
                                fecha_nacimiento_dt = datetime.strptime(fecha_nacimiento, "%d/%m/%Y")
                                hoy = datetime.today()
                                edad = hoy.year - fecha_nacimiento_dt.year - ((hoy.month, hoy.day) < (fecha_nacimiento_dt.month, fecha_nacimiento_dt.day))
                                afiliado_info['Edad'] = edad
                            else:
                                afiliado_info['Edad'] = "Sin fecha de nacimiento"

                        # Solicitud a la API interna para obtener fecha de alta y patologia
                        url_patologias = f"https://api.nobis.com.ar/fecha_alta_y_patologias/{nro_afi}"
                        response_p = requests.get(url_patologias, headers=headers_interno, timeout=settings.REQUESTS_TIMEOUT)
                        data_p = response_p.json()

                        if isinstance(data_p, list) and data_p:
                            
                            # Fecha de alta
                            fecha_alta = data_p[0].get('fecha_alta')
                            fecha_alta_format = datetime.strptime(fecha_alta, '%Y-%m-%dT%H:%M:%S.000')

                            fecha_formateada = fecha_alta_format.strftime('%d-%m-%Y')

                            afiliado_info['Fecha_alta'] = fecha_formateada

                            patologia = data_p[0].get('cobertura_especial')
                            patologias_id = data_p[0].get('id_cobertura_especial')
                            
                            if patologia != None:
                                #print(f"Patologia: {patologia}")
                                busqueda = buscar_cobertura(patologia)
                                #print(f"Primera busqueda: {busqueda}")
                                if busqueda == []:
                                    #print(f"PB = [] -> Iniciando segunda busqueda por ID: {patologias_id}")
                                    busqueda = buscar_preexistencias(patologias_id)
                                    #print(f"Resultado de busqueda de preexistencias: {busqueda}")
                                
                                if busqueda == "No se encontraron coincidencias.":
                                    afiliado_info['Patologias'] = []
                                    afiliado_info['Cobertura_especial'] = patologia
                                    #print("No se encontraron coincidencias de homologación. - Cobertura especial:", patologia)
                                else:
                                    afiliado_info['Patologias'] = busqueda
                                    afiliado_info['Cobertura_especial'] = patologia
                                    #print(f"Encontrado: {busqueda} - Cob: {patologia}")
                            else:
                                afiliado_info['Cobertura_especial'] = 'Sin cobertura especial'
                                #print("SIN COBERTURA ESPECIAL")
                        else:
                            print("Error. Fecha de alta no encontrada.")

                        resultados_combinados.append(afiliado_info)
                    else:
                        #print(f"Afiliado sin cobertura: {afiliado_info["DNI"]}")
                        pass
                
                if cobertura == 0:
                    print("⚠️ No hay afiliados con cobertura dentro del grupo familiar ⚠️")
                    dni_titular = dni

                # Aportes
                all_aportes = []

                url_aportes = f"https://api.nobis.com.ar/ultimos_aportes/{dni_titular}"
                #url_aportes = f"http://127.0.0.1:8080/ultimos_aportes/{dni_titular}"
                response_a = requests.get(url_aportes, headers=headers_interno, timeout=settings.REQUESTS_TIMEOUT)
                data_a = response_a.json()

                #print(f"Datos de aportes: {data_a}")

                cont = 0

                if isinstance(data_a, list) and data_a:
                    for aporte in data_a:
                        if cont != 5:
                            # Supongamos que `periodo` es una cadena como '202410'
                            periodo = aporte.get('Periodo')

                            # Convertir el período a un objeto datetime
                            fecha = datetime.strptime(periodo, '%Y%m')

                            # Sumar 2 meses usando relativedelta
                            nueva_fecha = fecha + relativedelta(months=2)

                            # Convertir de nuevo a formato 'YYYYMM'
                            nuevo_periodo = nueva_fecha.strftime('%Y%m')

                            aporte['Periodo'] = nuevo_periodo

                            monto = aporte.get('aporte')
                            #print(monto)

                            #dni = aporte.get('numero').replace(' ', '')
                            #aporte['numero'] == dni
                            #print(dni)

                            # ----- Retención de aportes -----
                            #mes_actual = datetime.now().strftime('%m')
                            #if mes_actual in ('08', '03'):
                            #    aporte['aporte_base'] = aporte.get('aporte')
                            #    nuevo_monto = round(aporte.get('aporte') * 0.7, 2)
                            #    aporte['Retencion'] = "30%"
                            #else:
                            #    aporte['aporte_base'] = aporte.get('aporte')
                            #    nuevo_monto = round(aporte.get('aporte') * 0.9, 2)
                            #    aporte['Retencion'] = "10%"
                            #aporte['aporte'] = nuevo_monto
                            # ----------------------------------

                            aporte['aporte_base'] = aporte.get('aporte')

                            if monto > 1:
                                all_aportes.append(aporte)
                                cont+=1
                            else:
                                pass
                        else:
                            break
                else:
                    pass
                

                if cobertura == 0:
                    return render(request, self.template_name, {'data': resultados_combinados, 'data_aportes': all_aportes})
                
                # Ordenar por parentesco
                orden_parentesco = {"TITULAR": 1, "CONYUGE": 2, "HIJO/A": 3, "FAMILIAR A CARGO": 4}
                for afiliado in resultados_combinados:
                    afiliado['Parentesco_Orden'] = orden_parentesco.get(afiliado['Parentesco'], 99)  # 99 para no asignar un orden conocido
 
                resultados_combinados.sort(key=lambda x: x['Parentesco_Orden'])
                for afiliado in resultados_combinados:
                    del afiliado['Parentesco_Orden']  # Eliminar el campo de ordenamiento antes de renderizar
                
                #print(resultados_combinados)

                # Renderiza la plantilla con ambos conjuntos de datos
                return render(request, self.template_name, {'data': resultados_combinados, 'data_aportes': all_aportes, 'data_fpago': forma_de_pago_bonif, "convenio_id": convenio_id, "convenio_nom":condicion_inicial, "tipo_nom":tipo_beneficiario, "grupo": grupo, "dni_titular": dni_titular})
            
            else:
                return render(request, self.template_name, {'error': 'No se encontraron datos para el DNI proporcionado.'}, status=404)
            
        elif response_afiliado.status_code == 400:
            return render(request, self.template_name, {'error': f'El servidor retornó un error 400. Comprueba los parámetros de la solicitud.'}, status=400)
       
        else:
            return render(request, self.template_name, {'error': f'Error en la solicitud. Código de estado: {response_afiliado.status_code}'}, status=500)
        

@method_decorator(requiere_permiso_iframe("vista.mesa"), name="dispatch")
class MesaDeEntradaView(ManejoErroresAPIMixin, View):
    template_carga_expediente = 'nuevo_expediente.html'
    template_expedientes = 'expedientes.html'
    template_error = template_carga_expediente

    def obtener_token_gecros(self):
        # Verifica si el token está en el caché
        token = cache.get('gecros_token')
 
        if token is None:
            # Si el token no está en el caché o ha expirado, actualizar el token
            token = actualizar_token_gecros()
            # Almacenar el token en caché con un tiempo de expiración
            cache.set('gecros_token', token, timeout=1296000)
 
        return token


    def get(self, request, dni=None, *args, **kwargs):

        print("Cargando vista de mesa de entrada...")

        OrigenesService.obtener_origenes()
        ProveedoresService.obtener_proveedores()
        GeneradoresService.obtener_generadores()

        # Validación de entrada de DNI
        if not dni:
            return HttpResponseBadRequest("Debe proporcionar un DNI.")
        elif len(dni) != 8:
            return render(request, self.template_carga_expediente, {'error': 'Verifique el número de DNI, debe ser de 8 dígitos.'}, status=400)
        
        fecha_actual = datetime.now().strftime("%Y-%m-%d")
        #print(f"La fecha actual es {fecha_actual} y el DNI {dni}")
 
        url_afiliado = "https://appmobile.nobissalud.com.ar/api/afiliados/gestionAfiliados"
       
        payload_afiliado = {
            "numero": None,
            "apellido": None,
            "nombre": None,
            "dni": dni,
            "cuil": None,
            "fecha": fecha_actual,
            "incluirGrupoFam": True
        }
       
        payload_json_afiliado = json.dumps(payload_afiliado)
        token_gecros = self.obtener_token_gecros()
 
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token_gecros}"
            }
       
        # Solicitud a la API de afiliados
        response_afiliado = requests.post(url_afiliado, data=payload_json_afiliado, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
        #print(response_afiliado.text)
 
        if response_afiliado.status_code == 200:
            data_afiliado = response_afiliado.json().get('data', [])
            if data_afiliado:
                df_afiliado = json_normalize(data_afiliado)
                df_selected = df_afiliado[["benId", "nombre", "nroAfi", "parentesco"]]
                df_selected.columns = ["benId","Nombre", "DNI", "Parentesco"]

                ben_ids = df_selected["benId"].tolist() # Obtiene todos los BenID del grupo familiar
                benid_to_dni = dict(zip(df_selected["benId"], df_selected["DNI"]))
                benid_to_nombre = dict(zip(df_selected["benId"], df_selected["Nombre"]))

                crear_nuevo = request.GET.get('nuevo', '0') == '1'

                expedientes = obtener_expedientes_grupo_familiar(ben_ids, token_gecros, benid_to_dni, benid_to_nombre)
                #print("Expedientes:", expedientes)

                if not crear_nuevo:
                    
                    expedientes_por_afiliado = defaultdict(list)
                    for exp in expedientes:
                        #print(exp.keys())
                        #print(exp)
                        key = f"{exp['Nombre']}"
                        #key = f"{exp['Nombre']} ({exp['DNI']})"
                        expedientes_por_afiliado[key].append(exp)

                    #print("Expedientes por afiliado:", dict(expedientes_por_afiliado))
                    afiliados = list(expedientes_por_afiliado.keys())
                    afiliados.sort(key=lambda x: (dni not in x, x))

                    afiliados_data = [(afiliado, expedientes_por_afiliado[afiliado]) for afiliado in afiliados]
                    
                    #print(afiliados_data)
                    
                    return render(request, self.template_expedientes, {
                        'afiliados_data': afiliados_data,
                        'dni': dni
                    })

                dni_aux = 0
                # Solicitud a la API de deuda
                for x in data_afiliado:
                    if x.get("esTitular") == True:
                        dni_aux = x.get("nroAfi")
                        print(f"Titular detectado: {dni_aux}")
                        break
                    else:
                        pass
                
                total_deuda = 0
                if dni_aux != 0:
                    url_deuda = f"https://appmobile.nobissalud.com.ar/api/AgentesCuenta/Deuda/{dni_aux}"
                    response_deuda = requests.get(url_deuda, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
                    try:
                        data_deuda = response_deuda.json()
                    except ValueError:
                        data_deuda = []

                    hoy = datetime.today()

                    if response_deuda.status_code == 200 and data_deuda != []:
                        for deuda in data_deuda:
                            fecven_dt = datetime.strptime(deuda.get("fecven"), "%d/%m/%Y")
                            if fecven_dt < hoy:
                                total_deuda = sum(float(item.get("monto", 0)) for item in data_deuda)
                                total_deuda = "SI" if total_deuda > 0 else "NO"
                                break
                            else:
                                total_deuda = "NO"
                    
                    elif response_deuda.status_code != 200:
                        total_deuda = "Sin dato"

                    else:
                        total_deuda = "NO"
                else:
                    print("GRUPO FAMILIAR SIN TITULAR, REVISAR!")
                    total_deuda = "Sin titular"
 
                df_selected = df_selected.copy()
                df_selected["Deuda"] = total_deuda
 
                # Ordenar por parentesco
                orden_parentesco = {"TITULAR": 1, "CONYUGE": 2, "HIJO/A": 3, "FAMILIAR A CARGO": 4}
                df_selected["Parentesco_Orden"] = df_selected["Parentesco"].map(orden_parentesco)
                df_selected = df_selected.sort_values(by=["Parentesco_Orden"]).drop("Parentesco_Orden", axis=1)
                #data = df_selected.to_dict(orient="records")
 
                zona_horaria = pytz.timezone('America/Argentina/Buenos_Aires')
               
                # Fecha actual
                fecha_actual = datetime.now(zona_horaria)
 
                # Restar 3 meses a la fecha actual
                tres_meses_antes = fecha_actual - relativedelta(months=3)

                fechas_alta_dict = {}

                for afiliado in data_afiliado:
                    nro_afi = afiliado.get("nroAfi")
                    nom_afi = afiliado.get("nombre")

                    headers_interno = {
                    "Content-Type": "application/json"
                    }

                    # Solicitud a la API interna para obtener fecha de alta y patologia
                    url_patologias = f"https://api.nobis.com.ar/fecha_alta_y_patologias/{nro_afi}"
                    response_p = requests.get(url_patologias, headers=headers_interno, timeout=settings.REQUESTS_TIMEOUT)
                    data_p = response_p.json()

                    if isinstance(data_p, list) and data_p:
                        fecha_alta = data_p[0].get('fecha_alta')
                        fecha_alta_format = datetime.strptime(fecha_alta, '%Y-%m-%dT%H:%M:%S.000')
                        fecha_formateada = fecha_alta_format.strftime('%d-%m-%Y')
                        fechas_alta_dict[nro_afi] = fecha_formateada
                    else:
                        fechas_alta_dict[nro_afi] = "Sin dato"
                
                # Asignar columna al DataFrame
                df_selected["Fecha_alta"] = df_selected["DNI"].map(fechas_alta_dict)

                data = df_selected.to_dict(orient="records")

                for item in data:
                    try:
                        fecha_alta_dt = datetime.strptime(item["Fecha_alta"], "%d-%m-%Y")
                        un_anio_despues = fecha_alta_dt + relativedelta(years=1)
                        hoy = datetime.now()

                        if hoy >= un_anio_despues:
                            item["color_class"] = "texto-verde"
                            item["simbolo"] = "+"
                        else:
                            item["color_class"] = "texto-rojo"
                            item["simbolo"] = "-"

                    except:
                        # Si la fecha está mal o vacía
                        item["color_class"] = "texto-verde"
                        item["simbolo"] = "?"

                return render(request, self.template_carga_expediente, {'afiliado': data})
 
            else:
                return render(request, self.template_carga_expediente, {'error': 'No se encontraron datos para el DNI proporcionado.'}, status=404)
       
        elif response_afiliado.status_code == 400:
            return render(request, self.template_carga_expediente, {'error': f'El servidor retornó un error 400. Comprueba los parámetros de la solicitud.'}, status=400)
       
        else:
            return render(request, self.template_carga_expediente, {'error': f'Error en la solicitud. Código de estado: {response_afiliado.status_code}'}, status=500)


# COTIZADOR
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

# cotizar_anterior: eliminado (dead code — no tenía ruta en urls.py y quedaba sin gate).


@requiere_permiso_json("accion.cotizar")
@csrf_exempt
def cotizar_actual(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        plan = data.get('plan')
        gestion = data.get('gestion')
        ubicacion = data.get('ubicacion')
        aporte = data.get('aportes')
        bonificacion = data.get('bonificaciones')
        patologias = data.get('patologias')
        edades = ','.join(data.get('edades', []))

        if not all([plan, gestion, ubicacion, edades]):
            return JsonResponse({"error": "Faltan parámetros"}, status=400)

        print(aporte, bonificacion, patologias)
        token = "496ae7b9-0787-482e-bbe2-235279237940"

        # Resumen para el AuditLog (no incluye el token).
        resumen = {"plan": plan, "gestion": gestion, "ubicacion": ubicacion,
                   "edades": edades, "aportes": aporte, "bonificaciones": bonificacion,
                   "patologias": patologias}

        def consultar_api():
            url_api = (
                f"https://cotizador.nobis.com.ar/cotizacion?"
                f"planes={plan}&convenio={gestion}&provincia={ubicacion}"
                f"&ages={edades}&directo={aporte}&descuento={bonificacion}&preexistencia={patologias}"
            )
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            }
            response = requests.get(url_api, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
            return response

        try:
            response = consultar_api()
            if response.status_code != 200:
                error_data = response.json()
                registrar(request, action="cotizar", recurso="accion.cotizar", target_type="cotizacion",
                          response_status=response.status_code, success=False,
                          error_detail=str(error_data), payload_summary=resumen)
                return JsonResponse({"error": f"{error_data}"}, status=400)
            else:
                response_data = response.json()
                valor_pc = response_data['resultado']['cotizacion']['planes'][0]['primera_cuota']
                valor_p = response_data['resultado']['cotizacion']['planes'][0]['valor_plan']

                registrar(request, action="cotizar", recurso="accion.cotizar", target_type="cotizacion",
                          response_status=200, success=True,
                          payload_summary={**resumen, "primera_cuota": valor_pc, "valor_plan": valor_p})
                return JsonResponse({
                    "primera_cuota": valor_pc,
                    "valor_plan": valor_p
                })

        except Exception as e:
            registrar(request, action="cotizar", recurso="accion.cotizar", target_type="cotizacion",
                      success=False, error_detail=str(e), payload_summary=resumen)
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Método no permitido"}, status=405)


class OrigenesService:
    @staticmethod
    def obtener_origenes():
        url_api = "https://api.nobis.com.ar/origenes/1"
        headers = {
            "Content-Type": "application/json"
        }

        # Ruta absoluta al archivo origenes.json
        json_path = os.path.join(settings.BASE_DIR, 'home', 'static', 'origenes.json')
        archivo_existe = os.path.exists(json_path)

        verificacion_semanal = cache.get('verificacion_semanal_origenes')

        # Si no se realizó la verificación semanal, consultamos la API y actualizamos el archivo
        if not archivo_existe or not verificacion_semanal:
            try:
                response = requests.get(url_api, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
                response.raise_for_status()
                data = response.json()
                #print("Actualizando archivo JSON de origenes")

                # Guardar el JSON recibido en el archivo
                with open(json_path, 'w', encoding='utf-8') as archivo:
                    json.dump(data, archivo, ensure_ascii=False, indent=2)

                # Registrar que se realizó la verificación semanal (7 días)
                cache.set('verificacion_semanal_origenes', True, timeout=7 * 24 * 3600)

            except requests.exceptions.RequestException as e:
                print(f"Error al consultar la API: {e}")
                # Si hay error y el archivo existe, devolvemos el último dato guardado
                if os.path.exists(json_path):
                    with open(json_path, 'r', encoding='utf-8') as archivo:
                        return json.load(archivo)
                return None

        # Leer y devolver el contenido del archivo actualizado
        try:
            with open(json_path, 'r', encoding='utf-8') as archivo:
                return json.load(archivo)
        except FileNotFoundError:
            print("Archivo local no encontrado y no se pudo actualizar desde la API.")
            return None
        

class ProveedoresService:
    @staticmethod
    def obtener_proveedores():
        url_api = "https://api.nobis.com.ar/proveedores/1"
        headers = {
            "Content-Type": "application/json"
        }

        # Ruta absoluta al archivo origenes.json
        json_path = os.path.join(settings.BASE_DIR, 'home', 'static', 'proveedores.json')
        archivo_existe = os.path.exists(json_path)

        verificacion_semanal = cache.get('verificacion_semanal_proveedores')

        # Si no se realizó la verificación semanal, consultamos la API y actualizamos el archivo
        if not archivo_existe or not verificacion_semanal:
            try:
                response = requests.get(url_api, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
                response.raise_for_status()
                data = response.json()
                #print("Actualizando archivo JSON de proveedores")

                # Guardar el JSON recibido en el archivo
                with open(json_path, 'w', encoding='utf-8') as archivo:
                    json.dump(data, archivo, ensure_ascii=False, indent=2)

                # Registrar que se realizó la verificación semanal (7 días)
                cache.set('verificacion_semanal_proveedores', True, timeout=7 * 24 * 3600)

            except requests.exceptions.RequestException as e:
                print(f"Error al consultar la API: {e}")
                # Si hay error y el archivo existe, devolvemos el último dato guardado
                if os.path.exists(json_path):
                    with open(json_path, 'r', encoding='utf-8') as archivo:
                        return json.load(archivo)
                return None

        # Leer y devolver el contenido del archivo actualizado
        try:
            with open(json_path, 'r', encoding='utf-8') as archivo:
                return json.load(archivo)
        except FileNotFoundError:
            print("Archivo local no encontrado y no se pudo actualizar desde la API.")
            return None


class GeneradoresService:
    @staticmethod
    def obtener_generadores():
        url_api = "https://api.nobis.com.ar/generadores/38"
        headers = {
            "Content-Type": "application/json"
        }

        # Ruta absoluta al archivo generadores.json
        json_path = os.path.join(settings.BASE_DIR, 'home', 'static', 'generadores.json')
        archivo_existe = os.path.exists(json_path)

        verificacion_semanal = cache.get('verificacion_semanal_generadores')

        # Si no se realizó la verificación semanal, consultamos la API y actualizamos el archivo
        if not archivo_existe or not verificacion_semanal:
            try:
                response = requests.get(url_api, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
                response.raise_for_status()
                data = response.json()
                #print("Actualizando archivo JSON de generadores")

                # Guardar el JSON recibido en el archivo
                with open(json_path, 'w', encoding='utf-8') as archivo:
                    json.dump(data, archivo, ensure_ascii=False, indent=2)

                # Registrar que se realizó la verificación semanal (7 días)
                cache.set('verificacion_semanal_generadores', True, timeout=7 * 24 * 3600)

            except requests.exceptions.RequestException as e:
                print(f"Error al consultar la API: {e}")
                # Si hay error y el archivo existe, devolvemos el último dato guardado
                if os.path.exists(json_path):
                    with open(json_path, 'r', encoding='utf-8') as archivo:
                        return json.load(archivo)
                return None

        # Leer y devolver el contenido del archivo actualizado
        try:
            with open(json_path, 'r', encoding='utf-8') as archivo:
                return json.load(archivo)
        except FileNotFoundError:
            print("Archivo local no encontrado y no se pudo actualizar desde la API.")
            return None


@requiere_permiso_json("accion.crear_expediente")
@csrf_exempt
def guardar_expediente(request):
    if request.method == "POST":
            
            data = json.loads(request.body)

            benId = data.get("benId")
            oriId = data.get("oriId")
            provId = data.get("provId")
            genId = data.get("genId")
            mTipoExpId = data.get("mTipoExpId")
            observaciones = data.get("observaciones", "")
            periodo = data.get("periodo")

            # Atribución: Gecros es de un tercero y solo acepta texto libre en observaciones.
            # BenefUserId queda None (no hay mapeo Azure->Gecros). El registro confiable es el AuditLog local.
            usuario = etiqueta_usuario(request)
            observaciones_ext = (f"{observaciones}\n[Cargado por {usuario}]" if observaciones
                                 else f"[Cargado por {usuario}]")

            url = "https://appmobile.nobissalud.com.ar/api/Expedientes"

            payload = json.dumps({
                "BenefUserId": None,
                "benId": int(benId) if benId else None,
                "oriId": int(oriId) if oriId else None,
                "provId": int(provId) if provId else None,
                "mTipoExpId": int(mTipoExpId) if mTipoExpId else None,
                "appCode": None,
                "observaciones": observaciones_ext,
                "periodo": periodo
            })

            token = cache.get('gecros_token')

            headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
            }
            
            try:
                response = requests.request("POST", url, headers=headers, data=payload, timeout=settings.REQUESTS_TIMEOUT)
                #print("Body enviado:", payload)

                if response.status_code == 200:
                    expediente_data = response.json()
                    expediente_id = expediente_data.get("data")
                    advertencia = None

                    # Si se seleccionó un generador, asociarlo al expediente recién creado.
                    if genId and int(genId) != 0:
                        # Gate inline: la asignación de generador es un recurso RBAC aparte.
                        if not usuario_puede(request, "accion.asignar_generador"):
                            advertencia = "Expediente creado, pero no tenés permiso para asignar generador."
                        else:
                            try:
                                generador_url = f"https://api.nobis.com.ar/generador_exp/{expediente_id}?generador_id={int(genId)}"
                                generador_response = requests.put(generador_url, timeout=settings.REQUESTS_TIMEOUT)

                                if generador_response.status_code == 409:
                                    advertencia = "Expediente creado, el generador actual es el mismo."
                                elif generador_response.status_code != 200:
                                    advertencia = "Expediente creado, pero no se pudo asociar el generador."
                                else:
                                    print(f"Generador {genId} asignado correctamente al expediente {expediente_id}")
                                registrar(request, action="asignar_generador", recurso="accion.asignar_generador", target_type="expediente",
                                          target_id=expediente_id, response_status=generador_response.status_code,
                                          success=(generador_response.status_code == 200),
                                          payload_summary={"generador_id": int(genId)})
                            except Exception as gen_error:
                                advertencia = f"Expediente creado, pero no se pudo asociar el generador: {str(gen_error)}"

                    registrar(request, action="crear_expediente", recurso="accion.crear_expediente", target_type="expediente",
                              target_id=expediente_id, response_status=200, success=True,
                              payload_summary={"benId": benId, "oriId": oriId, "provId": provId,
                                               "mTipoExpId": mTipoExpId, "periodo": periodo})
                    return JsonResponse({"success": True, "data": expediente_data, "advertencia": advertencia})
                else:
                    registrar(request, action="crear_expediente", recurso="accion.crear_expediente", target_type="expediente",
                              response_status=response.status_code, success=False,
                              error_detail=response.text)
                    return JsonResponse({"success": False, "error": response.text}, status=400)
            except Exception as e:
                registrar(request, action="crear_expediente", recurso="accion.crear_expediente", target_type="expediente",
                          success=False, error_detail=str(e))
                return JsonResponse({"success": False, "error": str(e)}, status=500)
    else:
        return JsonResponse({"success": False, "error": "Método no permitido"}, status=405)
    

@requiere_permiso_json("accion.subir_archivo")
@csrf_exempt
def archivo_expediente(request):
    if request.method == "POST":
        expediente_id = request.POST.get('expedienteId')
        archivo = request.FILES.get('file')
        if not expediente_id or not archivo:
            return JsonResponse({'success': False, 'error': 'Faltan datos'}, status=400)

        token = cache.get('gecros_token')

        files = {'file': (archivo.name, archivo.read(), archivo.content_type)}
        url = f"https://appmobile.nobissalud.com.ar/api/archivo/SaveArchivo?mExpId={expediente_id}"
        headers = {'Authorization': f'Bearer {token}'}

        try:
            response = requests.post(url, files=files, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
            ok = response.status_code == 200
            # SaveArchivo (Gecros, tercero) no acepta identidad de usuario -> solo AuditLog local.
            registrar(request, action="subir_archivo", recurso="accion.subir_archivo", target_type="expediente",
                      target_id=expediente_id, response_status=response.status_code, success=ok,
                      payload_summary={"filename": archivo.name, "content_type": archivo.content_type},
                      error_detail="" if ok else response.text)
            if ok:
                return JsonResponse({'success': True, 'data': response.json()})
            else:
                return JsonResponse({'success': False, 'error': response.text}, status=400)
        except Exception as e:
            registrar(request, action="subir_archivo", recurso="accion.subir_archivo", target_type="expediente",
                      target_id=expediente_id, success=False, error_detail=str(e))
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    else:
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)
    

from django.http import HttpResponse
EXTENSION_TO_CONTENT_TYPE = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'gif': 'image/gif',
    'pdf': 'application/pdf',
    'txt': 'text/plain',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}


@requiere_permiso_json("vista.mesa")
@csrf_exempt
def descargar_adjunto(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            archivo_id = data.get("archivoId")
            if not archivo_id:
                return JsonResponse({"error": "Falta archivoId"}, status=400)

            token = cache.get('gecros_token')

            # Llamar a la API externa
            api_url = f"https://appmobile.nobissalud.com.ar/api/Archivo/get-img/{archivo_id}"
            headers = {'Authorization': f'Bearer {token}'}
            api_response = requests.get(api_url, stream=True, headers=headers, timeout=settings.REQUESTS_TIMEOUT)

            if api_response.status_code != 200:
                return JsonResponse({"error": "No se pudo obtener el archivo"}, status=404)

            # Intentar obtener el nombre de archivo del header, si lo hay
            content_disposition = api_response.headers.get('Content-Disposition')
            if content_disposition and 'filename=' in content_disposition:
                filename = content_disposition.split('filename=')[1].strip('"')
            else:
                # Si no viene, usar un nombre genérico
                filename = f"archivo_{archivo_id}"

            # Detectar el tipo de archivo
            content_type = api_response.headers.get('Content-Type', 'application/octet-stream')

            response = HttpResponse(api_response.content, content_type=content_type)
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"error": "Método no permitido"}, status=405)


@requiere_permiso_json("vista.mesa")
@csrf_exempt
def previsualizar_adjunto(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            archivo_id = data.get("archivoId")
            extension = data.get("extension", "").lower()

            if not archivo_id or not extension:
                return JsonResponse({"error": "Faltan datos"}, status=400)
            
            token = cache.get('gecros_token')
            
            api_url = f"https://appmobile.nobissalud.com.ar/api/Archivo/get-img/{archivo_id}"
            headers = {'Authorization': f'Bearer {token}'}
            api_response = requests.get(api_url, stream=True, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
            
            if api_response.status_code != 200:
                return JsonResponse({"error": "No se pudo obtener el archivo"}, status=404)
            
            content_type = EXTENSION_TO_CONTENT_TYPE.get(extension, 'application/octet-stream')
            
            return HttpResponse(api_response.content, content_type=content_type)
        except Exception as e:
            
            return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"error": "Método no permitido"}, status=405)


@requiere_permiso_json("vista.mesa")
@csrf_exempt
def buscar_beneficiario(request):
    if request.method == "GET":
        try:
            numero = request.GET.get('numero')
            if not numero:
                return JsonResponse({'error': 'No se proporcionó número'}, status=400)
            
            api_url = f"https://api.nobis.com.ar/obtener_beneficiario/{numero}"
            api_response = requests.get(api_url, timeout=settings.REQUESTS_TIMEOUT)
            
            if api_response.status_code != 200:
                return JsonResponse({"error": "No se pudo obtener el dato"}, status=404)
            
            # Parsear el JSON de la respuesta
            data = api_response.json()
            if not data or not isinstance(data, list) or not data[0]:
                return JsonResponse({"error": "No se encontró el beneficiario"}, status=404)
            
            # Extraer ben_id y nombre
            ben_id = data[0].get("ben_id")
            nombre = data[0].get("nombre")
            if ben_id is None or nombre is None:
                return JsonResponse({"error": "Datos incompletos"}, status=404)
            
            return JsonResponse({
                "ben_id": ben_id,
                "nombre": nombre
            })
        
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Método no permitido'}, status=405)


import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json

@requiere_permiso_json("accion.crear_remito")
@csrf_exempt
def crear_remito(request, expediente_id):
    if request.method == "POST":
        try:
            body = json.loads(request.body.decode("utf-8"))
            sector_destino = body.get("sector_destino")
            generador_id = body.get("generador_id", 0)
            observaciones = body.get("observaciones", None)

            if not sector_destino:
                return JsonResponse({"error": "Debe indicar un sector destino"}, status=400)

            # API propia de Nobis: mandar el usuario real; se omite el campo si no hay.
            usuario = usuario_para_api(request)

            api_url = f"https://api.nobis.com.ar/crear_remito/{expediente_id}"

            payload = {
                "sector_destino": sector_destino,
                "observaciones": observaciones if observaciones else None,
            }
            if usuario:
                payload["usuario"] = usuario

            api_response = requests.post(api_url, json=payload, timeout=settings.REQUESTS_TIMEOUT)

            if api_response.status_code != 200:
                registrar(request, action="crear_remito", recurso="accion.crear_remito", target_type="expediente",
                          target_id=expediente_id, response_status=api_response.status_code,
                          success=False, error_detail=api_response.text)
                return JsonResponse({"error": "Error al crear remito en API externa"}, status=api_response.status_code)

            data = api_response.json()
            registrar(request, action="crear_remito", recurso="accion.crear_remito", target_type="remito",
                      target_id=data.get("mRem_id"), response_status=200, success=True,
                      payload_summary={"expediente_id": expediente_id, "sector_destino": sector_destino})
            #print(data)

            #print(f"Remito ID: {data.get("mRem_id")}")

            # Variable para almacenar advertencias
            advertencia = None

            # Si hay un generador_id válido (distinto de 0), llamar a la API de generador
            if generador_id and generador_id != 0 and not usuario_puede(request, "accion.asignar_generador"):
                advertencia = f"Remito creado ID: {data.get('mRem_id')}, pero no tenés permiso para asignar generador."
            elif generador_id and generador_id != 0:
                try:
                    print(f"Expediente {expediente_id} y generador {generador_id}")
                    generador_url = f"https://api.nobis.com.ar/generador_exp/{expediente_id}?generador_id={generador_id}"
                    generador_response = requests.put(generador_url, timeout=settings.REQUESTS_TIMEOUT)

                    if generador_response.status_code == 409:
                        advertencia = f"Remito creado ID: {data.get('mRem_id')}, el generador actual es el mismo."
                        print(f"Advertencia: {advertencia}")
                    elif generador_response.status_code != 200:
                        advertencia = f"Remito creado ID: {data.get('mRem_id')}, pero no se pudo asociar el generador al expediente."
                        print(f"Advertencia: {advertencia}")
                    else:
                        print(f"Generador {generador_id} asignado correctamente al expediente {expediente_id}")
                    registrar(request, action="asignar_generador", recurso="accion.asignar_generador", target_type="expediente",
                              target_id=expediente_id, response_status=generador_response.status_code,
                              success=(generador_response.status_code == 200),
                              payload_summary={"generador_id": generador_id})
                except Exception as gen_error:
                    advertencia = f"Remito creado, pero no se pudo asociar el generador al expediente: {str(gen_error)}"
                    print(f"Advertencia: {advertencia}")

            response_data = {
                "mensaje": "Remito creado correctamente",
                "data": data
            }

            # Agregar advertencia si existe
            if advertencia:
                response_data["advertencia"] = advertencia

            return JsonResponse(response_data)

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse({'error': 'Método no permitido'}, status=405)


from django.http import JsonResponse

@requiere_permiso_json("vista.mesa")
def obtener_destinos(request, sector_origen):
    """
    Devuelve los posibles sectores destino según el sector de origen.
    No consulta API externa; usa un diccionario local.
    """
    try:
        # Diccionario manual de rutas posibles
        destinos_por_sector = {
            9: [  # Agentes de Atención
                {"id": 5, "nombre": "Gestión de Padrón"},
                {"id": 14, "nombre": "Afiliaciones"},
                {"id": 18, "nombre": "Gestión de Prestaciones"},
                {"id": 21, "nombre": "Compras Medicas"},
                {"id": 26, "nombre": "Auditor Presencial SZC"},
                {"id": 27, "nombre": "Agente Interna"},
                {"id": 28, "nombre": "Auditor Presencial SZN"},
                {"id": 29, "nombre": "Auditor CAT"},
                {"id": 30, "nombre": "Auditor Region la Chaya"},
                {"id": 31, "nombre": "Auditor Región Centro"},
                {"id": 33, "nombre": "Auditor Preferencial"},
                {"id": 38, "nombre": "Carga App - Autorizado"},
                {"id": 39, "nombre": "Carga App - Rechazado"},
                {"id": 40, "nombre": "Auditor CAV"},
                {"id": 41, "nombre": "Auditor Región Este"},
                {"id": 42, "nombre": "Auditor Region Madre de Ciudades"},
                {"id": 43, "nombre": "Auditor Region Jardin de la Rep"},
                {"id": 44, "nombre": "Auditor Region La linda"},
                {"id": 45, "nombre": "Soporte de Atención"},
                {"id": 46, "nombre": "ACO Autorizados"},
                {"id": 55, "nombre": "Fidelización y Continuidad"},
                {"id": 56, "nombre": "Auditor odonto Centro"},
                {"id": 57, "nombre": "Auditor odonto NOA"},
                {"id": 59, "nombre": "Repositorio de tramites"},
                {"id": 60, "nombre": "Repositorio de tramites rechazados"},
                {"id": 61, "nombre": "Auditor reintegros APP"},
                {"id": 68, "nombre": "Afiliaciones NOA"},
                {"id": 70, "nombre": "Afiliaciones Centro y Cuyo"},
                {"id": 73, "nombre": "Pendiente de pago"},
                {"id": 74, "nombre": "Tramites Desestimados"},
                {"id": 78, "nombre": "Rechazo Reintegro App"},
            ],
            55: [  # Fidelización y continuidad
                {"id": 5, "nombre": "Gestión de Padrón"},
                {"id": 60, "nombre": "Repositorio de tramites rechazados"},
            ],
            # Podés seguir agregando sectores aquí si es necesario
        }

        sector_origen = int(sector_origen)
        destinos = destinos_por_sector.get(sector_origen, [])

        if not destinos:
            return JsonResponse({"error": "No hay destinos configurados para este sector"}, status=404)

        return JsonResponse({"destinos": destinos})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

    
def obtener_generadores(request, sector_origen):
    """
    Devuelve los posibles generadores según el sector de origen.
    Usa un diccionario local basado en la imagen proporcionada.
    """
    try:
        # Diccionario de generadores por sector
        generadores_por_sector = {
            9: [  # Agentes de Atención - 9
                {"mGen_id": 60, "mGen_cod": "60", "mGen_nom": "CONTINUIDAD"},
                {"mGen_id": 63, "mGen_cod": "63", "mGen_nom": "DESUNIFICACION"},
                {"mGen_id": 65, "mGen_cod": "65", "mGen_nom": "DESCUENTO"},
                {"mGen_id": 71, "mGen_cod": "71", "mGen_nom": "DEVOLUCION DE PLAN"},
                {"mGen_id": 73, "mGen_cod": "73", "mGen_nom": "Crédito"},
                {"mGen_id": 74, "mGen_cod": "74", "mGen_nom": "Débito"},
                {"mGen_id": 75, "mGen_cod": "75", "mGen_nom": "Contado"},
            ],
            999: [  # Fidelización y continuidad - 55
                {"mGen_id": 60, "mGen_cod": "60", "mGen_nom": "CONTINUIDAD"},
                {"mGen_id": 63, "mGen_cod": "63", "mGen_nom": "DESUNIFICACION"},
                {"mGen_id": 65, "mGen_cod": "65", "mGen_nom": "DESCUENTO"},
                {"mGen_id": 71, "mGen_cod": "71", "mGen_nom": "DEVOLUCION DE PLAN"},
                {"mGen_id": 73, "mGen_cod": "73", "mGen_nom": "Crédito"},
                {"mGen_id": 74, "mGen_cod": "74", "mGen_nom": "Débito"},
                {"mGen_id": 75, "mGen_cod": "75", "mGen_nom": "Contado"},
            ],
        }

        sector_origen = int(sector_origen)
        generadores = generadores_por_sector.get(sector_origen, [])

        if not generadores:
            return JsonResponse({"error": "No hay generadores configurados para este sector"}, status=404)

        return JsonResponse(generadores, safe=False)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
    

@requiere_permiso_json("accion.asignar_generador")
def obtener_generadores_api(request, sector_origen):
    """
    Devuelve los posibles generadores según el sector de origen.
    - Si sector_origen = 9 → tipos 37 y 38
    - Si sector_origen = 55 → tipo 38
    """

    try:
        # Determinar URL según sector_origen
        if sector_origen == 9:
            api_url = "https://api.nobis.com.ar/generadores/37,38"
        elif sector_origen == 55:
            api_url = "https://api.nobis.com.ar/generadores/38"
        else:
            return JsonResponse({
                "error": f"No se definen generadores para el sector de origen {sector_origen}"
            }, status=400)

        # Llamada a la API
        api_response = requests.get(api_url, timeout=settings.REQUESTS_TIMEOUT)

        if api_response.status_code != 200:
            return JsonResponse({"error": "No se pudo obtener los generadores"}, status=404)

        # Parsear JSON
        data = api_response.json()
        if not data or not isinstance(data, list):
            return JsonResponse({"error": "No se encontraron generadores"}, status=404)

        # Formatear resultado
        generadores = [
            {
                "mGen_id": item.get("mGen_id"),
                "mGen_cod": item.get("mGen_cod"),
                "mGen_nom": item.get("mGen_nom"),
                "mTipoGen_id": item.get("mTipoGen_id"),
                "mTipoGen_nom": item.get("mTipoGen_nom")
            }
            for item in data
        ]

        if not generadores:
            return JsonResponse({"error": "No hay generadores disponibles"}, status=404)

        return JsonResponse(generadores, safe=False)

    except requests.exceptions.RequestException as e:
        return JsonResponse({"error": f"Error de conexión con la API: {e}"}, status=500)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@requiere_permiso_json("accion.enviar_bonificacion")
@csrf_exempt
def cargar_bonificacion_externa(request, grupo):
    """
    Carga una bonificación/descuento en el sistema externo (10.4.5.3:5000).
    El porcentaje proviene del input "Manual (Cotizar)" del cotizador de retención.
    'modo' es fijo = "forzar".
    """
    if request.method != "POST":
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8"))
        observacion = body.get("observacion", "alta externa")
        tramos = body.get("tramos")  # opcional: [{peri_desde, peri_hasta, porcentaje}, ...]

        # Identidad del operador para la API (que ya lee 'usuario' y 'grupo').
        # usuario = parte local del UPN (antes del @), sin espacios, máx 20.
        # grupo = nombre legible del grupo que concede la acción, sin espacios, máx 25.
        # Fallback "" en ambos; el endpoint lo resuelve por su cuenta.
        usuario_api = usuario_para_api(request, 20)
        grupo_api = "".join(grupo_que_concede(request, "accion.enviar_bonificacion").split())[:25]

        api_token = os.environ.get("BONIF_API_TOKEN", "")
        headers = {"Content-Type": "application/json", "X-API-Token": api_token}
        api_url = f"https://bonificaciones.nobis.com.ar/api/individuales/{grupo}/insertar"

        def _insertar(peri_desde, peri_hasta, porcentaje, rg_id=None, obs=None):
            # obs por-item (ej. bonificación de regla comercial con su prefijo);
            # si no viene, se usa la observación general del request.
            obs_final = obs if obs else observacion
            payload = {
                "peri_desde": peri_desde,
                "peri_hasta": peri_hasta,
                "porcentaje": porcentaje,
                "modo": "forzar",            # fijo, no visible al usuario
                "observacion": obs_final,
                "usuario": usuario_api,
            }
            # Insert de regla comercial: lleva rg_id (id de la regla) y NO 'grupo'.
            # Insert manual (porcentaje "Nuevo" / tramos): lleva 'grupo' + reeval=True y NO rg_id.
            if rg_id is not None:
                payload["rg_id"] = rg_id
            else:
                payload["grupo"] = grupo_api
                payload["reeval"] = True
            r = requests.post(api_url, json=payload, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
            ok = r.status_code in (200, 201)
            registrar(request, action="enviar_bonificacion", recurso="accion.enviar_bonificacion", target_type="grupo",
                      target_id=grupo, response_status=r.status_code, success=ok,
                      payload_summary={"peri_desde": peri_desde, "peri_hasta": peri_hasta,
                                       "porcentaje": porcentaje, "rg_id": rg_id},
                      error_detail="" if ok else r.text)
            return {
                "ok": ok,
                "peri_desde": peri_desde,
                "peri_hasta": peri_hasta,
                "porcentaje": porcentaje,
                "rg_id": rg_id,
                "detalle": (None if ok else r.text),
            }

        # --- Modo escalonado: varios tramos, "seguir y reportar" ---
        if tramos:
            resultados = [
                _insertar(t.get("peri_desde"), t.get("peri_hasta"), t.get("porcentaje"),
                          rg_id=t.get("rg_id"), obs=t.get("observacion"))
                for t in tramos
            ]
            fallidos = [x for x in resultados if not x["ok"]]
            return JsonResponse(
                {
                    "resultados": resultados,
                    "ok": len(fallidos) == 0,
                    "cargados": len(resultados) - len(fallidos),
                    "fallidos": len(fallidos),
                },
                status=(200 if not fallidos else 207),
            )

        # --- Modo único (retrocompatible) ---
        peri_desde = body.get("peri_desde")
        peri_hasta = body.get("peri_hasta")
        porcentaje = body.get("porcentaje")
        if not peri_desde or not peri_hasta:
            return JsonResponse({"error": "Debe indicar período desde y hasta"}, status=400)

        res = _insertar(peri_desde, peri_hasta, porcentaje)
        if not res["ok"]:
            return JsonResponse(
                {"error": "Error al cargar bonificación en sistema externo", "detalle": res["detalle"]},
                status=400)

        return JsonResponse({"mensaje": "Bonificación cargada correctamente"})

    except requests.exceptions.RequestException as e:
        return JsonResponse({"error": f"Error de conexión con la API: {e}"}, status=500)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@requiere_permiso_json("accion.evaluar_bonificacion")
@csrf_exempt
def evaluar_bonificacion_externa(request):
    """
    Evalúa (solo lectura) si el grupo familiar del titular tiene bonificaciones
    adicionales según sus atributos en Gecros. Proxy hacia
    https://bonificaciones.nobis.com.ar/api/v1/grupos/evaluar para mantener el
    BONIF_API_TOKEN fuera del front y evitar CORS. El cálculo del descuento a
    aplicar lo hace el front con la respuesta cruda.
    """
    if request.method != "POST":
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8"))
        dni = body.get("dni")
        periodo = str(body.get("periodo", "")).strip()
        if not dni or not periodo:
            return JsonResponse({"error": "Debe indicar dni y periodo"}, status=400)

        api_token = os.environ.get("BONIF_API_TOKEN", "")
        headers = {"Content-Type": "application/json", "X-API-Token": api_token}
        api_url = "https://bonificaciones.nobis.com.ar/api/v1/grupos/evaluar"
        payload = {"dni": int(dni), "periodo": periodo, "evaluar_gecros": True}

        r = requests.post(api_url, json=payload, headers=headers, timeout=settings.REQUESTS_TIMEOUT)
        ok = r.status_code in (200, 201)
        registrar(request, action="evaluar_bonificacion", recurso="accion.evaluar_bonificacion",
                  target_type="dni", target_id=str(dni), response_status=r.status_code, success=ok,
                  payload_summary={"periodo": periodo}, error_detail="" if ok else r.text)
        if not ok:
            return JsonResponse({"error": "Error al evaluar", "detalle": r.text}, status=400)
        return JsonResponse(r.json(), safe=False)  # respuesta cruda; el front calcula

    except requests.exceptions.RequestException as e:
        return JsonResponse({"error": f"Error de conexión con la API: {e}"}, status=500)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


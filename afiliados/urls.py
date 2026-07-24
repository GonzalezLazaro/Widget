from django.urls import path
from .views import BuscarAfiliadoView, BuscarRetencionView, MesaDeEntradaView
from .views import guardar_expediente, archivo_expediente, cotizar_actual, descargar_adjunto, previsualizar_adjunto, buscar_beneficiario, obtener_destinos, crear_remito
from .views import obtener_generadores_api, obtener_generadores
from .views import cargar_bonificacion_externa, evaluar_bonificacion_externa
app_name = 'afiliados'

urlpatterns = [
    path('atencion//', BuscarAfiliadoView.as_view(), name='buscar_afiliado'), # Sin DNI
    path('atencion/<str:dni>/', BuscarAfiliadoView.as_view(), name='buscar_afiliado'),

    path('retencion//', BuscarRetencionView.as_view(), name='buscar_retencion'), # Sin DNI
    path('retencion/<str:dni>/', BuscarRetencionView.as_view(), name='buscar_retencion'),
    path('api/cotizar/', cotizar_actual, name='cotizar_actual'),
    
    path('mesa//', MesaDeEntradaView.as_view(), name='mesa_entrada'), # Sin DNI
    path('mesa/<str:dni>/', MesaDeEntradaView.as_view(), name='mesa_entrada'),
    path('nuevo_expediente/guardar/', guardar_expediente, name='guardar_expediente'),
    path('nuevo_expediente/subir_archivo/', archivo_expediente, name='archivo_expediente'),
    path('expediente/descargar-adjunto/', descargar_adjunto, name='descargar_adjunto'),
    path('expediente/previsualizar-adjunto/', previsualizar_adjunto, name='previsualizar_adjunto'),
    path('buscar_beneficiario', buscar_beneficiario, name='buscar_beneficiario'),

    path('obtener_destinos/<int:sector_origen>/', obtener_destinos, name='obtener_destinos'),
    path('crear_remito/<int:expediente_id>/', crear_remito, name='crear_remito'),

    path('obtener_generadores/<int:sector_origen>/', obtener_generadores_api, name='obtener_generadores'),

    path('api/cargar_bonificacion/<int:grupo>/', cargar_bonificacion_externa, name='cargar_bonificacion_externa'),
    path('api/evaluar_bonificacion/', evaluar_bonificacion_externa, name='evaluar_bonificacion_externa'),

]
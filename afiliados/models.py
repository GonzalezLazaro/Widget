from django.db import models


class AuditLog(models.Model):
    """
    Registro local de quién hizo qué. Es el registro CONFIABLE de atribución,
    independiente de si el sistema externo (Gecros, etc.) acepta la identidad del usuario.
    """
    ACTIONS = [
        ("cotizar", "Cotizar"),
        ("crear_expediente", "Crear expediente"),
        ("subir_archivo", "Subir archivo"),
        ("crear_remito", "Crear remito"),
        ("asignar_generador", "Asignar generador"),
        ("enviar_bonificacion", "Enviar bonificación"),
        ("evaluar_bonificacion", "Evaluar bonificación"),
        ("admin_permisos_guardar", "Guardar permisos"),
        ("admin_grupo_crear", "Crear grupo"),
        ("admin_grupo_eliminar", "Eliminar grupo"),
        ("admin_acceso_total", "Cambiar acceso total"),
        ("login", "Inicio de sesión"),
        ("logout", "Cierre de sesión"),
        ("ver_vista", "Ver pantalla"),
        ("acceso_denegado", "Acceso denegado"),
    ]

    user_upn = models.CharField(max_length=255, blank=True, db_index=True)
    user_oid = models.CharField(max_length=64, blank=True, db_index=True)
    user_name = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=64, choices=ACTIONS, db_index=True)
    grupo = models.CharField(max_length=255, blank=True, db_index=True)   # nombre legible del grupo que concedió
    recurso = models.CharField(max_length=64, blank=True, db_index=True)  # recurso_key: vista.* / accion.*
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=128, blank=True)
    endpoint = models.CharField(max_length=255, blank=True)
    payload_summary = models.JSONField(default=dict, blank=True)
    response_status = models.IntegerField(null=True, blank=True)
    success = models.BooleanField(default=True)
    error_detail = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    dni = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Registro de auditoría"
        verbose_name_plural = "Registros de auditoría"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.user_upn} {self.action} {self.target_id}"

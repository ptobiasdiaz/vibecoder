# Seguridad administrativa

- Solo UID 0 puede mutar configuración, secretos o estado administrativo.
- `vibecoder` ejecuta workflows pero no administra la instalación.
- Tokens y contraseñas no se aceptan en argv ni se muestran en JSON, logs o errores.
- Los secretos usan archivos `0600` bajo un directorio `0700`; los metadatos no
  incluyen su contenido.
- Se rechazan traversal, identificadores no canónicos y enlaces simbólicos.
- JSON se actualiza con lock, validación y reemplazo atómico, conservando permisos.
- El token se entrega al cliente GitHub en memoria; no a Codex ni al workspace. Un
  proceso de servicio comprometido puede leer los secretos que necesita para operar.
- `revoke` elimina el acceso local; no revoca el token ante GitHub.
- No se usa `shell=True` ni se contacta con GitHub/Codex al configurar o diagnosticar.

Las copias de diagnóstico deben excluir secretos y sesiones. M05.4 definirá la
política completa de backup y recuperación.

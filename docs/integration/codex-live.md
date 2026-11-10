# Validación live del plugin Codex

Se instaló el artefacto oficial Linux x86_64 de Codex CLI `0.167.0`. El archivo
comprimido se verificó contra el manifiesto versionado y el binario publicado
tiene SHA-256
`cdcb45aaa01d0f14e298985e09465aebccd47f64ecd298aea44814af7b62bbda`.

Device Code se ejecutó bajo el UID de `codex-1` y su `CODEX_HOME` privado. Tras
autorización humana, el probe negoció `initialize` y dejó el worker READY. Ni
credenciales ni Device Code quedan en SQLite, journald o el progreso temporal.

Se ejecutaron dos turnos mínimos en la misma sesión opaca: sesión nueva con
respuesta terminal `OK`; después, un App Server nuevo confirmó `thread/resume`
y respondió `OK2`. Ambos quedaron `SUCCEEDED` y la concurrencia volvió a cero.

El esquema generado por Codex 0.167.0 contiene peticiones de aprobación de
comandos, cambios y permisos, además de `turn/interrupt` y `thread/read`. El
adaptador todavía no anuncia cancelación ni reconciliación: la existencia del
método no prueba su semántica. Una solicitud no mediada nunca se autoaprueba y
mantiene resultado conservador. El transporte humano neutral queda pendiente.

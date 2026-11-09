# Operación con systemd

La unidad se ejecuta como `vibecoder:vibecoder`, usa journald, entrega SIGTERM y
espera hasta 45 segundos. `Restart=on-failure` no se aplica a paradas limpias.

```bash
systemctl status vibecoder
systemctl restart vibecoder
journalctl -u vibecoder --no-pager
```

El hardening validado incluye `ProtectSystem=strict`, `ProtectHome=true`,
`PrivateTmp=true`, `NoNewPrivileges=true`, capabilities vacías y escritura
limitada a `/var/lib/vibecoder` y `/run/vibecoder`. Para backup o restore detenga
la unidad y confirme quiescencia mediante `--offline`.

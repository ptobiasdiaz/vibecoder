# Primera configuración

## Instalación interactiva

```bash
sudo apt install ./vibecoder_0.4.0_all.deb
```

Debconf pregunta opcionalmente un identificador administrativo no secreto y si debe
mostrar la ayuda de primer arranque. No solicita tokens ni activa el runtime. El
servicio permanece inerte en `NOT_CONFIGURED`.

Continúe con:

```bash
sudo vibecoder-admin setup init
```

El asistente detecta identidad, cuentas, credenciales y proyectos ya existentes. Cada
paso puede posponerse. La activación se propone al final con respuesta predeterminada
negativa.

## Instalación desatendida

```bash
sudo DEBIAN_FRONTEND=noninteractive apt install ./vibecoder_0.4.0_all.deb
sudo vibecoder-admin setup init --non-interactive --admin-id automation
```

Los parámetros opcionales permiten registrar una cuenta/proyecto. Un token solo se
acepta mediante `--fd N`, nunca como argumento. Sin `--activate`, el asistente no
alcanza `READY`.

## Reanudación

```bash
sudo vibecoder-admin setup init
sudo vibecoder-admin setup status
sudo vibecoder-admin doctor
```

La segunda ejecución reutiliza identidades y recursos coincidentes. No sustituye
valores existentes ni duplica administradores. Ante una interrupción, vuelva a
ejecutar el asistente.

## Reconfiguración Debian

```bash
sudo dpkg-reconfigure vibecoder
```

Revisa únicamente parámetros debconf no secretos. No cambia tokens, proyectos,
workspaces, workflows ni `READY`. Si ya existe una identidad inicial, otro
identificador no la reemplaza; use la CLI para diagnosticar la discrepancia.

## Activación explícita

```bash
sudo vibecoder-admin setup validate
sudo vibecoder-admin setup configure
sudo vibecoder-admin setup activate
```

Activar no crea autorización de workflows. Inferencia y publicación siguen sujetas a
las políticas y aprobaciones del proyecto.

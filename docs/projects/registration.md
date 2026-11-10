# Registro de proyectos

GitHub debe estar `ONLINE`, `origin` debe ser GitHub y la rama elegida debe
tener al menos un commit publicado.

```bash
cd ~/dev/breakout
vibecoder project add
vibecoder project add ~/dev/breakout
vibecoder project add ~/dev/breakout --branch develop
```

La rama por defecto se usa si coincide con la local. Cualquier otra requiere
`--branch`, debe ser la rama local actual y existir remotamente.

Se comprueban cambios staged, unstaged, untracked y el SHA local. Un checkout
limpio puede contener commits no publicados. Para ignorar cualquier dato local:

```bash
vibecoder project add --ignore-dirty
```

La opción no importa ni elimina cambios; el workspace aislado parte del SHA
publicado. La ruta local deja de ser una dependencia después del alta.

```bash
vibecoder project list [--long|--json]
vibecoder project owner/breakout status
vibecoder project breakout users
vibecoder project breakout authorize alice
vibecoder project breakout deauthorize alice
```

Se acepta nombre único, `owner/repository` o ID completo. Los logins se resuelven
con GitHub y se persisten IDs numéricos. La identidad global es el usuario
inicial y nunca puede eliminarse el último usuario. Registrar un proyecto no
crea agentes ni procesa Issues y, sin workers, mantiene `STANDBY/ONLINE`.


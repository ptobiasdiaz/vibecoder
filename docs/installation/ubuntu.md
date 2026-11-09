# Instalación en Ubuntu

Ubuntu 24.04.5 LTS (`noble`, amd64) tiene **compatibilidad experimental**, sin
soporte oficial ni compromiso de mantenimiento. Vibecoder 0.4.0 fue probado con Python 3.12.3,
Pydantic 1.10.14, HTTPX 0.26.0 y systemd 255. El paquete no usa `pip`, no altera
el Python del sistema y resuelve sus dependencias desde repositorios oficiales.

```bash
sudo apt install ./vibecoder_0.4.0_all.deb
sudo vibecoder-admin doctor
systemctl status vibecoder
```

Una instalación nueva queda `NOT_CONFIGURED` e inerte. El paquete histórico
0.3.0 exigía Pydantic 2.7, no disponible en noble; por tanto las instalaciones
experimentales en Ubuntu comienzan en 0.4.0. Ubuntu 26.04 podrá evaluarse en un
hito futuro; actualmente no está soportada.

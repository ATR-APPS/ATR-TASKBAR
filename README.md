# ATR-TASKBAR

Centra los iconos de aplicaciones abiertas en la barra de tareas de Windows 10, igual que hace Windows 11 por defecto.

Corre en segundo plano como un icono en la bandeja del sistema y se ajusta automáticamente cada vez que abres, cierras o minimizas una ventana.

### Musica en la barra de tareas

Junto al boton de Inicio muestra la portada (en cuadrado) y el nombre de lo que estes escuchando en Spotify o viendo en YouTube (Brave, Chrome, Edge, Firefox), usando los controles de medios de Windows.

- El widget vive dentro de la barra de tareas: sigue visible con Inicio abierto y se oculta junto con la barra en pantalla completa.
- Click sobre el widget: despliega justo encima una pastilla translucida (estilo macOS) con los controles: anterior, pausa / reanuda, siguiente. Se cierra con otro click en el widget, con un click fuera del panel o tras 5 segundos sin el mouse encima.
- En pausa se atenua y desaparece despues de 60 segundos.
- Se puede activar o desactivar desde el menu de la bandeja: **Mostrar musica**.

---

## Uso rapido (exe)

Descarga o compila `ATR-TASKBAR.exe` y ejecutalo. No requiere instalacion.

- Se agrega automaticamente al inicio de Windows.
- Para pausarlo o cerrarlo: click derecho en el icono de la bandeja del sistema.

---

## Compilar desde codigo fuente

**Requisitos:** Python 3.10+

```bat
build.bat
```

El ejecutable queda en `dist\ATR-TASKBAR.exe`.

### Pasos manuales (opcional)

```bat
pip install -r requirements.txt
pip install pyinstaller
python -m PyInstaller ATR-TASKBAR.spec
```

---

## Ejecutar sin compilar

```bat
pip install -r requirements.txt
python ATR-TASKBAR.py
```

---

## Diagnostico

`inspect_taskbar.py` imprime informacion sobre los botones de la barra de tareas detectados por UIAutomation. Util para depurar si el centrado no funciona correctamente.

```bat
python inspect_taskbar.py
```

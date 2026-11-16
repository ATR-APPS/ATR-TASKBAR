# ATR-TASKBAR

Centra los iconos de aplicaciones abiertas en la barra de tareas de Windows 10, igual que hace Windows 11 por defecto.

Corre en segundo plano como un icono en la bandeja del sistema y se ajusta automáticamente cada vez que abres, cierras o minimizas una ventana.

### Musica en la barra de tareas

Junto al boton de Inicio muestra la portada (en cuadrado) y el nombre de lo que estes escuchando en Spotify o viendo en YouTube (Brave, Chrome, Edge, Firefox), usando los controles de medios de Windows.

- El widget vive dentro de la barra de tareas: sigue visible con Inicio abierto y se oculta junto con la barra en pantalla completa.
- Click sobre el widget: pausa / reanuda.
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

---

## macOS (branch `macos`)

Version nativa en Swift para Mac, en la carpeta `macos/`. En macOS el Dock ya esta centrado, asi que aqui viven widgets junto al Dock. El de musica aparece **abajo a la izquierda**, alineado con el Dock, con la portada, el titulo y el artista de lo que suena en Spotify o en YouTube (Brave, Chrome, Edge, Arc, Vivaldi, Safari).

- Click sobre el widget: pausa / reanuda.
- En pausa se atenua y desaparece despues de 60 segundos.

**Abajo a la derecha: portapapeles y bandeja**

- **Portapapeles:** pila con lo ultimo que copiaste, como la pila de Descargas del Dock (el texto se ve como una hojita). Click: lista de las ultimas 10 copias; elegir una la vuelve a copiar. Solo en memoria (no se guarda en disco) e ignora lo que los gestores de contrasenas marcan como privado.
- **Bandeja:** pila con vista previa de los archivos; suelta archivos encima para guardarlos y arrastralos despues a donde quieras (al sacarlos se quitan de la bandeja). Click: lista de archivos para verlos en Finder; con ⌥ presionado, para quitarlos.

- Icono en la barra de menus: **Mostrar musica**, **Mostrar portapapeles y bandeja**, **Iniciar con macOS**, **Salir**.
- Visible en todos los Escritorios; se oculta en apps a pantalla completa.

### Compilar e instalar

**Requisitos:** macOS 13+ y Xcode Command Line Tools (`xcode-select --install`).

```bash
./macos/build.sh             # genera macos/build/ATR-TASKBAR.app
./macos/build.sh --install   # ademas la copia a /Applications y la abre
```

### Permisos

- La primera vez macOS pide permiso para que ATR-TASKBAR controle Spotify y tu navegador (Ajustes del Sistema > Privacidad y seguridad > Automatizacion).
- Para saber si un video de YouTube esta en pausa y poder pausarlo con un click, activa en el navegador **Ver > Opciones para desarrolladores > Permitir JavaScript desde eventos de Apple** (en Safari: Desarrollo > Permitir JavaScript desde eventos de Apple). Sin esto el video se muestra igual, pero siempre como reproduciendo.
- Firefox no es compatible (no expone sus pestañas a AppleScript).

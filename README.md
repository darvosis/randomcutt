# randomcutt

Extractor de clips aleatorios para VJ. Le das un video largo, le indicas cuántos
clips quieres y de qué duración, y genera N recortes desde puntos al azar,
exportados en **HAP** para que **TouchDesigner** los decodifique en GPU.

Nació para alimentar un cutter de visuales con material corto y desordenado.
Hace una sola cosa y la hace rápido.

![randomcutt](docs/screenshot.png)

---

## Qué hace

- Lee la duración real del video con `ffprobe` y elige N puntos de inicio.
- Corta con `ffmpeg` usando *input seek* (`-ss` antes de `-i`): salta al punto
  sin decodificar todo lo anterior, así que el tiempo no depende de cuán
  adentro del video caiga el corte.
- Exporta en HAP, ProRes, H.264 (CPU, NVENC o VideoToolbox en Mac) o copia el
  stream sin recomprimir.
- Los clips no heredan los metadatos del original (título, capítulos): los
  reproductores muestran el nombre del archivo.
- Ejecuta varios `ffmpeg` en paralelo.

## Requisitos

**`ffmpeg` y `ffprobe`.** Es lo único, y en Windows la app puede descargarlos
sola.

La app los busca al arrancar (PATH, `C:\Program Files\ffmpeg\bin`, el paquete
de WinGet, junto al `.exe`, o una copia que haya descargado antes). Si los
tienes en otra ubicación, indica la carpeta `bin` en el campo *Carpeta bin de
ffmpeg* de la sección AVANZADO.

**Si no los encuentra**, arriba a la derecha aparece `NO ENCONTRADO` en rojo y
el botón **Descargar ffmpeg**. Al apretar *EXTRAER CLIPS* también ofrece
descargarlo, y la extracción parte sola al terminar. La descarga:

- Baja el build **full** de [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
  (variante *shared*, ~95 MB) desde su release más reciente en GitHub. El build
  *essentials* no sirve: no trae `libsnappy` y sin eso no hay encoder HAP.
- Verifica el sha256 que publica GitHub y que el ffmpeg descargado arranque y
  traiga HAP antes de instalarlo.
- Lo deja en `%LOCALAPPDATA%\randomcutt\ffmpeg\bin`. No toca el PATH ni el
  resto del sistema; para desinstalarlo, borra esa carpeta.
- Si la API de GitHub falla, usa como respaldo el build *gpl-shared* de
  [BtbN](https://github.com/BtbN/FFmpeg-Builds) (también trae HAP).
- Se puede cancelar con *Cancelar*.

Si prefieres instalarlo tú, en Windows:

```powershell
winget install Gyan.FFmpeg
```

**En macOS** la app no descarga nada. Instálalo con [Homebrew](https://brew.sh),
pero **no** con `brew install ffmpeg`: ese se compila sin `libsnappy` y no trae
el encoder HAP. El del tap `homebrew-ffmpeg` sí:

```bash
brew install homebrew-ffmpeg/ffmpeg/ffmpeg
```

Si ya tenías el ffmpeg normal de Homebrew, desinstálalo antes
(`brew uninstall ffmpeg`), porque los dos chocan. El tap no trae una versión
precompilada: ffmpeg se compila en tu Mac (menos de dos minutos en un M2). La
app lo busca en `/opt/homebrew/bin` y `/usr/local/bin` aunque la abras desde el
Finder, y si eliges HAP con un ffmpeg que no lo trae, avisa antes de extraer.

En Linux: `sudo apt install ffmpeg`.

Para ejecutarlo desde el código en vez del `.exe`: Python 3.9+ con `tkinter`.
No hay dependencias externas. En macOS usa el Python de
[python.org](https://www.python.org/downloads/macos/), que trae Tk 8.6; el
`/usr/bin/python3` del sistema trae Tk 8.5.

```bash
python randomcutt_v007.py
```

## Uso

1. **ORIGEN** — elige un video (`Archivo`) o una carpeta entera (`Carpeta`), y
   cómo nombrar los clips.
2. **CORTES** — cuántos clips y de cuántos segundos (acepta decimales: `0.5`).
3. **SALIDA** — carpeta destino, cómo organizarla y códec.
4. **EXTRAER CLIPS**.

Los archivos salen como `<nombre>_clip_<n>.mov`. Nunca sobrescribe nada: si el
nombre existe, agrega `_1`, `_2`, etc.

### Nombres de los clips

Los nombres de release traen de todo menos el título. randomcutt lo limpia
antes de nombrar los clips y la subcarpeta:

| Video de origen | Clips |
|-----------------|-------|
| `Night.Of.The.Living.Dead.1968.1080p.BluRay.x264-[YTS.AM].mp4` | `Night Of The Living Dead_clip_1.mov` |
| `A Kite (1998) (BDRip 1440x1076p x265 HEVC FLACx2)(Dual Audio)[sxales].mkv` | `A Kite_clip_1.mov` |
| `Kite 1998 UNCUT Part 2 DVDRip Dual Audio (ENG+JAP) LKRG.mp4` | `Kite Part 2_clip_1.mov` |
| `[HorribleSubs] Cowboy Bebop - 05 [1080p].mkv` | `Cowboy Bebop 05_clip_1.mov` |
| `The.Office.US.S02E03.The.Dundies.720p.WEB-DL.mkv` | `The Office US S02E03_clip_1.mov` |
| `L.A.Confidential.1997.REMASTERED.1080p.BluRay.mkv` | `L.A. Confidential_clip_1.mov` |

No usa ninguna lista de películas, sino la convención con que se nombran los
releases (scene, P2P, fansubs): el título va primero y, desde el año, la primera
etiqueta técnica (`1080p`, `BluRay`, `x264`, `AAC5.1`…) o el marcador de
episodio, lo que sigue es metadata. Por eso sirve con cualquier nombre, no solo
con una biblioteca en particular. Algunas reglas:

- Quita corchetes (`[YTS.MX]`, `[Grupo]`, `[ABCD1234]`), sitios `www.…` y el
  grupo de release.
- Conserva lo que distingue un archivo de otro: `S02E03`, `1x05`, `- 05` de
  fansub, `Part 2`, `Vol 1`, `CD 2`.
- No confunde números del título con años: `Blade Runner 2049`, `1917`,
  `2001 A Space Odyssey`, `Wonder Woman 1984`.
- Ediciones y tags (`Remastered`, `Director's Cut`, `Dual Audio`, `Uncut`…) solo
  se quitan al final del título: `Uncut Gems` se salva.
- Acepta cualquier alfabeto (`千と千尋の神隠し`, `Häxan`) y unifica los acentos
  descompuestos que dejan los discos de macOS.
- El resultado siempre es un nombre de archivo válido: sin `: ? * " |`, sin
  punto final, sin nombres reservados de Windows (`CON`, `NUL`…) y con un tope
  de 80 caracteres.

**Estilo:** `Limpio` (`Night Of The Living Dead`), `Limpio sin espacios`
(`NightOfTheLivingDead`), `snake_case` (`night_of_the_living_dead`) u
`Original` (el nombre del archivo tal cual, como en v0.8). **Conservar año**
agrega el año: `Night Of The Living Dead (1968)`.

**Corrección manual.** En modo `Archivo` el nombre aparece en un campo
editable. Si lo cambias, queda guardado para ese video (por nombre de archivo)
y se vuelve a usar cada vez que lo proceses, también en modo `Carpeta`. `Auto`
vuelve al nombre automático y borra el guardado. El estilo también se aplica al
nombre corregido: `Angel Cop` sale como `angel_cop` en `snake_case` y como
`AngelCop` en `Limpio sin espacios`. Sirve para los casos que
ninguna regla resuelve, como un título que termina en un número con forma de
año (`Class of 1999` sin año de estreno se leería como `Class of`).

En modo `Carpeta`, **Ver nombres en la consola** muestra `origen -> nombre` de
todo el lote antes de extraer. Si dos videos terminan con el mismo nombre, el
segundo recibe `(2)`: si no, compartirían subcarpeta y *Omitir videos ya
procesados* daría el segundo por hecho.

### Modo carpeta (lote)

Eliges una carpeta con películas y saca N clips de **cada una**. Con
*Organizar salida* en `Subcarpeta por video`, cada película termina en su propia
carpeta:

```
originales\                         salida\
  NinjaScroll.mkv          →          NinjaScroll\
  Suspiria.mp4                          NinjaScroll_clip_1_HAP.mov
  WickedCity.mkv                        NinjaScroll_clip_2_HAP.mov
                                      Suspiria\
                                        Suspiria_clip_1_HAP.mov
                                        ...
```

Antes de empezar muestra cuántos videos encontró, cuánto pesan y cuántos clips
va a generar.

| Opción | Qué hace |
|--------|----------|
| **Incluir subcarpetas** | Busca videos también dentro de las subcarpetas. Desactivado por defecto. |
| **Ignorar archivos `*_clip_*`** | No usa como fuente lo que ya parece un clip generado. Importa si la carpeta mezcla fuentes y clips. |
| **Omitir videos ya procesados** | Si la carpeta de salida de un video ya tiene clips suyos, lo omite. Puedes detener un lote a la mitad, volver a lanzarlo y continúa donde quedó. Reconoce los clips aunque se hayan hecho con otro estilo de nombre o con una versión anterior. |

También descarta sidecars de macOS (`._nombre.mkv`) y archivos de menos de
64 KB. Si un video está roto o no se puede leer, lo reporta y sigue con el
resto — un archivo malo no frena el lote.

Con **Seed** el lote entero es reproducible, pero cada película recibe su propia
tirada (el seed se combina con el nombre del archivo).

| Organizar salida | Resultado |
|------------------|-----------|
| **Subcarpeta por video** | `<salida>\<nombre del video>\` — opción por defecto. |
| **Todo en una carpeta** | Todos los clips sueltos en `<salida>\`. |
| **Junto al video de origen** | Al lado de cada fuente. Ignora la carpeta de salida. |

Medido sobre 15 películas reales (23.4 GB: h264, hevc, av1 y mpeg4 en
mp4/mkv/avi), 3 clips de 3s por película a HAP: **45 clips en 8.7 s**.

### Distribución de los cortes

| Modo | Qué hace |
|------|----------|
| **Random puro** | Puntos de inicio totalmente al azar. Puede repetir o solapar zonas. |
| **Estratificado** | Divide el video en N tramos iguales y saca un clip de cada uno, con jitter regulable. Cobertura pareja de todo el video, sin repetir. |
| **Lineal** | Intervalos exactos y regulares. Cero azar. |

El campo **Seed** hace la tirada reproducible: mismo seed + mismo video +
mismos parámetros = exactamente los mismos cortes.

### Códecs

| Códec | Contenedor | Para qué |
|-------|-----------|----------|
| `hap` | `.mov` | **Por defecto.** Decodifica en GPU. Lo ideal para `moviefilein` en TouchDesigner. |
| `prores_ks` | `.mov` | ProRes HQ. Decodifica en CPU. |
| `libx264` | `.mp4` | CRF 18, yuv420p. Liviano en disco. |
| `h264_nvenc` | `.mp4` | H.264 por GPU (NVIDIA). El más rápido de exportar. |
| `h264_videotoolbox` | `.mp4` | Solo en macOS, en lugar de `h264_nvenc`: H.264 por el hardware de Apple. |
| `copy` | igual que el origen | Sin recomprimir. Solo corta en keyframes, la duración real puede variar. |

**Variantes HAP:** `hap` (DXT1, el más liviano), `hap_q` (mejor calidad, ~70%
más pesado), `hap_alpha` (con canal alpha).

**Chunks:** parte cada frame en N bloques para que el decoder los descomprima en
paralelo. 4 funciona bien. Súbelo si reproduces varios streams a la vez.

> **HAP exige ancho y alto múltiplos de 4.** Si tu fuente no lo es `ffmpeg`
> aborta. Pasa más de lo que parece: material de DVD (654×480, 638×480) y también
> rips de 1080 con las barras recortadas (1920×1038). En una biblioteca real de
> 15 películas, 3 caían en esto. randomcutt lo detecta y recorta al múltiplo más
> cercano: pierdes 1–3 px del borde derecho e inferior. Puedes cambiarlo a `pad`
> (barras negras) en AVANZADO.

### Procesos en paralelo

Cuántos `ffmpeg` corren a la vez. Medido en un Ryzen 9 7900X, 24 clips de 5s a
HAP desde una fuente H.264 654×480:

| Procesos | Tiempo |
|----------|--------|
| 1 | 4.3 s |
| 3 | 2.3 s |
| 6 | 1.7 s |
| 12 | 1.5 s |

Rinde hasta ~6; por encima, el cuello de botella pasa a ser el disco. El valor
por defecto es 3.

## Compilar el ejecutable

```powershell
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

Crea un venv aislado en `.venv-build`, instala PyInstaller ahí y deja
`dist\randomcutt.exe` (~12 MB). No toca tu Python base. `-Clean` borra todo y
empieza de cero.

`ffmpeg` **no** se empaqueta (son 200 MB); la app lo busca al arrancar y, si no
está, ofrece descargarlo. Si quieres una versión totalmente portable, copia
`ffmpeg.exe` y `ffprobe.exe` al lado del `.exe` y los encontrará.

### macOS

```bash
./build_app.sh              # app para este Mac (arm64 en M1/M2)
./build_app.sh --universal  # una sola app para Apple Silicon e Intel
```

Necesita el Python de python.org. Igual que en Windows, usa un venv aislado en
`.venv-build` y `--clean` borra todo y empieza de cero. Deja
`dist/randomcutt.app` (28 MB; 49 MB la universal) y un `.zip` para el release,
`dist/randomcutt-macos-arm64.zip` o `-universal.zip`, hecho con `ditto` porque
`zip` rompe los permisos y los symlinks del bundle. ffmpeg tampoco se empaqueta.

La app no va firmada. Si la bajas de GitHub, macOS la bloquea la primera vez.
Desde macOS 15 ya no sirve clic derecho → Abrir: intenta abrirla una vez, ve a
*Ajustes del Sistema → Privacidad y seguridad* (*System Settings → Privacy &
Security*) y autorízala con el botón *Open Anyway* que aparece al final. O
quítale la cuarentena:

```bash
xattr -dr com.apple.quarantine randomcutt.app
```

> Si compilas con un Python de Anaconda/Miniconda, el script agrega
> `Library\bin` al PATH antes de invocar PyInstaller. Sin eso `tcl86t.dll` y
> `tk86t.dll` no se empaquetan y el `.exe` muere con
> `ImportError: DLL load failed while importing _tkinter`.

## Configuración

Los parámetros se guardan al cerrar y al lanzar cada extracción en
`%LOCALAPPDATA%\randomcutt\config.json` (en macOS,
`~/Library/Application Support/randomcutt/config.json`), y se restauran al
abrir. Ahí también
quedan los nombres corregidos a mano (`name_overrides`: nombre del archivo de
origen → nombre de los clips); se pueden editar o borrar a mano con la app
cerrada.

## Licencia

MIT — ver [LICENSE](LICENSE).

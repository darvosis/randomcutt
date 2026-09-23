# Manual de randomcutt

El detalle completo. Para empezar, el [README](../README.md) alcanza.

- [ffmpeg en detalle](#ffmpeg-en-detalle)
- [Nombres de los clips](#nombres-de-los-clips)
- [Modo carpeta (lote)](#modo-carpeta-lote)
- [Variantes HAP y chunks](#variantes-hap-y-chunks)
- [Rendimiento](#rendimiento)
- [Compilar el binario](#compilar-el-binario)

## ffmpeg en detalle

La app busca `ffmpeg` y `ffprobe` al arrancar: en el PATH,
`C:\Program Files\ffmpeg\bin`, el paquete de WinGet, junto al `.exe`, o una
copia que haya descargado antes. Si los tienes en otra ubicación, indica la
carpeta `bin` en el campo *Carpeta bin de ffmpeg* de la sección AVANZADO.

**Si no los encuentra**, arriba a la derecha aparece `NO ENCONTRADO` en rojo y
el botón **Descargar ffmpeg**. Al apretar *EXTRAER CLIPS* también ofrece
descargarlo, y la extracción parte sola al terminar. La descarga:

- Baja el build **full** de [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
  (variante *shared*, ~95 MB). El build *essentials* no sirve: no trae
  `libsnappy` y sin eso no hay encoder HAP.
- Verifica el sha256 que publica GitHub, y que el ffmpeg descargado arranque y
  traiga HAP, antes de instalarlo.
- Lo deja en `%LOCALAPPDATA%\randomcutt\ffmpeg\bin`. No toca el PATH ni el
  resto del sistema; para desinstalarlo, borra esa carpeta.
- Si la API de GitHub falla, usa de respaldo el build *gpl-shared* de
  [BtbN](https://github.com/BtbN/FFmpeg-Builds), que también trae HAP.
- Se puede cancelar con *Cancelar*.

**En macOS** la app no descarga nada. El tap `homebrew-ffmpeg` es el que trae
HAP; si ya tenías el ffmpeg normal de Homebrew, desinstálalo antes
(`brew uninstall ffmpeg`), porque los dos chocan. El tap no viene precompilado:
ffmpeg se compila en tu Mac (menos de dos minutos en un M2). La app lo busca en
`/opt/homebrew/bin` y `/usr/local/bin` aunque la abras desde el Finder, y si
eliges HAP con un ffmpeg que no lo trae, avisa antes de extraer.

### Correr desde el código

Python 3.9+ con `tkinter`, sin dependencias externas. En macOS usa el Python de
[python.org](https://www.python.org/downloads/macos/), que trae Tk 8.6; el
`/usr/bin/python3` del sistema trae Tk 8.5.

```bash
python randomcutt_v007.py
```

## Nombres de los clips

Los nombres de archivo que vienen de internet traen de todo menos el título.
randomcutt lo limpia antes de nombrar los clips y la subcarpeta:

| Video de origen | Clips |
|-----------------|-------|
| `Neon.Harvest.1991.1080p.BluRay.x264-GRUPO.mp4` | `Neon Harvest_clip_1.mov` |
| `Glass Tide (1998) (BDRip 1440x1076p x265 HEVC FLACx2)(Dual Audio)[abcd1234].mkv` | `Glass Tide_clip_1.mov` |
| `[Fansub] Static Garden - 05 [1080p].mkv` | `Static Garden 05_clip_1.mov` |
| `Vector.Lane.S02E03.El.Pozo.720p.WEB-DL.mkv` | `Vector Lane S02E03_clip_1.mov` |
| `K.L.Standard.1997.REMASTERED.1080p.BluRay.mkv` | `K.L. Standard_clip_1.mov` |

No usa ninguna lista de títulos, sino la convención con que se nombran esos
archivos: el título va primero y, desde el año, la primera etiqueta técnica
(`1080p`, `BluRay`, `x264`, `AAC5.1`…) o el marcador de episodio, lo que sigue
es metadata. Por eso sirve con cualquier nombre. Algunas reglas:

- Quita corchetes (`[abcd1234]`, `[Grupo]`), sitios `www.…` y el grupo final.
- Conserva lo que distingue un archivo de otro: `S02E03`, `1x05`, `- 05` de
  fansub, `Part 2`, `Vol 1`, `CD 2`.
- No confunde números del título con años: `Sector 2049`, `Turno 1999`.
- Ediciones y tags (`Remastered`, `Director's Cut`, `Dual Audio`, `Uncut`…) solo
  se quitan al final, así que un título que *empieza* con una de esas palabras
  no se rompe.
- Acepta cualquier alfabeto y unifica los acentos descompuestos que dejan los
  discos de macOS.
- El resultado siempre es un nombre válido: sin `: ? * " |`, sin punto final,
  sin nombres reservados de Windows (`CON`, `NUL`…) y con tope de 80 caracteres.

**Estilo:** `Limpio` (`Neon Harvest`), `Limpio sin espacios` (`NeonHarvest`),
`snake_case` (`neon_harvest`) u `Original` (el nombre tal cual, como en v0.8).
**Conservar año** agrega el año: `Neon Harvest (1991)`.

**Corrección manual.** En modo `Archivo` el nombre aparece en un campo
editable. Si lo cambias, queda guardado para ese video (por nombre de archivo) y
se reusa cada vez que lo proceses, también en modo `Carpeta`; `Auto` vuelve al
automático y borra el guardado. El estilo se aplica igual al nombre corregido
(`Neon Harvest` → `neon_harvest`). Sirve para lo que ninguna regla resuelve,
como un título que termina en un número con forma de año: `Turno 1999`, sin año
de estreno, se leería como `Turno`.

Los nombres corregidos quedan en el `config.json` bajo `name_overrides` (archivo
de origen → nombre de los clips); se pueden editar o borrar a mano con la app
cerrada.

En modo `Carpeta`, **Ver nombres en la consola** muestra `origen -> nombre` de
todo el lote antes de extraer. Si dos videos terminan con el mismo nombre, el
segundo recibe `(2)`: si no, compartirían subcarpeta y *Omitir videos ya
procesados* daría el segundo por hecho.

## Modo carpeta (lote)

Eliges una carpeta y saca N clips de **cada** video. Con *Organizar salida* en
`Subcarpeta por video`, cada uno termina en su propia carpeta:

```
originales\                         salida\
  NeonHarvest.mkv          →          NeonHarvest\
  GlassTide.mp4                         NeonHarvest_clip_1_HAP.mov
  StaticGarden.mkv                      NeonHarvest_clip_2_HAP.mov
                                      GlassTide\
                                        GlassTide_clip_1_HAP.mov
                                        ...
```

Antes de empezar muestra cuántos videos encontró, cuánto pesan y cuántos clips
va a generar.

| Opción | Qué hace |
|--------|----------|
| **Incluir subcarpetas** | Busca videos también dentro de las subcarpetas. Desactivado por defecto. |
| **Ignorar archivos `*_clip_*`** | No usa como fuente lo que ya parece un clip generado. Importa si la carpeta mezcla fuentes y clips. |
| **Omitir videos ya procesados** | Si la carpeta de salida de un video ya tiene clips suyos, lo omite. Puedes detener un lote a la mitad, volver a lanzarlo y continúa donde quedó. Los reconoce aunque se hayan hecho con otro estilo de nombre o con una versión anterior. |

| Organizar salida | Resultado |
|------------------|-----------|
| **Subcarpeta por video** | `<salida>\<nombre del video>\` — opción por defecto. |
| **Todo en una carpeta** | Todos los clips sueltos en `<salida>\`. |
| **Junto al video de origen** | Al lado de cada fuente. Ignora la carpeta de salida. |

También descarta sidecars de macOS (`._nombre.mkv`) y archivos de menos de
64 KB. Si un video está roto o no se puede leer, lo reporta y sigue con el
resto: un archivo malo no frena el lote.

Con **Seed** el lote entero es reproducible, pero cada video recibe su propia
tirada (el seed se combina con el nombre del archivo).

## Variantes HAP y chunks

- `hap` — DXT1, el más liviano. Es el que usa por defecto.
- `hap_q` — mejor calidad, ~70 % más pesado.
- `hap_alpha` — con canal alpha.

**Chunks** parte cada frame en N bloques para que el decoder los descomprima en
paralelo. 4 funciona bien; súbelo si reproduces varios streams a la vez.

Sobre el requisito de **múltiplos de 4**: pasa más seguido de lo que parece
(material de DVD como 654×480 o 638×480, y rips de 1080 con las barras
recortadas, 1920×1038 — en una biblioteca de 15 videos, 3 caían en esto).
randomcutt recorta al múltiplo más cercano, o agrega barras negras si eliges
`pad` en AVANZADO.

## Rendimiento

Corta con *input seek* (`-ss` antes de `-i`): ffmpeg salta directo al punto sin
decodificar lo anterior, así que un clip del minuto 90 cuesta lo mismo que uno
del minuto 2. Sobre eso, *Procesos en paralelo* define cuántos `ffmpeg` corren a
la vez. Medido en un Ryzen 9 7900X, 24 clips de 5s a HAP desde una fuente H.264
654×480:

| Procesos | Tiempo |
|----------|--------|
| 1 | 4.3 s |
| 3 | 2.3 s |
| 6 | 1.7 s |
| 12 | 1.5 s |

Rinde hasta ~6; por encima, el cuello de botella pasa a ser el disco. El valor
por defecto es 3.

En lote, sobre 15 videos (23.4 GB: h264, hevc, av1 y mpeg4 en mp4/mkv/avi), 3
clips de 3s de cada uno a HAP: **45 clips en 8.7 s**.

## Compilar el binario

No hace falta para usar randomcutt: el `.exe` y el `.app` de
[Releases](https://github.com/darvosis/randomcutt/releases/latest) se abren sin
instalar nada. Esto es solo para generarlos desde el código.

```powershell
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

Crea un venv aislado en `.venv-build`, instala PyInstaller ahí y deja
`dist\randomcutt.exe` (~12 MB). No toca tu Python base. `-Clean` empieza de
cero.

`ffmpeg` **no** se empaqueta (son 200 MB); la app lo busca al arrancar y, si no
está, ofrece descargarlo. Para una versión totalmente portable, copia
`ffmpeg.exe` y `ffprobe.exe` al lado del `.exe` y los encontrará.

> Si compilas con un Python de Anaconda/Miniconda, el script agrega
> `Library\bin` al PATH antes de invocar PyInstaller. Sin eso `tcl86t.dll` y
> `tk86t.dll` no se empaquetan y el `.exe` muere con
> `ImportError: DLL load failed while importing _tkinter`.

### macOS

```bash
./build_app.sh              # app para este Mac (arm64 en M1/M2)
./build_app.sh --universal  # una sola app para Apple Silicon e Intel
```

Necesita el Python de python.org. Igual que en Windows, usa un venv aislado en
`.venv-build` y `--clean` empieza de cero. Deja `dist/randomcutt.app` (28 MB;
49 MB la universal) y un `.zip` para el release, hecho con `ditto` porque `zip`
rompe los permisos y los symlinks del bundle. ffmpeg tampoco se empaqueta.

La app no va firmada, así que macOS la bloquea la primera vez. Desde macOS 15 ya
no sirve clic derecho → Abrir: intenta abrirla una vez, ve a *Ajustes del
Sistema → Privacidad y seguridad* y autorízala con el botón *Open Anyway* del
final. O quítale la cuarentena:

```bash
xattr -dr com.apple.quarantine randomcutt.app
```

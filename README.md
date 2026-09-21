# randomcutt

Extractor de clips random para VJ. Le das un video largo, le decís cuántos clips
querés y de qué duración, y te escupe N recortes desde puntos al azar —
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
- Exporta en HAP, ProRes, H.264 (CPU o NVENC) o copia el stream sin recomprimir.
- Corre varios `ffmpeg` en paralelo.

## Requisitos

**`ffmpeg` y `ffprobe` en el PATH.** Es lo único. En Windows:

```powershell
winget install Gyan.FFmpeg
```

La app los busca sola (PATH, `C:\Program Files\ffmpeg\bin`, el paquete de
WinGet, o junto al `.exe`). Si los tenés en otro lado, poné la carpeta `bin` en
el campo *Carpeta bin de ffmpeg* de la sección AVANZADO.

Para correrlo desde el código en vez del `.exe`: Python 3.9+ con `tkinter`.
No hay dependencias externas.

```bash
python randomcutt_v007.py
```

## Uso

1. **ORIGEN** — elegí el video. Debajo aparece códec, resolución, duración y fps.
2. **CORTES** — cuántos clips y de cuántos segundos (acepta decimales: `0.5`).
3. **SALIDA** — carpeta destino y códec.
4. **EXTRAER CLIPS**.

Los archivos salen como `<nombre>_clip_<n>.mov`. Nunca pisa nada: si el nombre
existe agrega `_1`, `_2`, etc.

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
| `hap` | `.mov` | **Default.** Decodifica en GPU. Lo que querés para `moviefilein` en TouchDesigner. |
| `prores_ks` | `.mov` | ProRes HQ. Decodifica en CPU. |
| `libx264` | `.mp4` | CRF 18, yuv420p. Liviano en disco. |
| `h264_nvenc` | `.mp4` | H.264 por GPU (NVIDIA). El más rápido de exportar. |
| `copy` | igual que el origen | Sin recomprimir. Sólo corta en keyframes, la duración real puede variar. |

**Variantes HAP:** `hap` (DXT1, el más liviano), `hap_q` (mejor calidad, ~70%
más pesado), `hap_alpha` (con canal alpha).

**Chunks:** parte cada frame en N bloques para que el decoder los descomprima en
paralelo. 4 anda bien. Subilo si reproducís varios streams a la vez.

> **HAP exige ancho y alto múltiplos de 4.** Si tu fuente no lo es (pasa seguido
> con material de DVD: 654×480, 716×480…) `ffmpeg` aborta. randomcutt lo detecta
> y recorta al múltiplo más cercano — perdés 1–3 px de borde. Podés cambiarlo a
> `pad` (barras negras) en AVANZADO.

### Procesos en paralelo

Cuántos `ffmpeg` corren a la vez. Medido en un Ryzen 9 7900X, 24 clips de 5s a
HAP desde una fuente H.264 654×480:

| Procesos | Tiempo |
|----------|--------|
| 1 | 4.3 s |
| 3 | 2.3 s |
| 6 | 1.7 s |
| 12 | 1.5 s |

Rinde hasta ~6; más arriba el cuello de botella pasa a ser el disco. El default
es 3.

## Compilar el ejecutable

```powershell
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

Crea un venv aislado en `.venv-build`, instala PyInstaller ahí y deja
`dist\randomcutt.exe` (~12 MB). No toca tu Python base. `-Clean` borra todo y
empieza de cero.

`ffmpeg` **no** se empaqueta (son 200 MB); la app lo busca al arrancar. Si
querés una versión totalmente portable, copiá `ffmpeg.exe` y `ffprobe.exe` al
lado del `.exe` y los va a encontrar.

> Si compilás con un Python de Anaconda/Miniconda, el script agrega
> `Library\bin` al PATH antes de invocar PyInstaller. Sin eso `tcl86t.dll` y
> `tk86t.dll` no se empaquetan y el `.exe` muere con
> `ImportError: DLL load failed while importing _tkinter`.

## Configuración

Los parámetros se guardan al cerrar en
`%LOCALAPPDATA%\randomcutt\config.json` y se restauran al abrir.

## Licencia

MIT — ver [LICENSE](LICENSE).

# randomcutt

Extractor de clips aleatorios para VJ. Le das un video largo, le indicas cuántos
clips quieres y de qué duración, y genera N recortes desde puntos al azar,
exportados en **HAP** para que **TouchDesigner** los decodifique en GPU.

Nació para alimentar un cutter de visuales con material corto y desordenado.
Hace una sola cosa y la hace rápido.

![randomcutt](docs/screenshot.png)

**[Descargar la última versión](https://github.com/darvosis/randomcutt/releases/latest)**
· **[Manual completo](docs/MANUAL.md)**

---

## Qué hace

- Corta con `ffmpeg` por *input seek*: el tiempo no depende de cuán adentro del
  video caiga el corte, y varios cortes salen en paralelo.
- Exporta en HAP, ProRes, H.264 (CPU, NVENC o VideoToolbox en Mac) o copia el
  stream sin recomprimir.
- Limpia el nombre del archivo de origen para nombrar los clips:
  `Neon.Harvest.1991.1080p.BluRay.x264-GRUPO.mp4` sale como
  `Neon Harvest_clip_1.mov` ([reglas](docs/MANUAL.md#nombres-de-los-clips)).
- **Modo carpeta:** N clips de *cada* video de una carpeta, reanudable
  ([detalle](docs/MANUAL.md#modo-carpeta-lote)).
- Los clips no heredan los metadatos del original: los reproductores muestran el
  nombre del archivo.

## Requisitos

**`ffmpeg` y `ffprobe` con encoder HAP.** Es lo único.

- **Windows** — la app los descarga sola si no los encuentra (botón **Descargar
  ffmpeg**), o instálalos con `winget install Gyan.FFmpeg`.
- **macOS** — con [Homebrew](https://brew.sh), pero **no** con
  `brew install ffmpeg`: ese viene sin el encoder HAP. El del tap sí:

  ```bash
  brew install homebrew-ffmpeg/ffmpeg/ffmpeg
  ```

- **Linux** — `sudo apt install ffmpeg`.

Dónde los busca la app, qué baja exactamente la descarga automática y cómo
correrlo desde el código: [manual](docs/MANUAL.md#ffmpeg-en-detalle).

## Uso

1. **ORIGEN** — un video (`Archivo`) o una carpeta entera (`Carpeta`), y cómo
   nombrar los clips.
2. **CORTES** — cuántos clips y de cuántos segundos (acepta decimales: `0.5`).
3. **SALIDA** — carpeta destino, cómo organizarla y códec.
4. **EXTRAER CLIPS**.

Salen como `<nombre>_clip_<n>.mov`. Nunca sobrescribe nada: si el nombre existe,
agrega `_1`, `_2`, etc.

### Distribución de los cortes

| Modo | Qué hace |
|------|----------|
| **Random puro** | Puntos de inicio al azar. Puede repetir o solapar zonas. |
| **Estratificado** | N tramos iguales, un clip de cada uno, con jitter regulable. Cobertura pareja, sin repetir. |
| **Lineal** | Intervalos exactos y regulares. Cero azar. |

**Seed** hace la tirada reproducible: mismo seed + mismo video + mismos
parámetros = exactamente los mismos cortes.

### Códecs

| Códec | Contenedor | Para qué |
|-------|-----------|----------|
| `hap` | `.mov` | **Por defecto.** Decodifica en GPU: lo ideal para `moviefilein` en TouchDesigner. |
| `prores_ks` | `.mov` | ProRes HQ. Decodifica en CPU. |
| `libx264` | `.mp4` | CRF 18, yuv420p. Liviano en disco. |
| `h264_nvenc` | `.mp4` | H.264 por GPU (NVIDIA). El más rápido de exportar. |
| `h264_videotoolbox` | `.mp4` | El equivalente en macOS, por hardware de Apple. |
| `copy` | igual que el origen | Sin recomprimir. Corta en keyframes: la duración real puede variar. |

> **HAP exige ancho y alto múltiplos de 4**, o `ffmpeg` aborta. Pasa más de lo
> que parece: material de DVD, o rips de 1080 con las barras recortadas.
> randomcutt lo detecta y recorta al múltiplo más cercano; se puede cambiar a
> `pad` (barras negras) en AVANZADO.

Las variantes `hap_q` / `hap_alpha`, los *chunks* y los tiempos medidos están en
el [manual](docs/MANUAL.md#variantes-hap-y-chunks).

## Configuración

Se guarda sola en `%LOCALAPPDATA%\randomcutt\config.json` (en macOS,
`~/Library/Application Support/randomcutt/`) y se restaura al abrir.

## Licencia

MIT — ver [LICENSE](LICENSE).

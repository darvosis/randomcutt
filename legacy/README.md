# legacy

Versiones anteriores de randomcutt, guardadas como historia. **No las uses.**

| Archivo | Qué era |
|---------|---------|
| `randomcutt.py`, `randomcutt2.py` | Primeros scripts, sin GUI. Rutas hardcodeadas, cortaban con moviepy. |
| `randomcutt_GUI_v01.py` → `v04.py` | GUI mínima en tkinter. v04 todavía decodificaba el video entero con `moviepy` para sacar cada clip. |
| `randomcutt_GUI_v05.py` | Primer salto a `ffmpeg` directo por subprocess. Tenía el slider de aleatoriedad (lineal ↔ random). |
| `randomcutt_GUI_v06.py` | Agregó hilo + barra de progreso, pero perdió el slider de v05 y quedó con la ruta de ffmpeg hardcodeada en `C:\Program Files\ffmpeg\bin`. |
| `nn.py` | Chequeo suelto de `torch.cuda`. No tiene nada que ver con randomcutt. |

Lo vigente está en la raíz del repo: `randomcutt_v007.py`.

"""Generador de PDF mínimos para las pruebas.

Permite validar el lector interno de PDF sin depender de librerías externas ni
de archivos reales de banco. Produce un PDF con texto posicionado en columnas,
igual que hacen los extractos bancarios.
"""

from __future__ import annotations

import zlib


def _escapar(texto: str) -> str:
    return texto.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def construir_pdf(
    filas: list[list[tuple[float, str]]],
    *,
    comprimir: bool = True,
    y_inicial: float = 760.0,
    alto_linea: float = 12.0,
) -> bytes:
    """Arma un PDF de una página.

    `filas` es una lista de líneas; cada línea es una lista de (x, texto),
    que replica el comportamiento real de un PDF: cada celda de la tabla se
    dibuja como una cadena independiente con su propia posición.
    """
    partes = ["BT", "/F1 9 Tf"]
    y = y_inicial
    for fila in filas:
        for x, texto in fila:
            partes.append(f"1 0 0 1 {x:.2f} {y:.2f} Tm ({_escapar(texto)}) Tj")
        y -= alto_linea
    partes.append("ET")
    contenido = "\n".join(partes).encode("latin-1")

    if comprimir:
        stream = zlib.compress(contenido)
        dic_stream = f"<< /Length {len(stream)} /Filter /FlateDecode >>"
    else:
        stream = contenido
        dic_stream = f"<< /Length {len(stream)} >>"

    objetos: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        dic_stream.encode("latin-1") + b"\nstream\n" + stream + b"\nendstream",
        (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"
        ),
    ]

    salida = bytearray(b"%PDF-1.4\n")
    posiciones: list[int] = []
    for numero, cuerpo in enumerate(objetos, start=1):
        posiciones.append(len(salida))
        salida += f"{numero} 0 obj\n".encode("latin-1") + cuerpo + b"\nendobj\n"

    inicio_xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n".encode("latin-1")
    salida += b"0000000000 65535 f \n"
    for posicion in posiciones:
        salida += f"{posicion:010d} 00000 n \n".encode("latin-1")
    salida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\n"
        f"startxref\n{inicio_xref}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(salida)


def pdf_desde_texto(texto: str, **kwargs) -> bytes:
    """Convierte texto plano en PDF, separando columnas por 2+ espacios."""
    filas: list[list[tuple[float, str]]] = []
    for linea in texto.splitlines():
        if not linea.strip():
            filas.append([])
            continue
        columnas: list[tuple[float, str]] = []
        x = 40.0
        posicion = 0
        for trozo in linea.split("  "):
            if trozo.strip():
                # 4.5 pt por carácter es el ancho que asume el lector para una
                # fuente de 9 pt: así el viaje ida y vuelta conserva columnas.
                columnas.append((40.0 + posicion * 4.5, trozo.strip()))
            posicion += len(trozo) + 2
        filas.append(columnas)
    return construir_pdf(filas, **kwargs)

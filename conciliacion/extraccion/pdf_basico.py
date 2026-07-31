"""Extractor de texto de PDF escrito con solo la librería estándar.

Es la red de seguridad: si el usuario no tiene instalado pdfplumber ni PyMuPDF,
el programa igual puede leer extractos generados digitalmente (los de banca en
línea suelen serlo). No reemplaza a pdfplumber en PDF complejos, pero cubre el
caso común: texto plano comprimido con Flate y fuentes con /ToUnicode.

No sirve para PDF escaneados (esos son imágenes y requieren OCR).
"""

from __future__ import annotations

import re
import zlib

_RE_OBJETO = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", re.DOTALL)
_RE_STREAM = re.compile(rb"stream\r?\n?(.*?)\r?\n?endstream", re.DOTALL)


# ---------------------------------------------------------------------------
# Lectura de la estructura del archivo
# ---------------------------------------------------------------------------

def _partir_objeto(cuerpo: bytes) -> tuple[bytes, bytes | None]:
    """Separa el diccionario del objeto y su stream crudo (si tiene)."""
    m = _RE_STREAM.search(cuerpo)
    if not m:
        return cuerpo, None
    return cuerpo[: m.start()], m.group(1)


def _descomprimir(diccionario: bytes, crudo: bytes) -> bytes | None:
    """Aplica los filtros de descompresión soportados."""
    if crudo is None:
        return None
    filtros = re.findall(rb"/(FlateDecode|ASCIIHexDecode|ASCII85Decode|LZWDecode)",
                         diccionario)
    datos = crudo
    if not filtros:
        return datos
    for filtro in filtros:
        try:
            if filtro == b"FlateDecode":
                try:
                    datos = zlib.decompress(datos)
                except zlib.error:
                    # Streams con basura al final o sin cabecera zlib.
                    try:
                        datos = zlib.decompressobj().decompress(datos)
                    except zlib.error:
                        datos = zlib.decompressobj(-15).decompress(datos)
            elif filtro == b"ASCIIHexDecode":
                limpio = re.sub(rb"[^0-9A-Fa-f]", b"", datos.split(b">")[0])
                if len(limpio) % 2:
                    limpio += b"0"
                datos = bytes.fromhex(limpio.decode("ascii"))
            else:
                return None  # ASCII85 / LZW: poco frecuentes, no soportados
        except Exception:
            return None
    return datos


def _deshacer_predictor(diccionario: bytes, datos: bytes) -> bytes:
    """Revierte el predictor PNG usado por los xref/object streams."""
    m = re.search(rb"/Predictor\s+(\d+)", diccionario)
    if not m or int(m.group(1)) < 10:
        return datos
    columnas = 1
    mc = re.search(rb"/Columns\s+(\d+)", diccionario)
    if mc:
        columnas = int(mc.group(1))
    ancho = columnas + 1
    salida = bytearray()
    previa = bytearray(columnas)
    for inicio in range(0, len(datos) - ancho + 1, ancho):
        fila = bytearray(datos[inicio + 1: inicio + ancho])
        tipo = datos[inicio]
        if tipo == 2:  # Up
            for i in range(columnas):
                fila[i] = (fila[i] + previa[i]) & 0xFF
        salida.extend(fila)
        previa = fila
    return bytes(salida)


def _leer_objetos(data: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    """Mapa numero_objeto -> (diccionario, stream descomprimido)."""
    objetos: dict[int, tuple[bytes, bytes | None]] = {}
    for m in _RE_OBJETO.finditer(data):
        numero = int(m.group(1))
        diccionario, crudo = _partir_objeto(m.group(3))
        stream = _descomprimir(diccionario, crudo) if crudo is not None else None
        objetos[numero] = (diccionario, stream)

    # PDF 1.5+ guarda objetos dentro de object streams comprimidos.
    for diccionario, stream in list(objetos.values()):
        if stream is None or b"/ObjStm" not in diccionario:
            continue
        contenido = _deshacer_predictor(diccionario, stream)
        mn = re.search(rb"/N\s+(\d+)", diccionario)
        mfirst = re.search(rb"/First\s+(\d+)", diccionario)
        if not mn or not mfirst:
            continue
        cantidad, primero = int(mn.group(1)), int(mfirst.group(1))
        cabecera = contenido[:primero].split()
        try:
            pares = [
                (int(cabecera[i * 2]), int(cabecera[i * 2 + 1]))
                for i in range(cantidad)
            ]
        except (IndexError, ValueError):
            continue
        for indice, (numero, desplazamiento) in enumerate(pares):
            inicio = primero + desplazamiento
            fin = (
                primero + pares[indice + 1][1]
                if indice + 1 < len(pares)
                else len(contenido)
            )
            if numero not in objetos:
                objetos[numero] = (contenido[inicio:fin], None)
    return objetos


# ---------------------------------------------------------------------------
# Fuentes: mapa de códigos a caracteres
# ---------------------------------------------------------------------------

_RE_BFCHAR = re.compile(rb"beginbfchar(.*?)endbfchar", re.DOTALL)
_RE_BFRANGE = re.compile(rb"beginbfrange(.*?)endbfrange", re.DOTALL)
_RE_HEX = re.compile(rb"<([0-9A-Fa-f]+)>")


def _texto_unicode(hex_bytes: bytes) -> str:
    """Convierte el destino de un bfchar (UTF-16BE) a texto."""
    try:
        crudo = bytes.fromhex(hex_bytes.decode("ascii"))
    except ValueError:
        return ""
    if len(crudo) >= 2:
        try:
            return crudo.decode("utf-16-be", errors="ignore")
        except Exception:
            return ""
    return crudo.decode("latin-1", errors="ignore")


def _leer_cmap(contenido: bytes) -> tuple[dict[int, str], int]:
    """Interpreta un CMap /ToUnicode. Devuelve (mapa, bytes_por_codigo)."""
    mapa: dict[int, str] = {}
    anchos: list[int] = []

    for bloque in _RE_BFCHAR.findall(contenido):
        tokens = _RE_HEX.findall(bloque)
        for i in range(0, len(tokens) - 1, 2):
            origen, destino = tokens[i], tokens[i + 1]
            anchos.append(len(origen) // 2)
            try:
                mapa[int(origen, 16)] = _texto_unicode(destino)
            except ValueError:
                continue

    for bloque in _RE_BFRANGE.findall(contenido):
        for linea in re.finditer(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])",
            bloque,
            re.DOTALL,
        ):
            desde_hex, hasta_hex, destino = linea.group(1), linea.group(2), linea.group(3)
            anchos.append(len(desde_hex) // 2)
            try:
                desde, hasta = int(desde_hex, 16), int(hasta_hex, 16)
            except ValueError:
                continue
            if hasta - desde > 65535:
                continue
            if destino.startswith(b"["):
                for offset, item in enumerate(_RE_HEX.findall(destino)):
                    mapa[desde + offset] = _texto_unicode(item)
            else:
                base = _RE_HEX.findall(destino)
                if not base:
                    continue
                inicial = int(base[0], 16)
                for offset in range(hasta - desde + 1):
                    try:
                        mapa[desde + offset] = chr(inicial + offset)
                    except ValueError:
                        break

    ancho = 2 if anchos and max(anchos) >= 2 else 1
    return mapa, ancho


def _fuentes_de_recursos(
    recursos: bytes, objetos: dict[int, tuple[bytes, bytes | None]]
) -> dict[str, tuple[dict[int, str], int]]:
    """Construye el mapa nombre_de_fuente -> (cmap, bytes_por_codigo)."""
    fuentes: dict[str, tuple[dict[int, str], int]] = {}
    m = re.search(rb"/Font\s*(\d+)\s+\d+\s+R", recursos)
    if m:
        referencia = objetos.get(int(m.group(1)))
        recursos_fuente = referencia[0] if referencia else b""
    else:
        m = re.search(rb"/Font\s*<<(.*?)>>", recursos, re.DOTALL)
        recursos_fuente = m.group(1) if m else b""

    for nombre, numero in re.findall(rb"/([^\s/<>\[\]]+)\s+(\d+)\s+\d+\s+R",
                                     recursos_fuente):
        objeto = objetos.get(int(numero))
        if not objeto:
            continue
        dic_fuente = objeto[0]
        dos_bytes = b"Identity-H" in dic_fuente or b"/Type0" in dic_fuente
        mu = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", dic_fuente)
        if mu:
            objeto_cmap = objetos.get(int(mu.group(1)))
            if objeto_cmap and objeto_cmap[1]:
                mapa, ancho = _leer_cmap(objeto_cmap[1])
                fuentes[nombre.decode("latin-1")] = (
                    mapa,
                    2 if dos_bytes else ancho,
                )
                continue
        fuentes[nombre.decode("latin-1")] = ({}, 2 if dos_bytes else 1)
    return fuentes


# ---------------------------------------------------------------------------
# Interpretación del contenido de la página
# ---------------------------------------------------------------------------

def _decodificar_literal(crudo: bytes) -> bytes:
    """Resuelve los escapes de una cadena literal PDF: (texto\\(con\\)escapes)."""
    salida = bytearray()
    i = 0
    while i < len(crudo):
        c = crudo[i]
        if c == 0x5C and i + 1 < len(crudo):  # backslash
            siguiente = crudo[i + 1]
            mapa = {0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09, 0x62: 0x08, 0x66: 0x0C}
            if siguiente in mapa:
                salida.append(mapa[siguiente])
                i += 2
            elif 0x30 <= siguiente <= 0x37:  # octal
                digitos = ""
                j = i + 1
                while j < len(crudo) and len(digitos) < 3 and 0x30 <= crudo[j] <= 0x37:
                    digitos += chr(crudo[j])
                    j += 1
                salida.append(int(digitos, 8) & 0xFF)
                i = j
            elif siguiente in (0x0A, 0x0D):
                i += 2
            else:
                salida.append(siguiente)
                i += 2
        else:
            salida.append(c)
            i += 1
    return bytes(salida)


def _aplicar_fuente(
    crudo: bytes, fuente: tuple[dict[int, str], int] | None
) -> str:
    """Traduce los bytes de una cadena a texto usando el CMap de la fuente."""
    if fuente is None:
        return crudo.decode("latin-1", errors="replace")
    mapa, ancho = fuente
    if not mapa:
        if ancho == 2:
            # Sin ToUnicode y con códigos de 2 bytes no hay forma de saber
            # el carácter; se intenta la interpretación más probable.
            return "".join(
                chr(int.from_bytes(crudo[i:i + 2], "big"))
                for i in range(0, len(crudo) - 1, 2)
            )
        return crudo.decode("latin-1", errors="replace")
    piezas: list[str] = []
    for i in range(0, len(crudo), ancho):
        trozo = crudo[i:i + ancho]
        if len(trozo) < ancho:
            break
        codigo = int.from_bytes(trozo, "big")
        piezas.append(mapa.get(codigo, ""))
    return "".join(piezas)


_RE_TOKENS = re.compile(
    rb"""
      \((?P<literal>(?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*)\)\s*(?P<op_lit>Tj|TJ|'|")
    | <(?P<hexa>[0-9A-Fa-f\s]*)>\s*(?P<op_hex>Tj|TJ)
    | \[(?P<arreglo>(?:[^\[\]]|\\.)*)\]\s*TJ
    | (?P<nums>(?:[-\d.]+\s+){1,6})(?P<op_pos>Tm|Td|TD)
    | (?P<estrella>T\*)
    | /(?P<fuente>[^\s/<>\[\]]+)\s+(?P<tamano>[-\d.]+)\s+Tf
    | (?P<leading>[-\d.]+)\s+TL
    | (?P<inicio>BT)
    | (?P<fin>ET)
    """,
    re.DOTALL | re.VERBOSE,
)


def _extraer_de_contenido(
    contenido: bytes, fuentes: dict[str, tuple[dict[int, str], int]]
) -> str:
    """Recorre los operadores de texto reconstruyendo líneas y columnas."""
    lineas: list[str] = []
    actual: list[str] = []
    x = y = 0.0
    inicio_linea_x = 0.0
    ultimo_y: float | None = None
    x_cursor = 0.0
    leading = 12.0
    tamano = 9.0
    fuente_actual: tuple[dict[int, str], int] | None = None

    def cerrar() -> None:
        nonlocal actual
        if actual:
            texto = "".join(actual).rstrip()
            if texto.strip():
                lineas.append(texto)
        actual = []

    def escribir(texto: str) -> None:
        """Agrega texto reponiendo los espacios que separaban las columnas.

        Un PDF no guarda espacios entre celdas: guarda saltos de posición. Si se
        reemplazan por un solo espacio, se pierde el alineado de la tabla y con
        él la posibilidad de saber a qué columna pertenece cada número. Aquí el
        salto se traduce a la cantidad de espacios equivalente, estimando el
        ancho de carácter como la mitad del tamaño de la fuente.
        """
        nonlocal ultimo_y, x_cursor
        if not texto:
            return
        if ultimo_y is not None and abs(y - ultimo_y) > 1.2:
            cerrar()
        ancho_caracter = max(1.0, abs(tamano) * 0.5)
        if not actual:
            # Sangría desde el margen izquierdo, para que todas las líneas
            # queden en la misma escala de columnas.
            relleno = int(round(x / ancho_caracter))
            if relleno > 0:
                actual.append(" " * relleno)
        else:
            faltantes = int(round((x - x_cursor) / ancho_caracter))
            if faltantes > 0:
                actual.append(" " * faltantes)
            elif x - x_cursor > 0.5:
                actual.append(" ")
        actual.append(texto)
        x_cursor = x + len(texto) * ancho_caracter
        ultimo_y = y

    for m in _RE_TOKENS.finditer(contenido):
        if m.group("literal") is not None:
            operador = m.group("op_lit")
            if operador in (b"'", b'"'):
                cerrar()
                y -= leading
            escribir(_aplicar_fuente(_decodificar_literal(m.group("literal")),
                                     fuente_actual))
        elif m.group("hexa") is not None:
            limpio = re.sub(rb"\s", b"", m.group("hexa"))
            if len(limpio) % 2:
                limpio += b"0"
            try:
                escribir(_aplicar_fuente(bytes.fromhex(limpio.decode("ascii")),
                                         fuente_actual))
            except ValueError:
                pass
        elif m.group("arreglo") is not None:
            partes: list[str] = []
            for elemento in re.finditer(
                rb"\((?P<lit>(?:\\.|[^\\()])*)\)|<(?P<hx>[0-9A-Fa-f\s]*)>"
                rb"|(?P<num>-?\d+(?:\.\d+)?)",
                m.group("arreglo"),
                re.DOTALL,
            ):
                if elemento.group("lit") is not None:
                    partes.append(
                        _aplicar_fuente(
                            _decodificar_literal(elemento.group("lit")), fuente_actual
                        )
                    )
                elif elemento.group("hx") is not None:
                    limpio = re.sub(rb"\s", b"", elemento.group("hx"))
                    if len(limpio) % 2:
                        limpio += b"0"
                    try:
                        partes.append(
                            _aplicar_fuente(
                                bytes.fromhex(limpio.decode("ascii")), fuente_actual
                            )
                        )
                    except ValueError:
                        pass
                else:
                    ajuste = float(elemento.group("num"))
                    if ajuste < -180:  # espacio explícito entre glifos
                        partes.append(" ")
            escribir("".join(partes))
        elif m.group("nums") is not None:
            numeros = [float(n) for n in m.group("nums").split()]
            operador = m.group("op_pos")
            if operador == b"Tm" and len(numeros) >= 6:
                x, y = numeros[4], numeros[5]
                inicio_linea_x = x
            elif len(numeros) >= 2:
                if operador == b"TD":
                    leading = -numeros[1]
                x += numeros[0]
                y += numeros[1]
                inicio_linea_x = x
            if ultimo_y is not None and abs(y - ultimo_y) > 1.2:
                cerrar()
        elif m.group("estrella") is not None:
            cerrar()
            y -= leading
            x = inicio_linea_x
        elif m.group("fuente") is not None:
            fuente_actual = fuentes.get(m.group("fuente").decode("latin-1"))
            try:
                tamano = float(m.group("tamano"))
            except (TypeError, ValueError):
                pass
        elif m.group("leading") is not None:
            leading = abs(float(m.group("leading")))
        elif m.group("fin") is not None:
            cerrar()

    cerrar()
    return "\n".join(lineas)


def _contenidos_de_pagina(
    dic_pagina: bytes, objetos: dict[int, tuple[bytes, bytes | None]]
) -> bytes:
    referencias = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", dic_pagina)
    if not referencias:
        return b""
    piezas = []
    for numero in re.findall(rb"(\d+)\s+\d+\s+R", referencias.group(1)):
        objeto = objetos.get(int(numero))
        if objeto and objeto[1]:
            piezas.append(objeto[1])
    return b"\n".join(piezas)


def _recursos_de_pagina(
    dic_pagina: bytes, objetos: dict[int, tuple[bytes, bytes | None]]
) -> bytes:
    """Obtiene /Resources, subiendo al padre si la página los hereda."""
    visitados = 0
    actual = dic_pagina
    while actual is not None and visitados < 8:
        m = re.search(rb"/Resources\s*(<<.*?>>\s*(?:>>)?|\d+\s+\d+\s+R)", actual,
                      re.DOTALL)
        if m:
            valor = m.group(1)
            mref = re.fullmatch(rb"(\d+)\s+\d+\s+R", valor.strip())
            if mref:
                objeto = objetos.get(int(mref.group(1)))
                return objeto[0] if objeto else b""
            return valor
        mp = re.search(rb"/Parent\s+(\d+)\s+\d+\s+R", actual)
        if not mp:
            break
        objeto = objetos.get(int(mp.group(1)))
        actual = objeto[0] if objeto else None
        visitados += 1
    return b""


def extraer_paginas(data: bytes) -> list[str]:
    """Devuelve el texto de cada página del PDF."""
    objetos = _leer_objetos(data)
    paginas: list[str] = []

    numeros_pagina = [
        numero
        for numero, (diccionario, _) in sorted(objetos.items())
        if re.search(rb"/Type\s*/Page(?![sO])", diccionario)
    ]

    if numeros_pagina:
        for numero in numeros_pagina:
            diccionario, _ = objetos[numero]
            contenido = _contenidos_de_pagina(diccionario, objetos)
            if not contenido:
                continue
            fuentes = _fuentes_de_recursos(
                _recursos_de_pagina(diccionario, objetos), objetos
            )
            paginas.append(_extraer_de_contenido(contenido, fuentes))
        if any(p.strip() for p in paginas):
            return paginas

    # Sin árbol de páginas legible: se procesa todo stream que parezca texto.
    fuentes_globales: dict[str, tuple[dict[int, str], int]] = {}
    for diccionario, _ in objetos.values():
        if b"/Font" in diccionario:
            fuentes_globales.update(_fuentes_de_recursos(diccionario, objetos))
    sueltas: list[str] = []
    for _, stream in sorted(objetos.items()):
        contenido = stream[1]
        if not contenido or b"BT" not in contenido:
            continue
        texto = _extraer_de_contenido(contenido, fuentes_globales)
        if texto.strip():
            sueltas.append(texto)
    return sueltas


def extraer(ruta: str) -> list[str]:
    with open(ruta, "rb") as archivo:
        return extraer_paginas(archivo.read())

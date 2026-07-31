"""Motor de parseo: convierte el texto de un extracto en movimientos.

El problema central es decidir si cada movimiento es ingreso o egreso, porque
los extractos no siempre lo dicen de forma explícita. Se usan cuatro señales,
de la más confiable a la menos:

  1. SALDO   - la diferencia entre el saldo de una fila y el de la anterior.
               Es aritmética pura, no admite discusión.
  2. COLUMNA - la posición horizontal del número: si cae bajo la columna
               "DÉBITOS" es un egreso. Requiere que el texto conserve el
               espaciado original.
  3. SIGNO   - el valor traía '-', paréntesis o el sufijo DB.
  4. PALABRAS- se deduce de la descripción ("consignación" vs "retiro").
               Es la única heurística, y queda marcada como tal para que el
               contador la pueda revisar.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .modelos import Confianza, Extracto, Movimiento
from .normalizacion import (
    CENTAVOS,
    RE_MONTO_CON_SIMBOLO,
    RE_MONTO_DEBIL,
    RE_MONTO_FUERTE,
    buscar_fecha,
    clave,
    clave_posicional,
    es_negativo_explicito,
    limpiar_linea,
    parse_fecha,
    parse_monto,
)
from .perfiles import (
    GENERICO,
    PALABRAS_EGRESO,
    PALABRAS_INGRESO,
    PATRONES_PERIODO,
    PATRONES_SALDO_FINAL,
    PATRONES_SALDO_INICIAL,
    PATRONES_TOTAL_EGRESOS,
    PATRONES_TOTAL_INGRESOS,
    PERFILES,
    REGLAS_PRIORITARIAS,
    PerfilBanco,
)

TOLERANCIA = Decimal("0.02")


# ---------------------------------------------------------------------------
# Detección del banco
# ---------------------------------------------------------------------------

def detectar_banco(texto: str) -> tuple[PerfilBanco, int]:
    """Identifica el banco contando coincidencias de sus huellas.

    Se revisa sobre todo el encabezado, donde está la marca del banco, pero se
    cuenta en todo el documento por si el logo quedó en el pie de página.
    """
    canon = clave(texto)
    encabezado = clave("\n".join(texto.splitlines()[:25]))

    mejor, puntaje_mejor = GENERICO, 0
    for perfil in PERFILES:
        puntaje = 0
        for huella in perfil.huellas:
            if re.search(huella, encabezado):
                puntaje += 3
            elif re.search(huella, canon):
                puntaje += 1
        if puntaje > puntaje_mejor:
            mejor, puntaje_mejor = perfil, puntaje
    return mejor, puntaje_mejor


def detectar_banco_por_archivo(ruta: str) -> PerfilBanco | None:
    """Último recurso: deducir el banco del nombre del archivo."""
    from .perfiles import perfil_por_nombre

    return perfil_por_nombre(os.path.basename(ruta))


# ---------------------------------------------------------------------------
# Tokens monetarios
# ---------------------------------------------------------------------------

@dataclass
class _Token:
    texto: str
    valor: Decimal
    inicio: int
    fin: int
    negativo: bool

    @property
    def centro(self) -> float:
        return (self.inicio + self.fin) / 2


def _token_sospechoso(texto: str, valor: Decimal) -> bool:
    """Descarta números que claramente no son montos (celulares, documentos)."""
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) >= 10 and "," not in texto and "." not in texto:
        return True  # cédulas, celulares, referencias largas
    if len(digitos) == 10 and digitos.startswith("3"):
        return True  # celular colombiano
    return False


def _tokens_monetarios(linea: str, permitir_debiles: bool) -> list[_Token]:
    """Encuentra los valores monetarios de una línea, con su posición.

    La posición importa: es lo que permite saber si un número está en la
    columna de débitos o en la de créditos.
    """
    encontrados: list[re.Match[str]] = []
    for regex in (RE_MONTO_FUERTE, RE_MONTO_CON_SIMBOLO):
        encontrados.extend(regex.finditer(linea))

    if not encontrados and permitir_debiles:
        encontrados.extend(RE_MONTO_DEBIL.finditer(linea))

    encontrados.sort(key=lambda m: (m.start(), -(m.end() - m.start())))

    tokens: list[_Token] = []
    fin_previo = -1
    for m in encontrados:
        if m.start() < fin_previo:  # solapado con uno más largo ya aceptado
            continue
        bruto = m.group(0)
        texto = bruto.strip()
        valor = parse_monto(texto)
        if valor is None:
            continue
        if _token_sospechoso(texto, valor):
            continue
        # La expresión regular admite espacios alrededor del monto (para tolerar
        # "$ 50.000"), así que el tramo capturado incluye el relleno de la
        # columna. Hay que quedarse con el tramo del número visible: de ahí sale
        # la posición con la que después se decide a qué columna pertenece.
        inicio = m.start() + (len(bruto) - len(bruto.lstrip()))
        fin = m.end() - (len(bruto) - len(bruto.rstrip()))
        tokens.append(
            _Token(
                texto=texto,
                valor=abs(valor),
                inicio=inicio,
                fin=fin,
                negativo=es_negativo_explicito(texto),
            )
        )
        fin_previo = m.end()
    return tokens


# ---------------------------------------------------------------------------
# Columnas: se aprenden del encabezado de la tabla
# ---------------------------------------------------------------------------

_ETIQUETAS = (
    ("debito", r"(?:VALOR\s+)?D[EÉ]BITOS?\b"),
    ("credito", r"(?:VALOR\s+)?CR[EÉ]DITOS?\b"),
    ("saldo", r"SALDO\b"),
    ("valor", r"VALOR\b(?!\s+(?:D[EÉ]BITO|CR[EÉ]DITO))"),
)


def conserva_columnas(lineas: list[str]) -> bool:
    """¿El texto extraído conservó el alineado en columnas?

    Toda la lógica posicional (asignar un número a la columna "Débitos", saber
    dónde termina la descripción) depende de que el espaciado original siga ahí.
    Algunos extractores devuelven el texto con un solo espacio entre celdas, y
    en ese caso las posiciones son basura: usarlas produce errores peores que
    no usarlas, como clasificar un débito bajo la columna de créditos.

    La señal es simple: un texto en columnas tiene muchas líneas con dos o más
    espacios seguidos entre dos caracteres visibles.
    """
    utiles = [l for l in lineas if len(l.strip()) > 20]
    if len(utiles) < 4:
        return False
    con_separacion = sum(1 for l in utiles if re.search(r"\S {2,}\S", l))
    return con_separacion >= max(3, len(utiles) * 0.25)


def _detectar_columnas(lineas: list[str]) -> dict[str, float]:
    """Ubica el centro de cada columna de valores usando la fila de títulos."""
    mejor: dict[str, float] = {}
    for linea in lineas:
        # Sin colapsar espacios: aquí la posición de cada título ES el dato.
        canon = clave_posicional(linea)
        if "FECHA" not in canon:
            continue
        posiciones: dict[str, float] = {}
        for nombre, patron in _ETIQUETAS:
            m = re.search(patron, canon)
            if m:
                posiciones[nombre] = (m.start() + m.end()) / 2
        # Si ya hay débito/crédito, "valor" es solo el prefijo de esos títulos.
        if "debito" in posiciones or "credito" in posiciones:
            posiciones.pop("valor", None)
        if len(posiciones) > len(mejor):
            mejor = posiciones
    return mejor


def _asignar_columna(
    token: _Token, columnas: dict[str, float], tolerancia: float = 16.0
) -> str | None:
    """Devuelve el nombre de la columna más cercana al token."""
    if not columnas:
        return None
    nombre, distancia = min(
        ((n, abs(token.centro - c)) for n, c in columnas.items()),
        key=lambda par: par[1],
    )
    return nombre if distancia <= tolerancia else None


_RE_MARCADOR_MONEDA = re.compile(r"(?i)\b(?:COP|COL\$|USD|EUR|CR|DB|CT|DT)\b")
_RE_LETRA = re.compile(r"[^\W\d_]", re.UNICODE)


def _en_zona_de_valores(linea: str, fin_token: int) -> bool:
    """¿El monto pertenece a las columnas de valores y no a la descripción?

    Banco de Bogotá escribe descripciones como "Consignacion nacional
    (10,000.00 en efectivo $0.00 en cheque)". Son montos de verdad, pero son
    parte del texto, y tomarlos como el valor del movimiento es el peor error
    posible.

    Lo que los distingue no es la posición (que se pierde según cómo se extraiga
    el texto) sino la estructura de la fila: las columnas de valores están al
    final, así que a la derecha de un valor real solo puede haber otros valores.
    Si después del monto todavía hay palabras, el monto es parte de la
    descripción.
    """
    cola = _RE_MARCADOR_MONEDA.sub(" ", linea[fin_token:])
    return not _RE_LETRA.search(cola)


def _tokens_de_valores(tokens: list[_Token], linea: str) -> list[_Token]:
    return [t for t in tokens if _en_zona_de_valores(linea, t.fin)]


# ---------------------------------------------------------------------------
# Clasificación por descripción
# ---------------------------------------------------------------------------

def clasificar_descripcion(
    descripcion: str, perfil: PerfilBanco
) -> tuple[int, Confianza]:
    """Deduce el signo de la descripción.

    Devuelve (+1 ingreso / -1 egreso / 0 no se sabe) y de dónde salió: una
    marca del propio banco es bastante más confiable que una palabra suelta,
    y esa diferencia se reporta al usuario.
    """
    canon = clave(descripcion)
    if not canon:
        return 0, Confianza.DESCONOCIDA

    # Las reglas del propio banco van primero: AV Villas marca la naturaleza
    # con el prefijo CRE/DEB, y "CRE PAGO PROVEEDOR" es una entrada de plata
    # aunque diga "pago". Igual "Cargo omision consignacion" en Bogotá es un
    # egreso aunque diga "consignacion".
    for patron, signo in perfil.reglas_signo:
        if re.search(patron, canon):
            return signo, Confianza.MARCA

    for frase, tipo in REGLAS_PRIORITARIAS:
        if frase in canon:
            return (1 if tipo == "INGRESO" else -1), Confianza.PALABRAS

    for palabra in perfil.palabras_ingreso:
        if palabra in canon:
            return 1, Confianza.PALABRAS
    for palabra in perfil.palabras_egreso:
        if palabra in canon:
            return -1, Confianza.PALABRAS

    # Se compara qué coincidencia es más específica (la frase más larga gana).
    mejor_egreso = max(
        (len(p) for p in PALABRAS_EGRESO if re.search(rf"\b{re.escape(p)}", canon)),
        default=0,
    )
    mejor_ingreso = max(
        (len(p) for p in PALABRAS_INGRESO if re.search(rf"\b{re.escape(p)}", canon)),
        default=0,
    )
    if mejor_ingreso > mejor_egreso:
        return 1, Confianza.PALABRAS
    if mejor_egreso > mejor_ingreso:
        return -1, Confianza.PALABRAS
    return 0, Confianza.DESCONOCIDA


def clasificar_por_palabras(descripcion: str, perfil: PerfilBanco) -> int:
    """Solo el signo, sin el origen. Se conserva por comodidad de uso."""
    return clasificar_descripcion(descripcion, perfil)[0]


# ---------------------------------------------------------------------------
# Metadatos del extracto
# ---------------------------------------------------------------------------

def _monto_tras_etiqueta(
    linea: str, canon: str, patrones: tuple[str, ...]
) -> Decimal | None:
    """Devuelve el primer monto que aparece después de la etiqueta buscada.

    En los extractos reales los cuadros del resumen están uno al lado del otro,
    así que una misma línea de texto puede traer "Saldo inicial 7,954,713.11" y
    "Saldo promedio 8,149,222.10". Tomar el último monto de la línea daría el
    valor equivocado: hay que tomar el que sigue a la etiqueta.
    """
    for patron in patrones:
        m = re.search(patron, canon)
        if not m:
            continue
        for token in _tokens_monetarios(linea, permitir_debiles=True):
            if token.inicio >= m.end():
                return -token.valor if token.negativo else token.valor
    return None


def _extraer_periodo(
    canon: str, anio_documento: int | None = None
) -> tuple[date, date] | None:
    """Lee el rango del extracto de una línea tipo 'DESDE ... HASTA ...'.

    Banco de Bogotá escribe 'Desde: Julio 01  Hasta: Julio 31' sin el año en
    esa misma línea, así que se acepta el año deducido del documento.
    """
    for patron in PATRONES_PERIODO:
        m = re.search(patron, canon)
        if not m:
            continue
        anios = re.findall(r"\b(20\d{2})\b", canon)
        anio = int(anios[-1]) if anios else anio_documento
        inicio = parse_fecha(m.group("a"), anio)
        fin = parse_fecha(m.group("b"), anio)
        if inicio and fin and inicio <= fin:
            return inicio, fin
    return None


def _anio_probable(lineas: list[str], archivo: str) -> int | None:
    """Deduce el año cuando el extracto solo imprime día y mes."""
    candidatos = Counter()
    for linea in lineas[:60]:
        for anio in re.findall(r"\b(20\d{2})\b", linea):
            candidatos[int(anio)] += 1
    if candidatos:
        return candidatos.most_common(1)[0][0]
    m = re.search(r"\b(20\d{2})\b", os.path.basename(archivo))
    if m:
        return int(m.group(1))
    return None


def _leer_metadatos(
    lineas: list[str],
    perfil: PerfilBanco,
    extracto: Extracto,
    anio_documento: int | None = None,
) -> None:
    for linea in lineas:
        canon = clave(linea)
        if not canon:
            continue

        if extracto.cuenta is None:
            for patron in perfil.patrones_cuenta:
                m = re.search(patron, canon)
                if m:
                    cuenta = re.sub(r"\s+", "", m.group(1)).strip("-.")
                    if len(re.sub(r"\D", "", cuenta)) >= 6:
                        extracto.cuenta = cuenta
                        break

        if extracto.periodo_inicio is None:
            periodo = _extraer_periodo(canon, anio_documento)
            if periodo:
                extracto.periodo_inicio, extracto.periodo_fin = periodo

        if extracto.saldo_inicial is None:
            extracto.saldo_inicial = _monto_tras_etiqueta(
                linea, canon, PATRONES_SALDO_INICIAL
            )

        if extracto.saldo_final is None:
            extracto.saldo_final = _monto_tras_etiqueta(
                linea, canon, PATRONES_SALDO_FINAL
            )

        if extracto.total_ingresos_reportado is None:
            valor = _monto_tras_etiqueta(linea, canon, PATRONES_TOTAL_INGRESOS)
            if valor is not None:
                extracto.total_ingresos_reportado = abs(valor)

        if extracto.total_egresos_reportado is None:
            valor = _monto_tras_etiqueta(linea, canon, PATRONES_TOTAL_EGRESOS)
            if valor is not None:
                extracto.total_egresos_reportado = abs(valor)


# ---------------------------------------------------------------------------
# Ajuste de signos con el saldo corriente
# ---------------------------------------------------------------------------

def _recorrer_saldos(
    secuencia: list[Movimiento], saldo_inicial: Decimal | None, aplicar: bool
) -> int:
    """Compara cada saldo con el anterior. Devuelve cuántas filas validó.

    Con `aplicar` en False solo cuenta, sin modificar nada: así se puede probar
    un sentido de lectura antes de comprometerse con él.
    """
    aciertos = 0
    previo = saldo_inicial
    for movimiento in secuencia:
        if movimiento.saldo is None:
            previo = None
            continue
        if previo is not None:
            delta = (movimiento.saldo - previo).quantize(CENTAVOS)
            if abs(abs(delta) - abs(movimiento.valor)) <= TOLERANCIA and delta != 0:
                aciertos += 1
                if aplicar:
                    movimiento.valor = delta
                    movimiento.confianza = Confianza.SALDO
        previo = movimiento.saldo
    return aciertos


def _orden_por_fechas(movimientos: list[Movimiento]) -> int:
    """+1 si las filas van de la más antigua a la más reciente, -1 al contrario."""
    ascendentes = descendentes = 0
    for anterior, siguiente in zip(movimientos, movimientos[1:]):
        if siguiente.fecha > anterior.fecha:
            ascendentes += 1
        elif siguiente.fecha < anterior.fecha:
            descendentes += 1
    return -1 if descendentes > ascendentes else 1


def ajustar_con_saldo(
    movimientos: list[Movimiento], saldo_inicial: Decimal | None
) -> tuple[list[Movimiento], int, bool]:
    """Corrige los signos usando el saldo corriente del extracto.

    Es la parte que hace confiable el resultado: cuando hay saldo, el signo
    deja de ser una adivinanza y pasa a ser una resta.

    El detalle fino es el sentido de lectura. Nequi imprime los movimientos del
    más reciente al más antiguo, de modo que el saldo de una fila corresponde a
    la resta de la fila de abajo, no de la de arriba. Aplicar el sentido
    equivocado no falla: invierte el signo de todos los movimientos, que es
    peor. Por eso se prueban los dos sentidos sin modificar nada y se escoge el
    que valide más filas; solo entonces se aplica.

    Devuelve (secuencia en orden cronológico, filas validadas, iba al revés).
    """
    if not movimientos:
        return movimientos, 0, False

    invertida = list(reversed(movimientos))
    aciertos_directo = _recorrer_saldos(movimientos, saldo_inicial, aplicar=False)
    aciertos_inverso = _recorrer_saldos(invertida, saldo_inicial, aplicar=False)

    if aciertos_inverso > aciertos_directo:
        al_reves = True
    elif aciertos_directo > aciertos_inverso:
        al_reves = False
    else:
        # Empate (o ningún saldo que validar): decide el orden de las fechas.
        al_reves = _orden_por_fechas(movimientos) < 0

    secuencia = invertida if al_reves else movimientos
    ajustados = _recorrer_saldos(secuencia, saldo_inicial, aplicar=True)
    return secuencia, ajustados, al_reves


# ---------------------------------------------------------------------------
# Parseo principal
# ---------------------------------------------------------------------------

def _es_ignorable(canon: str, patrones: tuple[str, ...]) -> bool:
    return any(re.search(patron, canon) for patron in patrones)


_PATRONES_METADATO: tuple[str, ...] = (
    PATRONES_SALDO_INICIAL
    + PATRONES_SALDO_FINAL
    + PATRONES_TOTAL_INGRESOS
    + PATRONES_TOTAL_EGRESOS
)


def _es_metadato(canon: str) -> bool:
    """Líneas de saldos y totales: ya se leyeron como metadatos del extracto."""
    return any(re.search(patron, canon) for patron in _PATRONES_METADATO)


def _resolver_fecha(
    linea: str, anio_defecto: int | None, extracto: Extracto
) -> tuple[date, tuple[int, int]] | None:
    """Busca la fecha resolviendo el año cuando el extracto lo omite.

    Un extracto de diciembre a enero tiene fechas '28/12' y '03/01' que
    pertenecen a años distintos: se elige el año que caiga dentro del periodo.
    """
    hallazgo = buscar_fecha(linea, anio_defecto)
    if not hallazgo:
        return None
    fecha, span = hallazgo
    inicio, fin = extracto.periodo_inicio, extracto.periodo_fin
    if inicio and fin and not (inicio <= fecha <= fin):
        for delta in (-1, 1):
            try:
                alternativa = fecha.replace(year=fecha.year + delta)
            except ValueError:
                continue
            if inicio <= alternativa <= fin:
                return alternativa, span
    return fecha, span


def analizar(
    texto: str | None = None,
    *,
    paginas: list[str] | None = None,
    perfil: PerfilBanco | None = None,
    archivo: str = "",
    anio_defecto: int | None = None,
    motor_texto: str = "",
) -> Extracto:
    """Convierte el texto de un extracto en un objeto Extracto con movimientos."""
    if paginas is None:
        paginas = [texto or ""]
    if texto is None:
        texto = "\n".join(paginas)

    detectado = False
    if perfil is None:
        perfil, puntaje = detectar_banco(texto)
        detectado = True
        if puntaje == 0:
            por_nombre = detectar_banco_por_archivo(archivo)
            if por_nombre:
                perfil = por_nombre

    extracto = Extracto(
        banco=perfil.nombre,
        archivo=archivo,
        motor_texto=motor_texto,
        detectado_automaticamente=detectado,
    )

    # Se conservan dos versiones de cada línea: la cruda (con el espaciado
    # original, necesaria para ubicar columnas) y la normalizada.
    numeradas: list[tuple[int, str]] = []
    for numero_pagina, contenido in enumerate(paginas, start=1):
        for linea in contenido.splitlines():
            numeradas.append((numero_pagina, linea.replace("\t", "    ").rstrip()))

    lineas_normalizadas = [limpiar_linea(l) for _, l in numeradas]

    # El año del documento se necesita antes de leer los metadatos, porque hay
    # extractos que escriben el periodo sin año ("Desde: Julio 01").
    anio_documento = _anio_probable(lineas_normalizadas, archivo)
    _leer_metadatos(lineas_normalizadas, perfil, extracto, anio_documento)

    if anio_defecto is None:
        anio_defecto = (
            extracto.periodo_fin.year if extracto.periodo_fin else anio_documento
        )

    lineas_crudas = [l for _, l in numeradas]
    columnas = _detectar_columnas(lineas_crudas)
    alineado = conserva_columnas(lineas_crudas)
    patrones_ignorar = perfil.patrones_ignorar

    # El encabezado real de la tabla es mejor fuente que el perfil: el mismo
    # banco cambia la estructura entre productos y entre años. AV Villas trae
    # solo VALOR, mientras Nequi y Bogotá traen VALOR y SALDO.
    esquema = perfil.esquema
    if columnas:
        if "debito" in columnas or "credito" in columnas:
            esquema = "debito_credito_saldo"
        elif "saldo" in columnas:
            esquema = "valor_saldo"
        else:
            esquema = "valor"
    # Qué columnas existen se sabe por el encabezado (no depende del espaciado);
    # dónde está cada una, solo si el texto conservó el alineado.
    usa_columnas = (
        alineado
        and bool(columnas)
        and ("debito" in columnas or "credito" in columnas or "saldo" in columnas)
    )


    movimientos: list[Movimiento] = []
    indice_ultimo = -2
    # Fila con fecha cuyos valores aún no aparecen: (fecha, descripción,
    # índice de línea, página).
    pendiente: tuple[date, str, int, int] | None = None

    for indice, (numero_pagina, linea_cruda) in enumerate(numeradas):
        linea = lineas_normalizadas[indice]
        if not linea:
            continue
        canon = clave(linea)

        if _es_ignorable(canon, patrones_ignorar) or _es_metadato(canon):
            continue

        hallazgo = _resolver_fecha(linea_cruda, anio_defecto, extracto)
        # Solo cuentan como valores los montos que están en las columnas de
        # valores; los que llevan texto detrás son parte de la descripción.
        tokens = _tokens_de_valores(
            _tokens_monetarios(linea_cruda, perfil.montos_debiles), linea_cruda
        )

        if hallazgo and not tokens:
            # Fila cuya descripción es tan larga que el PDF empujó los valores
            # a la línea siguiente. Se guarda y se completa más abajo.
            fecha_pendiente, span_pendiente = hallazgo
            pendiente = (
                fecha_pendiente,
                limpiar_linea(linea_cruda[span_pendiente[1]:]).strip(" -|.,;:"),
                indice,
                numero_pagina,
            )
            continue

        if not tokens:
            # Línea suelta: probablemente la continuación de la descripción.
            if (
                perfil.unir_descripciones
                and movimientos
                and indice == indice_ultimo + 1
                and 3 <= len(linea) <= 60
                and re.search(r"[A-Za-zÁ-Úá-ú]{3}", linea)
            ):
                movimientos[-1].descripcion = (
                    f"{movimientos[-1].descripcion} {linea}".strip()
                )
                indice_ultimo = indice
            continue

        prefijo_descripcion = ""
        if hallazgo:
            fecha = hallazgo[0]
            inicio_descripcion = max(hallazgo[1][1], 0)
            pendiente = None
        elif pendiente is not None and indice - pendiente[2] <= 2:
            # Los valores de la fila pendiente: se hereda fecha y descripción.
            fecha, prefijo_descripcion, _, numero_pagina = pendiente
            inicio_descripcion = 0
            pendiente = None
        else:
            extracto.lineas_no_reconocidas.append(linea)
            continue

        tokens_valor = [t for t in tokens if t.inicio >= inicio_descripcion]
        if not tokens_valor:
            extracto.lineas_no_reconocidas.append(linea)
            continue

        # La descripción es lo que queda entre la fecha y el primer valor.
        descripcion = limpiar_linea(
            f"{prefijo_descripcion} "
            f"{linea_cruda[inicio_descripcion:tokens_valor[0].inicio]}"
        ).strip(" -|.,;:")

        # Los códigos de transacción que algunos bancos ponen antes de la
        # descripción (el "Cod Trans" de Banco de Bogotá) son ruido de columna.
        cabeza = re.match(r"^(?:[\d\-]{2,8}\s+){1,2}(?=[A-Za-zÁ-Úá-ú])", descripcion)
        if cabeza:
            recortada = descripcion[cabeza.end():]
            if re.search(r"[A-Za-zÁ-Úá-ú]{3}", recortada):
                descripcion = recortada

        # Las columnas de oficina, documento y referencia quedan al final de la
        # descripción: se separan para que el detalle sea legible.
        documento = None
        cola = re.search(r"((?:\s+[\d\-]{3,20}){1,3})\s*$", descripcion)
        if cola:
            codigos = cola.group(1).split()
            documento = next(
                (c for c in reversed(codigos) if re.sub(r"\D", "", c).strip("0")),
                codigos[-1] if codigos else None,
            )
            recortada = descripcion[: cola.start()].strip(" -|.,;:")
            if re.search(r"[A-Za-zÁ-Úá-ú]{3}", recortada):
                descripcion = recortada

        valor: Decimal | None = None
        saldo: Decimal | None = None
        confianza = Confianza.DESCONOCIDA

        # El reparto es estructural: cuando la tabla tiene columna de saldo, el
        # saldo es el último valor de la fila. Eso no depende del espaciado.
        candidatos = tokens_valor
        if esquema != "valor" and len(tokens_valor) >= 2:
            token_saldo = tokens_valor[-1]
            saldo = -token_saldo.valor if token_saldo.negativo else token_saldo.valor
            candidatos = tokens_valor[:-1]

        # Si el banco imprime las dos columnas y rellena con ceros la que no
        # aplica, el valor del movimiento es el que no es cero.
        no_cero = [t for t in candidatos if t.valor != 0]
        token_valor = (no_cero or candidatos)[-1]

        # La posición solo se usa para lo que la estructura no resuelve:
        # distinguir un débito de un crédito cuando son columnas separadas.
        if usa_columnas and esquema == "debito_credito_saldo":
            naturaleza = {
                nombre: centro
                for nombre, centro in columnas.items()
                if nombre in ("debito", "credito")
            }
            columna = None
            if len(naturaleza) == 2:
                columna = min(
                    naturaleza, key=lambda n: abs(token_valor.centro - naturaleza[n])
                )
            elif naturaleza:
                columna = _asignar_columna(token_valor, naturaleza, tolerancia=20.0)
            if columna == "debito":
                valor = -token_valor.valor
                confianza = Confianza.COLUMNA
            elif columna == "credito":
                valor = token_valor.valor
                confianza = Confianza.COLUMNA

        if valor is None:
            valor = -token_valor.valor if token_valor.negativo else token_valor.valor
            confianza = (
                Confianza.SIGNO if token_valor.negativo else Confianza.DESCONOCIDA
            )

        movimientos.append(
            Movimiento(
                banco=perfil.nombre,
                fecha=fecha,
                descripcion=descripcion or "(sin descripción)",
                valor=valor,
                cuenta=extracto.cuenta,
                saldo=saldo,
                documento=documento,
                confianza=confianza,
                archivo=archivo,
                pagina=numero_pagina,
                linea_original=linea,
            )
        )
        indice_ultimo = indice

    # 1) El saldo corriente manda sobre cualquier otra señal. Aquí también se
    #    resuelve el sentido de lectura del extracto.
    movimientos, ajustados, al_reves = ajustar_con_saldo(
        movimientos, extracto.saldo_inicial
    )
    if al_reves:
        extracto.advertencias.append(
            "El extracto lista los movimientos del más reciente al más antiguo; "
            "se leyó en ese sentido."
        )

    # 2) Lo que quedó sin clasificar se resuelve por descripción.
    sin_clasificar = 0
    for movimiento in movimientos:
        if movimiento.confianza in (Confianza.SALDO, Confianza.COLUMNA, Confianza.SIGNO):
            continue
        signo, origen = clasificar_descripcion(movimiento.descripcion, perfil)
        if signo:
            movimiento.confianza = origen
            movimiento.valor = (
                abs(movimiento.valor) if signo > 0 else -abs(movimiento.valor)
            )
        else:
            sin_clasificar += 1

    extracto.movimientos = movimientos

    # Saldos deducidos cuando el extracto no los imprime en texto.
    con_saldo = [m for m in movimientos if m.saldo is not None]
    if extracto.saldo_inicial is None and con_saldo and ajustados:
        primero = con_saldo[0]
        extracto.saldo_inicial = (primero.saldo - primero.valor).quantize(CENTAVOS)
        extracto.advertencias.append(
            "El saldo inicial se dedujo del primer movimiento (el extracto no lo "
            "traía en texto legible)."
        )
    if extracto.saldo_final is None and con_saldo:
        extracto.saldo_final = con_saldo[-1].saldo

    if movimientos:
        fechas = [m.fecha for m in movimientos]
        if extracto.periodo_inicio is None:
            extracto.periodo_inicio = min(fechas)
        if extracto.periodo_fin is None:
            extracto.periodo_fin = max(fechas)

    _agregar_advertencias(extracto, sin_clasificar, ajustados)
    return extracto


def _agregar_advertencias(
    extracto: Extracto, sin_clasificar: int, ajustados: int
) -> None:
    total = len(extracto.movimientos)
    if total == 0:
        extracto.advertencias.append(
            "No se reconoció ningún movimiento. Revisa con el comando "
            "'diagnostico' qué texto se está leyendo."
        )
        return

    if sin_clasificar:
        extracto.advertencias.append(
            f"{sin_clasificar} de {total} movimientos quedaron sin poder "
            "clasificar como ingreso o egreso; se asumieron como ingreso."
        )

    por_palabras = sum(
        1 for m in extracto.movimientos if m.confianza == Confianza.PALABRAS
    )
    if por_palabras:
        extracto.advertencias.append(
            f"{por_palabras} de {total} movimientos se clasificaron por su "
            "descripción (revisión recomendada)."
        )

    # La marca del banco (el CRE/DEB de AV Villas) es confiable, pero si además
    # no hay forma de verificar el cuadre conviene decirlo.
    por_marca = sum(1 for m in extracto.movimientos if m.confianza == Confianza.MARCA)
    if por_marca and extracto.diferencia_cuadre is None:
        extracto.advertencias.append(
            f"{por_marca} de {total} movimientos se clasificaron por la marca del "
            "banco en la descripción y no hay saldos para verificar el cuadre."
        )

    diferencia = extracto.diferencia_cuadre
    if diferencia is not None and abs(diferencia) > TOLERANCIA:
        extracto.advertencias.append(
            f"El extracto no cuadra por {diferencia}: saldo inicial + movimientos "
            "no da el saldo final. Puede faltar algún movimiento por leer."
        )

    if extracto.total_ingresos_reportado is not None:
        calculado = sum((m.ingreso for m in extracto.movimientos), Decimal("0.00"))
        if abs(calculado - extracto.total_ingresos_reportado) > TOLERANCIA:
            extracto.advertencias.append(
                f"Los ingresos leídos ({calculado}) no coinciden con el total que "
                f"reporta el extracto ({extracto.total_ingresos_reportado})."
            )
    if extracto.total_egresos_reportado is not None:
        calculado = sum((m.egreso for m in extracto.movimientos), Decimal("0.00"))
        if abs(calculado - extracto.total_egresos_reportado) > TOLERANCIA:
            extracto.advertencias.append(
                f"Los egresos leídos ({calculado}) no coinciden con el total que "
                f"reporta el extracto ({extracto.total_egresos_reportado})."
            )


def analizar_archivo(
    ruta: str,
    *,
    banco: str | None = None,
    anio_defecto: int | None = None,
    dpi: int = 300,
    idioma: str = "spa",
    forzar_ocr: bool = False,
) -> Extracto:
    """Lee un archivo (PDF/JPG/PNG/TXT) y devuelve su Extracto."""
    from .extraccion import extraer_texto
    from .perfiles import perfil_por_nombre

    perfil = perfil_por_nombre(banco) if banco else None
    if banco and perfil is None:
        raise ValueError(
            f"Banco no reconocido: '{banco}'. "
            "Usa: bancolombia, nequi, avvillas, bogota o generico."
        )

    extraido = extraer_texto(ruta, dpi=dpi, idioma=idioma, forzar_ocr=forzar_ocr)
    extracto = analizar(
        paginas=extraido.paginas or [extraido.texto],
        perfil=perfil,
        archivo=ruta,
        anio_defecto=anio_defecto,
        motor_texto=extraido.motor,
    )
    extracto.advertencias = extraido.advertencias + extracto.advertencias
    return extracto

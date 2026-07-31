"""Normalización de texto, montos y fechas con convenciones colombianas.

Los extractos colombianos usan punto como separador de miles y coma como
separador decimal (1.234.567,89). Algunos PDF exportados usan el formato
anglosajón (1,234,567.89). Aquí se resuelven ambos casos.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

CENTAVOS = Decimal("0.01")

MESES: dict[str, int] = {
    "ENE": 1, "ENERO": 1, "JAN": 1, "JANUARY": 1,
    "FEB": 2, "FEBRERO": 2, "FEBRUARY": 2,
    "MAR": 3, "MARZO": 3, "MARCH": 3,
    "ABR": 4, "ABRIL": 4, "APR": 4, "APRIL": 4,
    "MAY": 5, "MAYO": 5,
    "JUN": 6, "JUNIO": 6, "JUNE": 6,
    "JUL": 7, "JULIO": 7, "JULY": 7,
    "AGO": 8, "AGOSTO": 8, "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SET": 9, "SEPT": 9, "SEPTIEMBRE": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTUBRE": 10, "OCTOBER": 10,
    "NOV": 11, "NOVIEMBRE": 11, "NOVEMBER": 11,
    "DIC": 12, "DICIEMBRE": 12, "DEC": 12, "DECEMBER": 12,
}

NOMBRE_MES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre",
    12: "Diciembre",
}


def sin_acentos(texto: str) -> str:
    """Quita tildes y diacríticos, útil porque el OCR es inconsistente."""
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def clave(texto: str) -> str:
    """Versión canónica de un texto para comparaciones: sin tildes, mayúsculas."""
    return re.sub(r"\s+", " ", sin_acentos(texto or "")).strip().upper()


def clave_posicional(texto: str) -> str:
    """Como `clave` pero conservando cada espacio, y por tanto las posiciones.

    Se usa para leer el encabezado de la tabla: ahí lo que importa es en qué
    columna está cada título, así que no se puede colapsar el espaciado.
    """
    return sin_acentos(texto or "").upper()


def limpiar_linea(texto: str) -> str:
    """Colapsa espacios raros (incluye los que mete el OCR) sin perder el orden."""
    texto = texto.replace("\u00a0", " ").replace("\t", " ")
    return re.sub(r" {1,}", " ", texto).strip()


# --------------------------------------------------------------------------
# Montos
# --------------------------------------------------------------------------

# Un token monetario "confiable" trae separador de miles o dos decimales,
# o viene precedido por $ / COP. Así evitamos confundir números de documento
# o de referencia con valores.
_NUM_CON_MILES = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
_NUM_CON_DECIMAL = r"\d+(?:[.,]\d{2})"
_NUM_SIMPLE = r"\d+"

_ENVOLTURA = r"[-+(]{0,2}\s*(?:\$|COP|COL\$)?\s*"
_CIERRE = r"\s*(?:\)|-|\+)?\s*(?:CR|DB|CT|DT)?"

RE_MONTO_FUERTE = re.compile(
    rf"(?<![\w/\-]){_ENVOLTURA}(?:{_NUM_CON_MILES}|{_NUM_CON_DECIMAL}){_CIERRE}",
    re.IGNORECASE,
)
RE_MONTO_CON_SIMBOLO = re.compile(
    rf"(?<![\w/\-])[-+(]{{0,2}}\s*(?:\$|COP|COL\$)\s*{_NUM_SIMPLE}{_CIERRE}",
    re.IGNORECASE,
)
RE_MONTO_DEBIL = re.compile(
    rf"(?<![\w/\-.,]){_ENVOLTURA}{_NUM_SIMPLE}{_CIERRE}(?![\w.,])",
    re.IGNORECASE,
)


def parse_monto(texto: str | None) -> Decimal | None:
    """Convierte un texto a Decimal. Devuelve None si no es un monto.

    Reconoce: 1.234.567,89 · 1,234,567.89 · $ 50.000 · 1.234,56- · (1.234,56)
    · 1.234,56 CR · -1.234
    El signo negativo se detecta por: guion adelante o atrás, paréntesis,
    o los sufijos DB/DT (débito).
    """
    if texto is None:
        return None
    original = str(texto).strip()
    if not original:
        return None

    negativo = False
    t = original

    if "(" in t and ")" in t:
        negativo = True

    # Sufijos de naturaleza contable usados por algunos bancos.
    canon = clave(t)
    if re.search(r"(?:\bDB\b|\bDT\b|\bDEBITO\b)$", canon):
        negativo = True
    if re.search(r"(?:\bCR\b|\bCT\b|\bCREDITO\b)$", canon):
        negativo = False if not negativo else negativo

    t = re.sub(r"(?i)\b(?:COP|COL\$|CR|DB|CT|DT|DEBITO|CREDITO)\b", "", t)
    t = t.replace("$", "").replace("(", "").replace(")", "")
    t = t.replace("\u00a0", " ").strip()

    if t.startswith("-") or t.endswith("-"):
        negativo = True
    t = t.strip("+- ").strip()
    t = re.sub(r"[\s']", "", t)

    if not t or not re.fullmatch(r"[\d.,]+", t):
        return None
    if not re.search(r"\d", t):
        return None

    # ¿Cuál separador es el decimal?
    decimal_sep: str | None = None
    if "." in t and "," in t:
        decimal_sep = "," if t.rfind(",") > t.rfind(".") else "."
    elif "," in t:
        ultimo = t.rsplit(",", 1)[1]
        if t.count(",") == 1 and len(ultimo) in (1, 2):
            decimal_sep = ","
    elif "." in t:
        ultimo = t.rsplit(".", 1)[1]
        if t.count(".") == 1 and len(ultimo) in (1, 2):
            decimal_sep = "."

    if decimal_sep:
        corte = t.rfind(decimal_sep)
        entero = re.sub(r"[.,]", "", t[:corte]) or "0"
        fraccion = t[corte + 1:]
        numero = f"{entero}.{fraccion}"
    else:
        numero = re.sub(r"[.,]", "", t)

    try:
        valor = Decimal(numero)
    except InvalidOperation:
        return None

    valor = valor.quantize(CENTAVOS)
    return -valor if negativo else valor


def es_negativo_explicito(texto: str) -> bool:
    """True si el texto del monto trae marca explícita de egreso."""
    t = clave(texto)
    if "(" in texto and ")" in texto:
        return True
    if t.endswith("DB") or t.endswith("DT"):
        return True
    limpio = re.sub(r"(?i)\b(?:COP|COL\$|CR|DB|CT|DT)\b", "", texto).strip()
    limpio = limpio.replace("$", "").strip()
    return limpio.startswith("-") or limpio.endswith("-")


def formato_cop(valor: Decimal | int | float | None, *, decimales: int = 2) -> str:
    """Formatea un valor en pesos colombianos: -1.234.567,89"""
    if valor is None:
        return ""
    if not isinstance(valor, Decimal):
        valor = Decimal(str(valor))
    signo = "-" if valor < 0 else ""
    entero = f"{abs(valor):,.{decimales}f}"
    # De formato anglosajón a colombiano.
    entero = entero.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{signo}{entero}"


# --------------------------------------------------------------------------
# Fechas
# --------------------------------------------------------------------------

_RE_ISO = re.compile(r"(?<!\d)(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})(?!\d)")
_RE_DMY = re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})(?!\d)")
_RE_D_MES_Y = re.compile(
    r"(?<!\w)(\d{1,2})\s*(?:de\s+|[/\-\s])\s*([A-Za-zÁ-Úá-ú]{3,10})\.?"
    r"(?:\s*(?:de\s+|[/\-\s])\s*(\d{2,4}))?(?!\w)"
)
_RE_MES_D_Y = re.compile(
    r"(?<!\w)([A-Za-zÁ-Úá-ú]{3,10})\.?\s*[/\-\s]\s*(\d{1,2})"
    r"(?:\s*[/\-\s,]\s*(\d{2,4}))?(?!\w)"
)
_RE_DM = re.compile(r"(?<![\d/])(\d{1,2})[/\-](\d{1,2})(?![\d/])")


def _completar_anio(anio: int | None, anio_defecto: int | None) -> int | None:
    if anio is None:
        return anio_defecto
    if anio < 100:
        return 2000 + anio if anio < 80 else 1900 + anio
    return anio


def _armar(dia: int, mes: int, anio: int | None) -> date | None:
    if anio is None:
        return None
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def buscar_fecha(
    texto: str, anio_defecto: int | None = None, *, mes_defecto: int | None = None
) -> tuple[date, tuple[int, int]] | None:
    """Busca la primera fecha del texto.

    Devuelve (fecha, (inicio, fin)) para poder recortarla de la línea antes de
    buscar montos. `anio_defecto` se usa cuando el extracto omite el año
    (Bancolombia y Nequi lo hacen con frecuencia).
    """
    if not texto:
        return None

    m = _RE_ISO.search(texto)
    if m:
        f = _armar(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if f:
            return f, m.span()

    m = _RE_DMY.search(texto)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        anio = _completar_anio(int(m.group(3)), anio_defecto)
        f = _armar(a, b, anio)
        if f is None and a <= 12:  # venía como mm/dd/aaaa
            f = _armar(b, a, anio)
        if f:
            return f, m.span()

    for regex, orden in ((_RE_D_MES_Y, "dm"), (_RE_MES_D_Y, "md")):
        for m in regex.finditer(texto):
            if orden == "dm":
                bruto_dia, bruto_mes, bruto_anio = m.group(1), m.group(2), m.group(3)
            else:
                bruto_mes, bruto_dia, bruto_anio = m.group(1), m.group(2), m.group(3)
            mes = MESES.get(clave(bruto_mes))
            if not mes:
                continue
            anio = _completar_anio(int(bruto_anio) if bruto_anio else None, anio_defecto)
            f = _armar(int(bruto_dia), mes, anio)
            if f:
                return f, m.span()

    m = _RE_DM.search(texto)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if b <= 12:
            f = _armar(a, b, anio_defecto)
            if f:
                return f, m.span()
        if a <= 12:  # posible mm/dd
            f = _armar(b, a, anio_defecto)
            if f:
                return f, m.span()

    return None


def parse_fecha(texto: str, anio_defecto: int | None = None) -> date | None:
    """Como buscar_fecha pero devuelve solo la fecha."""
    hallazgo = buscar_fecha(texto, anio_defecto)
    return hallazgo[0] if hallazgo else None


def etiqueta_periodo(f: date) -> str:
    """Clave de agrupación mensual: 2025-03"""
    return f"{f.year:04d}-{f.month:02d}"


def periodo_legible(etiqueta: str) -> str:
    """2025-03 -> Marzo 2025"""
    try:
        anio, mes = etiqueta.split("-")
        return f"{NOMBRE_MES[int(mes)]} {anio}"
    except (ValueError, KeyError):
        return etiqueta

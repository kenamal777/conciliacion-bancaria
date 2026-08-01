"""Clasificación de cada movimiento por concepto y por tercero.

El resumen mensual dice cuánto entró y cuánto salió. Esto responde el resto:
en qué se fue la plata (IVA, comisiones, GMF, nómina, proveedores) y con quién
se movió (quién consignó, a quién se le pagó).

Dos advertencias sobre la naturaleza del problema:

1. El banco no informa el concepto ni el tercero en campos separados: hay que
   deducirlos del texto de la descripción. Es una heurística, no un dato.
2. El mismo tercero aparece escrito distinto según el canal ("SERVIEQUIPOS I",
   "SERVIEQUIPOS IND SAS"). Unificarlos de más es peor que de menos, porque
   junta plata de dos terceros distintos. Por eso la unificación aquí es
   conservadora y siempre deja ver qué variantes agrupó.

Cuando la deducción no acierte, el usuario manda: un archivo `terceros.csv`
con sus propias reglas tiene prioridad sobre todo lo de este módulo.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal

from .modelos import Movimiento
from .normalizacion import CENTAVOS, clave

# Sirve tanto para cobros como para abonos del banco (intereses ganados), así
# que la etiqueta no debe decir "cargo".
SIN_TERCERO = "(el propio banco)"
TERCERO_DESCONOCIDO = "(sin identificar)"


# ---------------------------------------------------------------------------
# Taxonomía de conceptos
# ---------------------------------------------------------------------------

# El orden es lo que importa: se evalúa de arriba abajo y gana el primero que
# coincida. Lo específico va antes que lo general (IVA antes que impuestos,
# GMF antes que impuestos, nómina antes que transferencias).
CONCEPTOS_EGRESO: tuple[tuple[str, str], ...] = (
    ("IVA", r"\bI\.?\s?V\.?\s?A\b|IMPUESTO\s+AL\s+VALOR"),
    ("GMF (4x1000)", r"\bGMF\b|4\s?X\s?1000|GRAVAMEN\s+(?:AL\s+)?MOVIMIENTO|"
                     r"IMPUESTO\s+FINANCIERO|IMPTO\s+GOBIERNO"),
    ("Retención en la fuente", r"RETENCION|RETEFUENTE|RTE\s?FTE"),
    ("Comisiones y cuotas de manejo",
     r"COMISION|CUOTA\s+(?:DE\s+)?MANEJO|PORTES|TALONARIO|CHEQUERA|"
     r"CUOTA\s+ADMIN|ADMINISTRACION\s+CUENTA|CONSULTA\s+SALDO"),
    ("Nómina", r"N[OÓ]MINA|NOMINA|PAGO\s+DE\s+EMPLEADOS|LIQUIDACION\s+LABORAL|"
               r"SEGURIDAD\s+SOCIAL|APORTES\s+PARAFISCALES|PILA\b"),
    ("Proveedores", r"PROVEEDOR|PAGO\s+DE\s+PROV|PAGO\s+A\s+PROV|"
                    r"MATERIAS?\s+PRIMAS?"),
    ("Servicios públicos y telecomunicaciones",
     r"SERVICIOS?\s+P[UÚ]BLICOS?|\bEPM\b|CODENSA|ENEL|EMCALI|AFINIA|AIR-?E|"
     r"ACUEDUCTO|ALCANTARILLADO|\bGAS\b|VANTI|ENERG[IÍ]A|CLARO|MOVISTAR|"
     r"\bTIGO\b|\bETB\b|WOM\b|INTERNET"),
    ("Impuestos y entidades del Estado",
     r"IMPUESTO|IMPTO|\bDIAN\b|PREDIAL|\bICA\b|INDUSTRIA\s+Y\s+COMERCIO|"
     r"VEHICULO|DISTRITAL|MUNICIPAL|TESORER[IÍ]A|SECRETAR[IÍ]A\s+DE\s+HACIENDA"),
    ("Seguros", r"SEGURO|P[OÓ]LIZA|POLIZA|ASEGURADORA|SURA\b|BOLIVAR\b"),
    ("Créditos y obligaciones financieras",
     r"ABONO\s+(?:A\s+)?CR[EÉ]DITO|CUOTA\s+CR[EÉ]DITO|PAGO\s+CR[EÉ]DITO|"
     r"LIBRANZA|LEASING|SOBREGIRO|INTERES\s+MORA|TARJETA\s+DE\s+CR[EÉ]DITO"),
    ("Cargos y ajustes del banco",
     r"CARGO\s+(?:APLICADO|OMISION|POR|DE)|AJUSTE|NOTA\s+D[EÉ]BITO|"
     r"OMISION\s+CONSIGNACION"),
    ("Cheques pagados", r"\bCHEQUE\b"),
    ("Retiros y efectivo",
     r"RETIRO|CAJERO|AVANCE|SACASTE|CORRESPONSAL|EFECTIVO"),
    ("Compras y pagos con tarjeta",
     r"COMPRA|PAGASTE|PAGO\s+PSE|\bPSE\b|DATAFONO|POS\b"),
    ("Transferencias enviadas",
     r"TRANSFERENCIA|TRANSF|\bACH\b|ENV[IÍ]O|ENVIASTE|\bPARA\b|TRASLADO|"
     r"DISPERSION|ABONO\s+A\s+CUENTA"),
    ("Otros pagos", r"\bPAGO\b|\bPAGOS\b|CARGO|NOTA\s+D[EÉ]BITO|D[EÉ]BITO"),
)

CONCEPTOS_INGRESO: tuple[tuple[str, str], ...] = (
    ("Intereses y rendimientos",
     r"INTERES|INTERESES\s+GANADOS|RENDIMIENTO|ABONO\s+INTERESES"),
    ("Devoluciones y reversiones",
     r"DEVOLUCION|REVERSION|REVERSO|ANULACION|REINTEGRO"),
    ("Notas crédito del banco", r"NOTA\s+CR[EÉ]DITO"),
    ("Recargas", r"RECARGA|METES\s+PLATA|METISTE"),
    ("Consignaciones y recaudos",
     r"CONSIGNACION|CONSIG|RECAUDO|DEP[OÓ]SITO|EFECTIVO|CHEQUE\s+LOCAL|"
     r"CHEQUE\s+NACIONAL"),
    ("Transferencias recibidas",
     r"TRANSFERENCIA|TRANSF|\bACH\b|RECIBISTE|TE\s+ENVIARON|\bDE\b|ABONO|"
     r"TRASLADO|DISPERSION"),
    ("Ventas y otros ingresos", r"VENTA|DESEMBOLSO|SUBSIDIO|PRESTAMO"),
    # Un movimiento de entrada que dice "pago" es plata que alguien nos pagó.
    ("Pagos recibidos", r"\bPAGO\b|\bPAGOS\b|PROVEEDOR"),
)

CONCEPTO_OTROS_EGRESO = "Otros egresos"
CONCEPTO_OTROS_INGRESO = "Otros ingresos"

# Conceptos que son cobros del propio banco: no tienen un tercero externo, y
# ponerles uno sería inventar información.
CONCEPTOS_DEL_BANCO: frozenset[str] = frozenset(
    {
        "IVA",
        "GMF (4x1000)",
        "Retención en la fuente",
        "Comisiones y cuotas de manejo",
        "Intereses y rendimientos",
        "Notas crédito del banco",
        "Cargos y ajustes del banco",
    }
)

# Entidades cuyo nombre hay que reconocer completo, porque contiene palabras
# que en otro contexto serían ruido: "Banco de Bogotá" lleva una ciudad dentro.
ENTIDADES_CONOCIDAS: tuple[str, ...] = (
    "BANCOLOMBIA", "BANCO DE BOGOTA", "BANCO DE OCCIDENTE", "BANCO POPULAR",
    "BANCO AGRARIO", "BANCO CAJA SOCIAL", "BANCO FALABELLA", "BANCO PICHINCHA",
    "BANCO SERFINANZA", "BANCO UNION", "BANCO W", "BANCO GNB SUDAMERIS",
    "GNB SUDAMERIS", "AV VILLAS", "DAVIVIENDA", "DAVIPLATA", "BBVA",
    "SCOTIABANK COLPATRIA", "COLPATRIA", "ITAU", "BANCOOMEVA", "COOMEVA",
    "BANCAMIA", "FINANDINA", "MIBANCO", "CONFIAR", "JURISCOOP", "COOPCENTRAL",
    "LULO BANK", "LULO", "NEQUI", "MOVII", "RAPPIPAY", "IRIS", "UALA",
    "NU COLOMBIA", "BOLD", "SISTECREDITO", "ADDI", "EFECTY", "SUPERGIROS",
    "BALOTO", "GANA", "SU RED", "REDESERVI", "MOVILRED",
)


# ---------------------------------------------------------------------------
# Limpieza para extraer el tercero
# ---------------------------------------------------------------------------

# Palabras que describen la operación, no al tercero.
RUIDO_OPERACION: tuple[str, ...] = (
    "CRE", "DEB", "NOTA DEBITO", "NOTA CREDITO", "TRANSF", "TRANSFERENCIA",
    "INTERNET", "VIRTUAL", "SUCURSAL", "CANAL", "CORRESPONSAL", "NACIONAL",
    "LOCAL", "ACH", "PSE", "OI", "PAGO", "PAGOS", "PAGO DE", "ABONO", "CARGO",
    "CONSIGNACION", "CONSIG", "REFEREN", "REFERENCIA", "EFECTIVO", "CHEQUE",
    "RECAUDO", "DEPOSITO", "RETIRO", "ENVIO", "ENVIASTE", "RECIBISTE",
    "PAGASTE", "SACASTE", "PARA", "PROV", "PROVEEDOR", "PROVEEDORES", "NOMINA",
    "SERVICIOS", "PUBLICOS", "IMPUESTO", "IMPUESTOS", "COMISION", "CUOTA",
    "MANEJO", "GRAVAMEN", "MOVIMIENTO", "FINANCIERO", "OMISION", "APLICADO",
    "POR", "CUENTA", "ADMINISTRACION", "GANADOS", "MOVIMIENTOS",
    "PAGADO", "DISTRITALES", "CTA", "SUC", "PRIMERA", "SEGUNDA", "QUINCENA",
    "MES", "AUTOMATICO", "DIARIO", "TRANSACCION", "DESCRIPCION",
)

# Palabras que los extractos cortan a la mitad por el ancho de la columna.
RUIDO_TRUNCADO: tuple[str, ...] = (
    r"CORRESPONSA\w*", r"REFEREN\w*", r"NACIONA\w*", r"VIRTUA\w*",
    r"TRANSFEREN\w*", r"CONSIGNACIO\w*", r"ADMINISTRA\w*", r"EMPRES\w*",
)

# Ciudades y nombres de oficina que algunos extractos dejan pegados en la
# descripción porque son columnas propias del reporte.
RUIDO_UBICACION: tuple[str, ...] = (
    "BOGOTA", "MEDELLIN", "CALI", "BARRANQUILLA", "CARTAGENA", "BUCARAMANGA",
    "PEREIRA", "MANIZALES", "ARMENIA", "IBAGUE", "NEIVA", "VILLAVICENCIO",
    "CUCUTA", "SANTA MARTA", "MONTERIA", "POPAYAN", "PASTO", "TUNJA",
    "SINCELEJO", "VALLEDUPAR", "RIOHACHA", "QUIBDO", "FLORENCIA", "YOPAL",
    "CHIA", "ENVIGADO", "ITAGUI", "BELLO", "SOACHA", "PRINCIPAL", "OFICINA",
    "GCIA", "BCA", "PYME", "BANCA", "EMPRESARIAL", "PERSONAS", "GERENCIA",
)

# Sufijos societarios: se quitan solo para comparar, no para mostrar.
SUFIJOS_SOCIETARIOS: tuple[str, ...] = (
    "SAS", "S A S", "SA", "S A", "LTDA", "LTD", "SCA", "SAC", "EU", "ESP",
    "E S P", "SEM", "CIA", "Y CIA", "AND CIA", "INC", "CORP", "SOCIEDAD",
    "ANONIMA", "SIMPLIFICADA", "LIMITADA",
)

# Preposiciones y artículos que sobran al inicio del nombre.
CONECTORES_INICIALES: tuple[str, ...] = (
    "DE", "DEL", "A", "AL", "EN", "PARA", "POR", "CON", "LA", "EL", "LOS",
    "LAS", "UN", "UNA", "Y", "SR", "SRA", "SEÑOR", "SENOR",
)


def _quitar_palabras(texto: str, palabras: tuple[str, ...]) -> str:
    """Elimina palabras completas de un texto ya canonizado."""
    resultado = texto
    for palabra in sorted(palabras, key=len, reverse=True):
        resultado = re.sub(rf"(?<!\w){re.escape(palabra)}(?!\w)", " ", resultado)
    return re.sub(r"\s+", " ", resultado).strip()


def extraer_tercero(descripcion: str, concepto: str = "") -> str:
    """Deduce con quién fue el movimiento a partir de la descripción.

    Devuelve `SIN_TERCERO` para los cobros del propio banco y
    `TERCERO_DESCONOCIDO` cuando después de limpiar no queda un nombre.
    """
    if concepto in CONCEPTOS_DEL_BANCO:
        return SIN_TERCERO

    texto = clave(descripcion)
    if not texto:
        return TERCERO_DESCONOCIDO

    # Las entidades conocidas se resuelven primero: su nombre propio contiene
    # palabras que en cualquier otro contexto habría que descartar.
    for entidad in sorted(ENTIDADES_CONOCIDAS, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(entidad)}(?!\w)", texto):
            return entidad

    # Fuera códigos, referencias y números largos.
    texto = re.sub(r"\b\d[\d\-\.]{2,}\b", " ", texto)
    texto = re.sub(r"\b\d+\b", " ", texto)
    texto = _quitar_palabras(texto, RUIDO_OPERACION)
    for patron in RUIDO_TRUNCADO:
        texto = re.sub(rf"(?<!\w){patron}", " ", texto)
    texto = _quitar_palabras(texto, RUIDO_UBICACION)
    texto = re.sub(r"[^\w\s&\.]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    # Fuera conectores sueltos y signos que quedaron pegados a los bordes.
    partes = [p.strip(".-_&") for p in texto.split()]
    partes = [p for p in partes if p]
    while partes and partes[0] in CONECTORES_INICIALES:
        partes.pop(0)
    while partes and partes[-1] in CONECTORES_INICIALES:
        partes.pop()

    # Una letra sola no identifica a nadie.
    partes = [p for p in partes if len(p) > 1]
    if not partes:
        return TERCERO_DESCONOCIDO

    nombre = " ".join(partes)
    if len(nombre) < 3:
        return TERCERO_DESCONOCIDO
    return nombre


def clave_tercero(nombre: str) -> str:
    """Forma canónica de un nombre, para reconocerlo escrito de otra manera."""
    texto = clave(nombre)
    texto = _quitar_palabras(texto, SUFIJOS_SOCIETARIOS)
    texto = re.sub(r"[^\w\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


# ---------------------------------------------------------------------------
# Reglas propias del usuario
# ---------------------------------------------------------------------------

@dataclass
class ReglaUsuario:
    patron: str
    tercero: str | None
    concepto: str | None
    compilado: re.Pattern[str] = field(repr=False, default=None)  # type: ignore


def cargar_reglas(ruta: str) -> tuple[list[ReglaUsuario], list[str]]:
    """Lee `terceros.csv`: patron;tercero;concepto

    Sirve para corregir a mano lo que la deducción automática no acierte. El
    patrón se busca dentro de la descripción, sin distinguir tildes ni
    mayúsculas. También acepta expresiones regulares.
    """
    if not ruta or not os.path.isfile(ruta):
        return [], []

    reglas: list[ReglaUsuario] = []
    avisos: list[str] = []

    with open(ruta, "r", encoding="utf-8-sig", errors="replace") as archivo:
        muestra = archivo.read(4096)
        archivo.seek(0)
        delimitador = ";" if muestra.count(";") >= muestra.count(",") else ","
        for numero, fila in enumerate(csv.reader(archivo, delimiter=delimitador), 1):
            if not fila or not fila[0].strip() or fila[0].lstrip().startswith("#"):
                continue
            patron = fila[0].strip()
            if clave(patron) in ("PATRON", "PATRÓN", "TEXTO", "BUSCAR"):
                continue  # encabezado
            tercero = fila[1].strip() if len(fila) > 1 and fila[1].strip() else None
            concepto = fila[2].strip() if len(fila) > 2 and fila[2].strip() else None
            try:
                compilado = re.compile(clave(patron))
            except re.error as error:
                avisos.append(f"Regla inválida en la línea {numero} ({patron}): {error}")
                continue
            reglas.append(
                ReglaUsuario(
                    patron=patron,
                    tercero=tercero,
                    concepto=concepto,
                    compilado=compilado,
                )
            )

    if reglas:
        avisos.insert(0, f"Se aplicaron {len(reglas)} reglas propias de {os.path.basename(ruta)}.")
    return reglas, avisos


def _aplicar_reglas(
    descripcion: str, reglas: list[ReglaUsuario]
) -> tuple[str | None, str | None]:
    canon = clave(descripcion)
    for regla in reglas:
        if regla.compilado.search(canon):
            return regla.tercero, regla.concepto
    return None, None


# ---------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------

def clasificar_concepto(movimiento: Movimiento) -> str:
    """Devuelve el concepto contable del movimiento."""
    canon = clave(movimiento.descripcion)
    tabla = CONCEPTOS_INGRESO if movimiento.valor > 0 else CONCEPTOS_EGRESO
    for nombre, patron in tabla:
        if re.search(patron, canon):
            return nombre
    return CONCEPTO_OTROS_INGRESO if movimiento.valor > 0 else CONCEPTO_OTROS_EGRESO


def clasificar(
    movimientos: list[Movimiento], reglas: list[ReglaUsuario] | None = None
) -> None:
    """Rellena `concepto` y `tercero` en cada movimiento, en el sitio."""
    reglas = reglas or []
    for movimiento in movimientos:
        tercero_regla, concepto_regla = _aplicar_reglas(movimiento.descripcion, reglas)
        concepto = concepto_regla or clasificar_concepto(movimiento)
        movimiento.concepto = concepto
        movimiento.tercero = tercero_regla or extraer_tercero(
            movimiento.descripcion, concepto
        )
    _unificar_terceros(movimientos)


def _unificar_terceros(movimientos: list[Movimiento]) -> None:
    """Junta las variantes de escritura de un mismo tercero.

    Criterio conservador: dos nombres se unifican solo si las palabras de uno
    están todas contenidas en el otro (por ejemplo "SERVIEQUIPOS" dentro de
    "SERVIEQUIPOS INDUSTRIALES"). Se adopta como nombre bueno el más completo,
    que es el que más información tiene. Nombres de una sola palabra corta no
    se usan para absorber a otros, porque un apellido común uniría terceros
    distintos.
    """
    conteo: dict[str, int] = {}
    tokens_por_clave: dict[str, frozenset[str]] = {}
    nombre_por_clave: dict[str, str] = {}

    for movimiento in movimientos:
        nombre = movimiento.tercero or TERCERO_DESCONOCIDO
        if nombre in (SIN_TERCERO, TERCERO_DESCONOCIDO):
            continue
        llave = clave_tercero(nombre)
        if not llave:
            continue
        conteo[llave] = conteo.get(llave, 0) + 1
        tokens_por_clave.setdefault(llave, frozenset(llave.split()))
        # Se conserva la variante más larga como nombre para mostrar.
        if len(nombre) > len(nombre_por_clave.get(llave, "")):
            nombre_por_clave[llave] = nombre

    # Cada clave apunta a la clave "canónica" que la absorbe.
    destino: dict[str, str] = {llave: llave for llave in conteo}
    llaves = sorted(conteo, key=lambda k: (-len(tokens_por_clave[k]), k))

    for llave in llaves:
        tokens = tokens_por_clave[llave]
        if len(tokens) == 1 and len(llave) <= 4:
            continue  # una sigla corta no absorbe a nadie
        for otra in llaves:
            if otra == llave or destino[otra] != otra:
                continue
            otros_tokens = tokens_por_clave[otra]
            if otros_tokens == tokens:
                continue
            # `otra` se absorbe en `llave` si es un subconjunto de ella.
            if otros_tokens < tokens and (
                len(otros_tokens) >= 2 or len(otra) >= 6
            ):
                destino[otra] = llave

    for movimiento in movimientos:
        nombre = movimiento.tercero or TERCERO_DESCONOCIDO
        if nombre in (SIN_TERCERO, TERCERO_DESCONOCIDO):
            continue
        llave = clave_tercero(nombre)
        final = destino.get(llave, llave)
        movimiento.tercero = nombre_por_clave.get(final, nombre)


def variantes_por_tercero(movimientos: list[Movimiento]) -> dict[str, set[str]]:
    """Qué textos originales quedaron agrupados bajo cada tercero.

    Es lo que permite auditar la unificación: si agrupó de más, aquí se ve.
    """
    variantes: dict[str, set[str]] = {}
    for movimiento in movimientos:
        if not movimiento.tercero:
            continue
        crudo = extraer_tercero(movimiento.descripcion, movimiento.concepto or "")
        if crudo in (SIN_TERCERO, TERCERO_DESCONOCIDO):
            continue
        if clave_tercero(crudo) != clave_tercero(movimiento.tercero):
            variantes.setdefault(movimiento.tercero, set()).add(crudo)
    return variantes


# ---------------------------------------------------------------------------
# Agrupaciones para el informe
# ---------------------------------------------------------------------------

@dataclass
class FilaConcepto:
    concepto: str
    tipo: str  # INGRESO o EGRESO
    cantidad: int
    total: Decimal

    @property
    def promedio(self) -> Decimal:
        if not self.cantidad:
            return Decimal("0.00")
        return (self.total / self.cantidad).quantize(CENTAVOS)


@dataclass
class FilaTercero:
    tercero: str
    concepto: str
    tipo: str
    cantidad: int
    total: Decimal
    bancos: list[str] = field(default_factory=list)
    primera: str = ""
    ultima: str = ""


def por_concepto(movimientos: list[Movimiento]) -> list[FilaConcepto]:
    """Totales por concepto, ordenados de mayor a menor dentro de cada tipo."""
    acumulado: dict[tuple[str, str], list] = {}
    for movimiento in movimientos:
        tipo = "INGRESO" if movimiento.valor > 0 else "EGRESO"
        llave = (tipo, movimiento.concepto or "Sin clasificar")
        registro = acumulado.setdefault(llave, [0, Decimal("0.00")])
        registro[0] += 1
        registro[1] += abs(movimiento.valor)

    filas = [
        FilaConcepto(concepto=concepto, tipo=tipo, cantidad=datos[0], total=datos[1])
        for (tipo, concepto), datos in acumulado.items()
    ]
    filas.sort(key=lambda f: (f.tipo != "INGRESO", -f.total))
    return filas


def por_concepto_y_tercero(movimientos: list[Movimiento]) -> list[FilaTercero]:
    """Detalle de cada concepto abierto por tercero."""
    acumulado: dict[tuple[str, str, str], list] = {}
    for movimiento in movimientos:
        tipo = "INGRESO" if movimiento.valor > 0 else "EGRESO"
        llave = (
            tipo,
            movimiento.concepto or "Sin clasificar",
            movimiento.tercero or TERCERO_DESCONOCIDO,
        )
        registro = acumulado.setdefault(
            llave, [0, Decimal("0.00"), set(), movimiento.fecha, movimiento.fecha]
        )
        registro[0] += 1
        registro[1] += abs(movimiento.valor)
        registro[2].add(movimiento.banco)
        registro[3] = min(registro[3], movimiento.fecha)
        registro[4] = max(registro[4], movimiento.fecha)

    filas = [
        FilaTercero(
            tercero=tercero,
            concepto=concepto,
            tipo=tipo,
            cantidad=datos[0],
            total=datos[1],
            bancos=sorted(datos[2]),
            primera=datos[3].strftime("%d/%m/%Y"),
            ultima=datos[4].strftime("%d/%m/%Y"),
        )
        for (tipo, concepto, tercero), datos in acumulado.items()
    ]
    # Ingresos primero; dentro, el concepto de mayor monto; dentro, el tercero
    # de mayor monto.
    total_por_concepto: dict[tuple[str, str], Decimal] = {}
    for fila in filas:
        llave = (fila.tipo, fila.concepto)
        total_por_concepto[llave] = total_por_concepto.get(
            llave, Decimal("0.00")
        ) + fila.total
    filas.sort(
        key=lambda f: (
            f.tipo != "INGRESO",
            -total_por_concepto[(f.tipo, f.concepto)],
            f.concepto,
            -f.total,
        )
    )
    return filas


def por_tercero(movimientos: list[Movimiento]) -> list[FilaTercero]:
    """Consolidado por tercero, sumando todos sus conceptos."""
    acumulado: dict[str, list] = {}
    for movimiento in movimientos:
        nombre = movimiento.tercero or TERCERO_DESCONOCIDO
        registro = acumulado.setdefault(
            nombre,
            [0, Decimal("0.00"), Decimal("0.00"), set(), set(),
             movimiento.fecha, movimiento.fecha],
        )
        registro[0] += 1
        registro[1] += movimiento.ingreso
        registro[2] += movimiento.egreso
        registro[3].add(movimiento.banco)
        registro[4].add(movimiento.concepto or "Sin clasificar")
        registro[5] = min(registro[5], movimiento.fecha)
        registro[6] = max(registro[6], movimiento.fecha)

    filas: list[FilaTercero] = []
    for nombre, datos in acumulado.items():
        neto = datos[1] - datos[2]
        filas.append(
            FilaTercero(
                tercero=nombre,
                concepto=", ".join(sorted(datos[4])),
                tipo="INGRESO" if neto > 0 else "EGRESO",
                cantidad=datos[0],
                total=abs(neto).quantize(CENTAVOS),
                bancos=sorted(datos[3]),
                primera=datos[5].strftime("%d/%m/%Y"),
                ultima=datos[6].strftime("%d/%m/%Y"),
            )
        )
    filas.sort(key=lambda f: -f.total)
    return filas


PLANTILLA_REGLAS = """\
# Reglas propias para clasificar movimientos.
#
# Formato:  patron;tercero;concepto
#
#   patron   texto que aparece en la descripcion del extracto (sin importar
#            tildes ni mayusculas). Tambien acepta expresiones regulares.
#   tercero  nombre unificado con el que quiere ver ese tercero. Opcional.
#   concepto en que grupo quiere contarlo. Opcional.
#
# La primera regla que coincida es la que se aplica, asi que ponga las mas
# especificas arriba. Las lineas que empiezan por # se ignoran.
#
# Ejemplos (borre el # para activarlos):
#
# SERVIEQUIPOS;SERVIEQUIPOS INDUSTRIALES SAS;Proveedores
# AGUAS E INGENIE;AGUAS E INGENIERIA SAS;Proveedores
# CLIENTE ABC;COMERCIAL ABC LTDA;Consignaciones y recaudos
# SONIA BLANCO;SONIA BLANCO RAMIREZ;Nomina
# NOVD AUTOM;PAGO NOMINA EMPLEADOS;Nomina
"""


def crear_plantilla_reglas(ruta: str) -> str:
    """Escribe un `terceros.csv` de ejemplo si todavía no existe."""
    if os.path.isfile(ruta):
        return ruta
    os.makedirs(os.path.dirname(os.path.abspath(ruta)) or ".", exist_ok=True)
    with open(ruta, "w", encoding="utf-8-sig") as archivo:
        archivo.write(PLANTILLA_REGLAS)
    return ruta

"""Perfiles de los bancos soportados.

Todo lo específico de cada banco vive aquí, en forma declarativa, para poder
ajustarlo sin tocar el motor de parseo. Si un extracto real no se lee bien,
lo que hay que corregir son las expresiones de este archivo.

Los perfiles se afinaron contra extractos reales de:
  · Bancolombia   - Estado de cuenta corriente (2024)
  · Nequi         - Estado de cuenta (2021)
  · AV Villas     - Estado de cuenta Rentavillas (2023)
  · Banco Bogotá  - Extracto cuenta corriente (2020)

Las comparaciones se hacen sin tildes y en mayúsculas, porque ni el OCR ni los
PDF son consistentes con los acentos.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Clasificación por descripción (último recurso cuando no hay saldo ni signo)
# ---------------------------------------------------------------------------

# El orden importa: se evalúa egreso primero porque expresiones como
# "PAGO NOMINA" son salidas de plata para la empresa que concilia.
PALABRAS_EGRESO: tuple[str, ...] = (
    "RETIRO", "RETIROS", "COMPRA", "COMPRAS", "PAGO", "PAGOS", "PAGASTE",
    "TRANSFERENCIA A", "TRANSF A", "TRANSFERENCIA ENVIADA", "ENVIO", "ENVIASTE",
    "ENVIO DE DINERO", "SACASTE", "SALIDA", "CARGO", "CARGOS", "DEBITO",
    "DEBITO AUTOMATICO", "CUOTA", "CUOTA MANEJO", "COMISION", "COMISIONES",
    "IVA", "GMF", "4X1000", "4 X 1000", "IMPUESTO", "IMPTO", "IMP GOB",
    "GRAVAMEN", "GRAVAMEN AL MOVIMIENTO FINANCIERO", "IMPUESTO FINANCIERO",
    "CHEQUE", "AVANCE", "SOBREGIRO", "INTERES MORA", "INTERESES MORA", "SEGURO",
    "TRASLADO A", "NOTA DEBITO", "COBRO", "RECAUDO PAGO", "PSE PAGO",
    "COMPRA CON TARJETA", "PAGO PSE", "PAGO SERVICIOS", "TARJETA DE CREDITO",
    "ADMINISTRACION", "CUOTA DE MANEJO", "PORTES", "TALONARIO", "CONSULTA",
    "TRANSFERENCIA SALIENTE", "DISPERSION", "RETENCION", "RETEFUENTE",
    "OMISION", "PARA ",
)

PALABRAS_INGRESO: tuple[str, ...] = (
    "CONSIGNACION", "CONSIGNACIONES", "ABONO", "ABONOS", "DEPOSITO",
    "RECIBISTE", "RECIBIDO", "RECIBIDA", "TRANSFERENCIA RECIBIDA",
    "TRANSFERENCIA DE", "TRANSF DE", "ENTRADA", "INGRESO", "NOTA CREDITO",
    "CREDITO", "INTERESES", "INTERES", "INTERESES GANADOS", "RENDIMIENTOS",
    "DEVOLUCION", "REINTEGRO", "REVERSION", "REVERSO", "TRASLADO DE",
    "RECARGA", "METES PLATA", "METISTE", "TE ENVIO", "TE ENVIARON",
    "PAGO RECIBIDO", "VENTA", "DESEMBOLSO", "NOMINA RECIBIDA", "SUBSIDIO",
    "ANULACION",
)

# Términos que resuelven ambigüedades: se revisan antes que las listas
# anteriores porque son frases completas y no fragmentos.
REGLAS_PRIORITARIAS: tuple[tuple[str, str], ...] = (
    ("ABONO INTERESES", "INGRESO"),
    ("INTERESES GANADOS", "INGRESO"),
    ("PAGO RECIBIDO", "INGRESO"),
    ("PAGO DE NOMINA RECIBIDO", "INGRESO"),
    ("RECIBISTE DE", "INGRESO"),
    ("TE ENVIARON", "INGRESO"),
    ("REVERSION PAGO", "INGRESO"),
    ("DEVOLUCION PAGO", "INGRESO"),
    ("ANULACION COMPRA", "INGRESO"),
    ("PAGO NOMINA", "EGRESO"),
    ("PAGO PROVEEDOR", "EGRESO"),
    ("PAGO DE PROV", "EGRESO"),
    ("ENVIO DE DINERO", "EGRESO"),
    ("RETIRO CAJERO", "EGRESO"),
    ("IMPUESTO GOBIERNO", "EGRESO"),
    ("GRAVAMEN AL MOVIMIENTO", "EGRESO"),
    ("OMISION CONSIGNACION", "EGRESO"),
)

# Líneas que nunca son movimientos, comunes a todos los bancos.
IGNORAR_COMUNES: tuple[str, ...] = (
    r"^\s*$",
    r"^P[AÁ]GINA\b", r"\bP[AÁ]G(?:INA)?\.?\s*\d+\s*(?:DE|/)\s*\d+", r"^P[AÁ]G\.",
    r"^HOJA\s+\d+",
    r"\bSALDO\s+(?:ANTERIOR|INICIAL|ACTUAL|FINAL|PROMEDIO|DISPONIBLE|TOTAL)\b",
    r"^SALDO\b", r"\bSALDO\s+AL\s+CORTE\b", r"^NUEVO\s+SALDO\b",
    r"^TOTAL(?:ES)?\b", r"\bTOTAL\s+(?:ABONOS|CARGOS|DEBITOS|CREDITOS|RETIROS)",
    r"^SUBTOTAL\b",
    r"^FECHA\b.*\b(?:DESCRIPCI|VALOR|D[EÉ]BITO|CR[EÉ]DITO|SALDO|CONCEPTO|"
    r"MOVIMIENTO|COD)",
    r"^(?:DESCRIPCI[OÓ]N|CONCEPTO|DETALLE)\b",
    # Cuadros de resumen de los extractos reales.
    r"\bMOVIMIENTO\s+(?:CR[EÉ]DITO|D[EÉ]BITO)\b",
    r"\bCUPO\s+(?:DE\s+)?SOBREGIRO\b", r"\bVALOR\s+INTERESES\b",
    r"^RETEFUENTE\b", r"^RETENCI[OÓ]N\b", r"^HONORARIOS\b",
    r"^GASTOS\s+JUDICIALES\b", r"\bVALOR\s+TOTAL\s+ADEUDADO\b",
    r"\bCUENTAS?\s+(?:POR\s+COBRAR|CONTINGENTES)\b",
    r"^PAQUETE\b", r"^TRANSACCIONAL\b", r"^OFICINA\b", r"\bBANCA\s+E",
    r"^TIPO\s+DE\s+CUENTA\b", r"^COD\.?\s*ORIGEN\b", r"^ESTADO\s+DE\s+CUENTA\b",
    r"^TOTALES\s+DEL\s+PER", r"^MOVIMIENTO\s+RESUMEN\b",
    r"^FECHA\s+EXTRACTO\b", r"^ENTREGA\b",
    # Ruido general de encabezados y pies de página.
    r"\(\+\d{1,3}\)\s*\d",  # teléfonos con indicativo: (+57) 300 600 0106
    r"^(?:CARRERA|CRA|CALLE|CL|AVENIDA|AVDA|AV|DIAGONAL|DG|TRANSVERSAL|TV|"
    r"AUTOPISTA|KR|CR)\.?\s*\d",  # direcciones
    r"\bwww\.", r"@", r"\bNIT\b", r"^L[IÍ]NEA\b", r"^TEL[EÉ]FONO",
    r"\bDEFENSOR\s+DEL\s+CONSUMIDOR\b", r"\bFOGAFIN\b",
    r"\bVIGILAD[OA]\s+POR\b", r"\bSUPERINTENDENCIA\b",
    r"\bSEGURO\s+DE\s+DEP[OÓ]SITOS?\b",
    r"^(?:SEÑOR|SENOR|CLIENTE|TITULAR|DIRECCI[OÓ]N|CIUDAD)\b",
    r"\bTASA\s+(?:DE\s+)?INTER[EÉ]S\b",
    r"^ESTE\b", r"^IMPORTANTE\b", r"^NOTA:", r"^SI\s+USTED\b",
    r"^N[UÚ]MERO\s+DE?\b", r"^N[UÚ]MERO\s*:?\s*\d", r"^CUENTA\b", r"^CTA\b",
    r"^PERIODO\b", r"^PER[IÍ]ODO\b", r"^DESDE\b", r"^EXTRACTO\b",
    r"\bRESUMEN\b", r"^MOVIMIENTOS\b", r"^FECHA\s+DE\s+(?:CORTE|EXPEDICI)",
    r"^CUOTA\s+DE\s+MANEJO\s+MES\s*$",
    r"\bCONTINUA\s+EN\s+LA\s+SIGUIENTE\b",
)

PATRONES_SALDO_INICIAL: tuple[str, ...] = (
    r"SALDO\s+(?:ANTERIOR|INICIAL|MES\s+ANTERIOR|AL\s+INICIO|INICIAL\s+DEL\s+PERIODO)",
    r"SALDO\s+ANT\b",
    r"SALDO\s+[UÚ]LTIMO\s+EXTRACTO",
)

PATRONES_SALDO_FINAL: tuple[str, ...] = (
    r"SALDO\s+(?:ACTUAL|FINAL|AL\s+CORTE|A\s+LA\s+FECHA|TOTAL)",
    r"SALDO\s+FINAL\s+PERIODO",
    r"NUEVO\s+SALDO",
    r"SALDO\s+(?:DISPONIBLE|EN\s+CUENTA)",
    r"PLATA\s+(?:DISPONIBLE|QUE\s+TIENES)",
)

PATRONES_TOTAL_INGRESOS: tuple[str, ...] = (
    r"TOTAL\s+(?:ABONOS|CR[EÉ]DITOS|CONSIGNACIONES|INGRESOS|ENTRADAS|DEP[OÓ]SITOS)",
    r"MOVIMIENTO\s+CR[EÉ]DITO",
    r"(?:ABONOS|CR[EÉ]DITOS|ENTRADAS)\s+DEL\s+(?:MES|PERIODO)",
    r"^ENTRADAS\b",
)

PATRONES_TOTAL_EGRESOS: tuple[str, ...] = (
    r"TOTAL\s+(?:CARGOS|D[EÉ]BITOS|RETIROS|EGRESOS|SALIDAS|PAGOS)",
    r"MOVIMIENTO\s+D[EÉ]BITO",
    r"(?:CARGOS|D[EÉ]BITOS|SALIDAS)\s+DEL\s+(?:MES|PERIODO)",
    r"^SALIDAS\b",
)

PATRONES_PERIODO: tuple[str, ...] = (
    r"(?:PERIODO|PER[IÍ]ODO)\s*(?:DE)?\s*:?\s*(?:DEL?\s*:?\s*)?"
    r"(?P<a>.+?)\s+(?:AL?|HASTA|A)\s+(?P<b>.+?)$",
    r"DESDE\s*:?\s*(?P<a>.+?)\s+HASTA\s*:?\s*(?P<b>.+?)$",
    r"DEL\s+(?P<a>\d.+?)\s+AL\s+(?P<b>\d.+?)$",
    r"(?:MOVIMIENTOS|EXTRACTO)\s+(?:DEL?|ENTRE)\s+(?P<a>.+?)\s+(?:AL?|Y)\s+(?P<b>.+?)$",
)

# Etiquetas reales con las que los cuatro bancos identifican la cuenta.
PATRONES_CUENTA_COMUNES: tuple[str, ...] = (
    # "CUENTA/DEPÓSITO No. 059-01108-0", "Cuenta Numero: 613000355",
    # "CUENTA DE AHORROS No. 123-456789-01"
    r"(?:CUENTA|CTA|DEP[OÓ]SITO)[^\d\n]{0,32}?(\d[\d\-\.]{4,24}\d)",
    # "NÚMERO 4064169148", "Número de 3017704163", "Número Nequi: 3001234567"
    r"N[UÚ]MERO(?:\s+DE)?(?:\s+(?:CUENTA|CTA|PRODUCTO|NEQUI|CELULAR))?"
    r"\s*:?\s*(\d[\d\-\.]{4,24}\d)",
)


@dataclass(frozen=True)
class PerfilBanco:
    """Reglas de lectura de un banco."""

    id: str
    nombre: str
    # Expresiones que identifican al banco en el texto del extracto.
    huellas: tuple[str, ...] = ()
    # Disposición de las columnas de valores. Solo se usa como respaldo: si el
    # extracto trae encabezado de tabla, el esquema se deduce de ahí.
    #   "valor_saldo"           -> ... VALOR SALDO
    #   "debito_credito_saldo"  -> ... DEBITO CREDITO SALDO
    #   "valor"                 -> una sola columna de valor, sin saldo corriente
    esquema: str = "valor_saldo"
    patrones_cuenta: tuple[str, ...] = PATRONES_CUENTA_COMUNES
    ignorar: tuple[str, ...] = ()
    # Acepta montos sin separador de miles (p. ej. "$ 500" o "500").
    montos_debiles: bool = False
    # Une a la descripción anterior las líneas sueltas sin fecha ni valor.
    unir_descripciones: bool = True
    # Reglas propias del banco, con prioridad sobre las listas genéricas.
    # Son (expresión regular, +1 ingreso / -1 egreso) y se evalúan en orden.
    reglas_signo: tuple[tuple[str, int], ...] = ()
    palabras_ingreso: tuple[str, ...] = ()
    palabras_egreso: tuple[str, ...] = ()
    notas: str = ""

    @property
    def patrones_ignorar(self) -> tuple[str, ...]:
        return IGNORAR_COMUNES + self.ignorar


BANCOLOMBIA = PerfilBanco(
    id="bancolombia",
    nombre="Bancolombia",
    huellas=(
        r"BANCOLOMBIA", r"\bSUCURSAL\s+VIRTUAL\b", r"GRUPO\s+BANCOLOMBIA",
        r"\bAHORRO\s+A\s+LA\s+MANO\b",
    ),
    esquema="valor_saldo",
    ignorar=(
        r"^\s*SUCURSAL\s+VIRTUAL", r"^SUCURSAL\b",
        r"\bTOTAL\s+(?:ABONOS|CARGOS)\b",
        r"^\s*(?:CTA|CUENTA)\s+(?:DE\s+)?(?:AHORROS|CORRIENTE)\b",
        r"\bCONOCE\s+NUESTRAS\s+SOLUCIONES\b", r"\bDESDE\s+\$0\b",
        r"^DCF\b",
    ),
    reglas_signo=(
        (r"^CONSIG", 1),
        (r"^ABONO", 1),
        (r"^PAGO\s+DE\s+PROV", -1),
        (r"^IMPTO\b", -1),
        (r"^IVA\b", -1),
        (r"^COMISION", -1),
        (r"^GMF\b", -1),
    ),
    notas="Fechas dd/mm sin año; columnas VALOR y SALDO; débitos con '-' adelante.",
)

NEQUI = PerfilBanco(
    id="nequi",
    nombre="Nequi",
    huellas=(r"NEQUI", r"\bPLATA\s+(?:DISPONIBLE|QUE\s+TIENES)\b", r"BOLSILLO"),
    # El estado de cuenta trae Valor y Saldo, y lista los movimientos del más
    # reciente al más antiguo (el motor detecta el sentido automáticamente).
    esquema="valor_saldo",
    ignorar=(
        r"^\s*NEQUI\s*$", r"\bBOLSILLOS?\b\s*$", r"^\s*COLCHONCITO\b",
        r"\bPLATA\s+(?:DISPONIBLE|QUE\s+TIENES)\b",
        r"^(?:ENTRADAS|SALIDAS)\b",
        r"\bDESCARGA\s+LA\s+APP\b", r"^ESTADO\s+DE\s+CUENTA\s+DE\b",
        r"\bBANCOLOMBIA\s+S\.?A\.?\s+ESTABLECIMIENTO\b",
    ),
    montos_debiles=True,
    reglas_signo=(
        (r"^GRAVAMEN", -1),
        (r"^PARA\b", -1),
        (r"^DE\b", 1),
        (r"^RECIBISTE", 1),
        (r"^TE\s+ENVIARON", 1),
        (r"^ENV[IÍ]O\b", -1),
        (r"^ENVIASTE", -1),
        (r"^PAGASTE", -1),
        (r"^PAGO\s+PSE", -1),
        (r"^SACASTE", -1),
        (r"^COBRO", -1),
        (r"^RECARGA", 1),
    ),
    palabras_ingreso=("RECIBISTE", "TE ENVIARON", "RECARGA", "METES PLATA",
                      "PAGO RECIBIDO", "DEVOLUCION"),
    palabras_egreso=("ENVIASTE", "ENVIO DE DINERO", "PAGASTE", "SACASTE",
                     "RETIRO", "GRAVAMEN", "PARA "),
    notas="Movimientos en orden inverso (más reciente primero); Valor y Saldo.",
)

AVVILLAS = PerfilBanco(
    id="avvillas",
    nombre="Banco AV Villas",
    # "GRUPO AVAL" no sirve como huella: lo comparten AV Villas, Banco de
    # Bogotá, Occidente y Popular.
    huellas=(r"AV\s*VILLAS", r"AVVILLAS", r"BANCO\s+AV\s+VILLAS", r"RENTAVILLAS"),
    # El estado de cuenta Rentavillas solo trae FECHA, DESCRIPCIÓN y VALOR:
    # no hay saldo corriente ni columnas separadas de débito y crédito.
    esquema="valor",
    ignorar=(
        r"^\s*AV\s*VILLAS\s*$", r"\bGRUPO\s+AVAL\b", r"^RENTAVILLAS\b",
        r"^SOLUCIONES\s+LABORALES\b",
    ),
    # El prefijo CRE/DEB del propio banco es la señal más confiable, y manda
    # sobre el texto que sigue: "CRE PAGO PROVEEDOR" es una entrada de plata.
    reglas_signo=(
        (r"^CRE\b", 1),
        (r"^DEB\b", -1),
        (r"^NOTA\s+D[EÉ]BITO", -1),
        (r"^NOTA\s+CR[EÉ]DITO", 1),
        (r"^COMISION", -1),
        (r"^ABONO\s+INTERESES", 1),
        (r"^IMPUESTO", -1),
    ),
    notas="Columnas FECHA/DESCRIPCIÓN/VALOR; el signo va en el prefijo CRE o DEB.",
)

BOGOTA = PerfilBanco(
    id="bogota",
    nombre="Banco de Bogotá",
    huellas=(
        r"BANCO\s+DE\s+BOGOT[AÁ]", r"BANCODEBOGOTA", r"\bBOGOT[AÁ]\s+VIRTUAL\b",
        r"\bPORTAL\s+WEB\b.*BANCO\s+DE\s+BOGOT[AÁ]",
    ),
    esquema="valor_saldo",
    ignorar=(
        r"^\s*BANCO\s+DE\s+BOGOT[AÁ]\s*$", r"\bGRUPO\s+AVAL\b",
        r"^PAGAR\s+TUS\s+IMPUESTOS\b", r"^PORTAL\s+WEB\b",
        r"^RESUMEN\s+DE\s+(?:LA\s+INFORMACI|COBROS)",
        r"^GERENCIA\s+BANCA\b",
    ),
    # "Cargo omision consignacion Nacional" es un egreso aunque diga
    # "consignacion": el prefijo Cargo define la naturaleza.
    reglas_signo=(
        (r"^CARGO\b", -1),
        (r"^GRAVAMEN", -1),
        (r"^INTERESES\s+GANADOS", 1),
        (r"^CONSIGNACION", 1),
        (r"^ABONO", 1),
        (r"^CHEQUE", -1),
        (r"^NOTA\s+D[EÉ]BITO", -1),
        (r"^NOTA\s+CR[EÉ]DITO", 1),
        (r"^RETENCION", -1),
    ),
    notas="Trae Cod Trans, Ciudad, Oficina, Documento, Valor y Saldo.",
)

GENERICO = PerfilBanco(
    id="generico",
    nombre="Banco no identificado",
    huellas=(),
    esquema="valor_saldo",
    montos_debiles=True,
    notas="Perfil de reserva: deduce la estructura del propio extracto.",
)

PERFILES: tuple[PerfilBanco, ...] = (BANCOLOMBIA, NEQUI, AVVILLAS, BOGOTA)
PERFILES_POR_ID: dict[str, PerfilBanco] = {p.id: p for p in PERFILES}
PERFILES_POR_ID[GENERICO.id] = GENERICO

# Alias que el usuario puede escribir en la línea de comandos.
ALIAS: dict[str, str] = {
    "bancolombia": "bancolombia", "banco colombia": "bancolombia",
    "bc": "bancolombia", "bcol": "bancolombia",
    "nequi": "nequi",
    "avvillas": "avvillas", "av villas": "avvillas", "villas": "avvillas",
    "av-villas": "avvillas", "rentavillas": "avvillas",
    "bogota": "bogota", "banco de bogota": "bogota", "bogotá": "bogota",
    "bdb": "bogota", "banco bogota": "bogota",
    "generico": "generico", "otro": "generico",
}


def perfil_por_nombre(nombre: str) -> PerfilBanco | None:
    """Busca un perfil por id o alias escrito por el usuario."""
    from .normalizacion import clave

    if not nombre:
        return None
    limpio = clave(nombre).lower()
    if limpio in ALIAS:
        return PERFILES_POR_ID[ALIAS[limpio]]
    for alias, destino in ALIAS.items():
        if alias in limpio:
            return PERFILES_POR_ID[destino]
    return None

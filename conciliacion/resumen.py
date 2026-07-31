"""Consolidación de movimientos: filtros por periodo y resúmenes mensuales."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .modelos import Confianza, Extracto, Movimiento, ResumenMensual
from .normalizacion import CENTAVOS, clave, etiqueta_periodo, parse_fecha

CERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Rangos de fechas pedidos por el usuario
# ---------------------------------------------------------------------------

def fecha_desde_texto(valor: str, *, fin_de_mes: bool = False) -> date:
    """Interpreta una fecha escrita por el usuario.

    Acepta 2025-03-15, 15/03/2025, 2025-03 (mes completo) y 2025 (año completo).
    Con `fin_de_mes` en True, un mes o un año se resuelven a su último día.
    """
    texto = (valor or "").strip()
    if not texto:
        raise ValueError("Fecha vacía")

    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", texto)
    if m:
        anio, mes = int(m.group(1)), int(m.group(2))
        dia = calendar.monthrange(anio, mes)[1] if fin_de_mes else 1
        return date(anio, mes, dia)

    if re.fullmatch(r"\d{4}", texto):
        anio = int(texto)
        return date(anio, 12, 31) if fin_de_mes else date(anio, 1, 1)

    fecha = parse_fecha(texto)
    if fecha is None:
        raise ValueError(
            f"No entiendo la fecha '{valor}'. Usa AAAA-MM-DD, DD/MM/AAAA, "
            "AAAA-MM o AAAA."
        )
    return fecha


def rango_de_periodo(
    *,
    desde: str | None = None,
    hasta: str | None = None,
    mes: str | None = None,
    anio: int | None = None,
) -> tuple[date | None, date | None]:
    """Convierte las opciones de la línea de comandos en un rango de fechas."""
    if mes:
        inicio = fecha_desde_texto(mes)
        fin = fecha_desde_texto(mes, fin_de_mes=True)
        if len(mes.strip()) == 4:  # se pasó un año en --mes
            fin = date(inicio.year, 12, 31)
        return inicio, fin
    if anio:
        return date(anio, 1, 1), date(anio, 12, 31)
    inicio = fecha_desde_texto(desde) if desde else None
    fin = fecha_desde_texto(hasta, fin_de_mes=True) if hasta else None
    if inicio and fin and inicio > fin:
        raise ValueError("La fecha inicial es posterior a la final.")
    return inicio, fin


# ---------------------------------------------------------------------------
# Unificación de varios extractos
# ---------------------------------------------------------------------------

@dataclass
class Consolidado:
    """Todos los extractos leídos, ya unificados y sin duplicados."""

    movimientos: list[Movimiento] = field(default_factory=list)
    extractos: list[Extracto] = field(default_factory=list)
    duplicados: list[Movimiento] = field(default_factory=list)
    errores: list[tuple[str, str]] = field(default_factory=list)

    @property
    def bancos(self) -> list[str]:
        return sorted({m.banco for m in self.movimientos})


def unificar(extractos: list[Extracto]) -> Consolidado:
    """Junta los movimientos de varios archivos descartando repetidos.

    Es normal que el usuario cargue extractos que se traslapan (por ejemplo, el
    del mes y el del trimestre). Un movimiento del mismo banco, cuenta, fecha,
    valor y descripción se cuenta una sola vez.
    """
    consolidado = Consolidado(extractos=list(extractos))

    # Para cada movimiento se conserva la mayor cantidad de repeticiones vista
    # en un mismo archivo. Si un extracto trae dos veces el mismo movimiento,
    # es porque de verdad ocurrió dos veces; si aparece en dos archivos que se
    # traslapan, se cuenta una sola vez.
    mejores: dict[str, list[Movimiento]] = {}
    for extracto in extractos:
        repetidos: dict[str, list[Movimiento]] = {}
        for movimiento in extracto.movimientos:
            repetidos.setdefault(movimiento.huella, []).append(movimiento)
        for huella, grupo in repetidos.items():
            previo = mejores.get(huella)
            if previo is None or len(grupo) > len(previo):
                if previo is not None:
                    consolidado.duplicados.extend(previo)
                mejores[huella] = grupo
            else:
                consolidado.duplicados.extend(grupo)

    for grupo in mejores.values():
        consolidado.movimientos.extend(grupo)
    consolidado.movimientos.sort(key=lambda m: (m.banco, m.cuenta or "", m.fecha))
    return consolidado


def filtrar(
    movimientos: list[Movimiento],
    *,
    desde: date | None = None,
    hasta: date | None = None,
    bancos: list[str] | None = None,
    cuenta: str | None = None,
    contiene: str | None = None,
) -> list[Movimiento]:
    """Aplica los filtros de periodo, banco, cuenta y texto."""
    nombres = {clave(b) for b in bancos} if bancos else None
    cuenta_canon = re.sub(r"\D", "", cuenta) if cuenta else None
    texto = clave(contiene) if contiene else None

    resultado = []
    for movimiento in movimientos:
        if desde and movimiento.fecha < desde:
            continue
        if hasta and movimiento.fecha > hasta:
            continue
        if nombres and clave(movimiento.banco) not in nombres:
            continue
        if cuenta_canon:
            propia = re.sub(r"\D", "", movimiento.cuenta or "")
            if cuenta_canon not in propia:
                continue
        if texto and texto not in clave(movimiento.descripcion):
            continue
        resultado.append(movimiento)
    return resultado


# ---------------------------------------------------------------------------
# Resumen mensual
# ---------------------------------------------------------------------------

def _saldos_reportados(
    extractos: list[Extracto],
) -> tuple[dict[tuple[str, str, str], Decimal], dict[tuple[str, str, str], Decimal]]:
    """Indexa los saldos inicial y final que cada extracto declara, por mes."""
    iniciales: dict[tuple[str, str, str], Decimal] = {}
    finales: dict[tuple[str, str, str], Decimal] = {}
    for extracto in extractos:
        llave_base = (extracto.banco, extracto.cuenta or "")
        if extracto.periodo_inicio and extracto.saldo_inicial is not None:
            llave = (*llave_base, etiqueta_periodo(extracto.periodo_inicio))
            iniciales.setdefault(llave, extracto.saldo_inicial)
        if extracto.periodo_fin and extracto.saldo_final is not None:
            llave = (*llave_base, etiqueta_periodo(extracto.periodo_fin))
            finales.setdefault(llave, extracto.saldo_final)
    return iniciales, finales


def resumen_mensual(
    movimientos: list[Movimiento], extractos: list[Extracto] | None = None
) -> list[ResumenMensual]:
    """Un resumen por banco, cuenta y mes, con saldos y cuadre.

    El saldo inicial se busca en este orden:
      1. despejado del primer movimiento que trae saldo corriente,
      2. el que declara el extracto para ese mes,
      3. el saldo final del mes anterior de la misma cuenta,
      4. como último recurso, restando los movimientos al saldo final.
    """
    extractos = extractos or []
    iniciales, finales = _saldos_reportados(extractos)

    grupos: dict[tuple[str, str, str], list[Movimiento]] = {}
    for movimiento in movimientos:
        llave = (movimiento.banco, movimiento.cuenta or "", movimiento.periodo)
        grupos.setdefault(llave, []).append(movimiento)

    resumenes: list[ResumenMensual] = []
    saldo_final_previo: dict[tuple[str, str], Decimal] = {}

    for llave in sorted(grupos, key=lambda k: (k[0], k[1], k[2])):
        banco, cuenta, periodo = llave
        delmes = sorted(grupos[llave], key=lambda m: m.fecha)

        ingresos = sum((m.ingreso for m in delmes), CERO)
        egresos = sum((m.egreso for m in delmes), CERO)
        neto = (ingresos - egresos).quantize(CENTAVOS)

        con_saldo = [m for m in delmes if m.saldo is not None]

        saldo_inicial: Decimal | None = None
        if con_saldo and con_saldo[0] is delmes[0]:
            saldo_inicial = (delmes[0].saldo - delmes[0].valor).quantize(CENTAVOS)
        if saldo_inicial is None:
            saldo_inicial = iniciales.get(llave)
        if saldo_inicial is None:
            saldo_inicial = saldo_final_previo.get((banco, cuenta))

        # Saldo final tal como lo reporta el banco.
        saldo_reportado = con_saldo[-1].saldo if con_saldo else finales.get(llave)

        deducido = False
        if saldo_inicial is None and saldo_reportado is not None:
            saldo_inicial = (saldo_reportado - neto).quantize(CENTAVOS)
            deducido = True

        saldo_final = saldo_reportado
        if saldo_final is None and saldo_inicial is not None:
            saldo_final = (saldo_inicial + neto).quantize(CENTAVOS)

        resumenes.append(
            ResumenMensual(
                banco=banco,
                cuenta=cuenta or None,
                periodo=periodo,
                cantidad_movimientos=len(delmes),
                ingresos=ingresos.quantize(CENTAVOS),
                egresos=egresos.quantize(CENTAVOS),
                saldo_inicial=saldo_inicial,
                saldo_final=saldo_final,
                saldo_final_extracto=saldo_reportado,
                saldo_inicial_deducido=deducido,
                movimientos_sin_clasificar=sum(
                    1 for m in delmes if m.confianza == Confianza.DESCONOCIDA
                ),
                movimientos_por_palabras=sum(
                    1 for m in delmes if m.confianza == Confianza.PALABRAS
                ),
            )
        )
        if saldo_final is not None:
            saldo_final_previo[(banco, cuenta)] = saldo_final

    return resumenes


# ---------------------------------------------------------------------------
# Totales por banco y generales
# ---------------------------------------------------------------------------

@dataclass
class TotalBanco:
    banco: str
    cuentas: list[str]
    cantidad_movimientos: int
    ingresos: Decimal
    egresos: Decimal
    meses: list[str]
    saldo_inicial: Decimal | None = None
    saldo_final: Decimal | None = None

    @property
    def neto(self) -> Decimal:
        return (self.ingresos - self.egresos).quantize(CENTAVOS)


def totales_por_banco(
    movimientos: list[Movimiento], resumenes: list[ResumenMensual] | None = None
) -> list[TotalBanco]:
    """Agrega el periodo completo consultado, banco por banco."""
    resumenes = resumenes if resumenes is not None else resumen_mensual(movimientos)

    por_banco: dict[str, list[ResumenMensual]] = {}
    for resumen in resumenes:
        por_banco.setdefault(resumen.banco, []).append(resumen)

    totales: list[TotalBanco] = []
    for banco in sorted(por_banco):
        filas = sorted(por_banco[banco], key=lambda r: r.periodo)
        movs_banco = [m for m in movimientos if m.banco == banco]
        # El saldo inicial del rango es el del primer mes; el final, el del último.
        primeros = [f for f in filas if f.saldo_inicial is not None]
        ultimos = [f for f in filas if f.saldo_final is not None]
        totales.append(
            TotalBanco(
                banco=banco,
                cuentas=sorted({f.cuenta for f in filas if f.cuenta}),
                cantidad_movimientos=len(movs_banco),
                ingresos=sum((f.ingresos for f in filas), CERO).quantize(CENTAVOS),
                egresos=sum((f.egresos for f in filas), CERO).quantize(CENTAVOS),
                meses=sorted({f.periodo for f in filas}),
                saldo_inicial=primeros[0].saldo_inicial if primeros else None,
                saldo_final=ultimos[-1].saldo_final if ultimos else None,
            )
        )
    return totales


def totales_por_mes(resumenes: list[ResumenMensual]) -> list[dict[str, object]]:
    """Suma todos los bancos mes a mes, para ver el flujo consolidado."""
    por_mes: dict[str, list[ResumenMensual]] = {}
    for resumen in resumenes:
        por_mes.setdefault(resumen.periodo, []).append(resumen)

    filas: list[dict[str, object]] = []
    for periodo in sorted(por_mes):
        grupo = por_mes[periodo]
        ingresos = sum((r.ingresos for r in grupo), CERO)
        egresos = sum((r.egresos for r in grupo), CERO)
        saldos = [r.saldo_final for r in grupo if r.saldo_final is not None]
        filas.append(
            {
                "periodo": periodo,
                "bancos": len({r.banco for r in grupo}),
                "cantidad_movimientos": sum(r.cantidad_movimientos for r in grupo),
                "ingresos": ingresos.quantize(CENTAVOS),
                "egresos": egresos.quantize(CENTAVOS),
                "neto": (ingresos - egresos).quantize(CENTAVOS),
                "saldo_final": sum(saldos, CERO).quantize(CENTAVOS) if saldos else None,
            }
        )
    return filas

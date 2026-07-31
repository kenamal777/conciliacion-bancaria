"""Modelos de datos del proceso de conciliación."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from .normalizacion import CENTAVOS, clave, etiqueta_periodo


class Confianza(str, Enum):
    """De dónde salió el signo (ingreso/egreso) de un movimiento.

    El orden refleja qué tanto se puede confiar en la clasificación:
    SALDO es matemáticamente verificable, PALABRAS es una heurística.
    """

    SALDO = "saldo"          # deducido del delta del saldo corriente
    COLUMNA = "columna"      # el extracto trae columnas débito/crédito separadas
    SIGNO = "signo"          # el propio valor traía signo, paréntesis o DB/CR
    MARCA = "marca"          # el banco marcó la naturaleza (el CRE/DEB de AV Villas)
    PALABRAS = "palabras"    # inferido por la descripción del movimiento
    DESCONOCIDA = "desconocida"


class Tipo(str, Enum):
    INGRESO = "INGRESO"
    EGRESO = "EGRESO"
    NEUTRO = "NEUTRO"


@dataclass
class Movimiento:
    """Un movimiento (débito o crédito) de un extracto bancario.

    `valor` siempre viene con signo: positivo = ingreso, negativo = egreso.
    """

    banco: str
    fecha: date
    descripcion: str
    valor: Decimal
    cuenta: str | None = None
    saldo: Decimal | None = None
    referencia: str | None = None
    documento: str | None = None
    confianza: Confianza = Confianza.DESCONOCIDA
    archivo: str | None = None
    pagina: int | None = None
    linea_original: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.valor, Decimal):
            self.valor = Decimal(str(self.valor))
        self.valor = self.valor.quantize(CENTAVOS)
        if self.saldo is not None:
            if not isinstance(self.saldo, Decimal):
                self.saldo = Decimal(str(self.saldo))
            self.saldo = self.saldo.quantize(CENTAVOS)

    @property
    def tipo(self) -> Tipo:
        if self.valor > 0:
            return Tipo.INGRESO
        if self.valor < 0:
            return Tipo.EGRESO
        return Tipo.NEUTRO

    @property
    def ingreso(self) -> Decimal:
        return self.valor if self.valor > 0 else Decimal("0.00")

    @property
    def egreso(self) -> Decimal:
        """Valor absoluto del egreso (positivo, como se presenta en reportes)."""
        return -self.valor if self.valor < 0 else Decimal("0.00")

    @property
    def periodo(self) -> str:
        return etiqueta_periodo(self.fecha)

    @property
    def huella(self) -> str:
        """Identidad del movimiento, para reconocerlo si aparece en dos archivos.

        Incluye el saldo porque es lo único que distingue dos transacciones
        legítimamente iguales: dos envíos de $100.000 a la misma persona el
        mismo día son dos movimientos, no uno repetido, y sus saldos difieren.
        El mismo movimiento visto en dos extractos que se traslapan sí trae el
        mismo saldo, así que se sigue reconociendo como repetido.
        """
        base = "|".join(
            [
                clave(self.banco),
                clave(self.cuenta or ""),
                self.fecha.isoformat(),
                str(self.valor),
                str(self.saldo) if self.saldo is not None else "",
                clave(self.descripcion)[:60],
            ]
        )
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    def como_fila(self) -> dict[str, object]:
        return {
            "banco": self.banco,
            "cuenta": self.cuenta or "",
            "fecha": self.fecha.isoformat(),
            "periodo": self.periodo,
            "descripcion": self.descripcion,
            "referencia": self.referencia or "",
            "documento": self.documento or "",
            "tipo": self.tipo.value,
            "ingreso": self.ingreso,
            "egreso": self.egreso,
            "valor": self.valor,
            "saldo": self.saldo if self.saldo is not None else "",
            "confianza": self.confianza.value,
            "archivo": self.archivo or "",
        }


@dataclass
class Extracto:
    """El resultado de leer un archivo de extracto."""

    banco: str
    archivo: str
    cuenta: str | None = None
    titular: str | None = None
    periodo_inicio: date | None = None
    periodo_fin: date | None = None
    saldo_inicial: Decimal | None = None
    saldo_final: Decimal | None = None
    total_ingresos_reportado: Decimal | None = None
    total_egresos_reportado: Decimal | None = None
    movimientos: list[Movimiento] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)
    lineas_no_reconocidas: list[str] = field(default_factory=list)
    motor_texto: str = ""
    detectado_automaticamente: bool = True

    @property
    def suma_movimientos(self) -> Decimal:
        return sum((m.valor for m in self.movimientos), Decimal("0.00"))

    @property
    def cuadra(self) -> bool | None:
        """¿El saldo inicial + movimientos coincide con el saldo final del extracto?

        None cuando el extracto no reporta ambos saldos y no se puede validar.
        """
        if self.saldo_inicial is None or self.saldo_final is None:
            return None
        diferencia = (self.saldo_inicial + self.suma_movimientos) - self.saldo_final
        return abs(diferencia) <= Decimal("0.02")

    @property
    def diferencia_cuadre(self) -> Decimal | None:
        if self.saldo_inicial is None or self.saldo_final is None:
            return None
        return ((self.saldo_inicial + self.suma_movimientos) - self.saldo_final).quantize(
            CENTAVOS
        )


@dataclass
class ResumenMensual:
    """Resumen de un mes para una cuenta bancaria."""

    banco: str
    cuenta: str | None
    periodo: str  # AAAA-MM
    cantidad_movimientos: int
    ingresos: Decimal
    egresos: Decimal
    saldo_inicial: Decimal | None = None
    saldo_final: Decimal | None = None
    saldo_final_extracto: Decimal | None = None
    movimientos_sin_clasificar: int = 0
    movimientos_por_palabras: int = 0
    # True cuando el saldo inicial se despejó restando los movimientos al saldo
    # final. En ese caso el cuadre es trivial y no prueba nada, así que no se
    # reporta como verificado.
    saldo_inicial_deducido: bool = False

    @property
    def neto(self) -> Decimal:
        return (self.ingresos - self.egresos).quantize(CENTAVOS)

    @property
    def saldo_final_calculado(self) -> Decimal | None:
        if self.saldo_inicial is None:
            return None
        return (self.saldo_inicial + self.neto).quantize(CENTAVOS)

    @property
    def diferencia(self) -> Decimal | None:
        """Diferencia entre el saldo final calculado y el reportado por el banco.

        Cero significa que el mes está cuadrado y la lectura fue completa.
        """
        if self.saldo_inicial_deducido:
            return None
        referencia = self.saldo_final_extracto
        if referencia is None:
            referencia = self.saldo_final
        calculado = self.saldo_final_calculado
        if calculado is None or referencia is None:
            return None
        return (calculado - referencia).quantize(CENTAVOS)

    @property
    def cuadra(self) -> bool | None:
        d = self.diferencia
        if d is None:
            return None
        return abs(d) <= Decimal("0.02")

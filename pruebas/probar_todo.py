"""Pruebas de extremo a extremo. Se ejecutan con:

    python pruebas/probar_todo.py

No requieren pytest ni dependencias externas: generan sus propios PDF y
verifican los números contra valores calculados a mano.
"""

from __future__ import annotations

import glob
import os
import shutil
import sys
import tempfile
from datetime import date
from decimal import Decimal

CARPETA = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(CARPETA)
sys.path.insert(0, RAIZ)
sys.path.insert(0, CARPETA)

from util_pdf import pdf_desde_texto  # noqa: E402

from conciliacion.libro import conciliar, leer_libro  # noqa: E402
from conciliacion.modelos import Confianza  # noqa: E402
from conciliacion.motor import analizar, analizar_archivo  # noqa: E402
from conciliacion.normalizacion import parse_fecha, parse_monto  # noqa: E402
from conciliacion.reportes import CAMPOS_RESUMEN, escribir_csv, filas_resumen  # noqa: E402
from conciliacion.resumen import (  # noqa: E402
    filtrar,
    rango_de_periodo,
    resumen_mensual,
    totales_por_banco,
    unificar,
)

DATOS = os.path.join(CARPETA, "datos")

fallas: list[str] = []
pruebas = 0


def revisar(condicion: bool, mensaje: str) -> None:
    global pruebas
    pruebas += 1
    if condicion:
        print(f"  ok   {mensaje}")
    else:
        print(f"  FALLA {mensaje}")
        fallas.append(mensaje)


def igual(obtenido, esperado, mensaje: str) -> None:
    revisar(obtenido == esperado, f"{mensaje} (esperado {esperado}, obtenido {obtenido})")


def dec(texto: str) -> Decimal:
    return Decimal(texto)


# ---------------------------------------------------------------------------

def probar_normalizacion() -> None:
    print("\n[1] Normalización de montos y fechas")
    igual(parse_monto("1.234.567,89"), dec("1234567.89"), "formato colombiano")
    igual(parse_monto("1,234,567.89"), dec("1234567.89"), "formato anglosajón")
    igual(parse_monto("2.300.000,00-"), dec("-2300000.00"), "signo al final")
    igual(parse_monto("(45.000,50)"), dec("-45000.50"), "paréntesis")
    igual(parse_monto("$ 50.000"), dec("50000.00"), "con símbolo de peso")
    igual(parse_monto("50.000 DB"), dec("-50000.00"), "sufijo débito")
    igual(parse_monto("1.500"), dec("1500.00"), "punto como miles")
    igual(parse_monto("12,50"), dec("12.50"), "coma como decimal")
    igual(parse_monto("no es plata"), None, "texto no numérico")

    igual(parse_fecha("01/03/2025"), date(2025, 3, 1), "dd/mm/aaaa")
    igual(parse_fecha("2025-03-01"), date(2025, 3, 1), "ISO")
    igual(parse_fecha("12 de marzo de 2025"), date(2025, 3, 12), "mes en palabras")
    igual(parse_fecha("15-ABR-2025"), date(2025, 4, 15), "mes abreviado")
    igual(parse_fecha("01/03", 2025), date(2025, 3, 1), "sin año, con año por defecto")
    igual(parse_fecha("31/02/2025"), None, "fecha inexistente")


ESPERADO = {
    "bancolombia_marzo.txt": {
        "banco": "Bancolombia",
        "cuenta": "123-456789-01",
        "movimientos": 10,
        "ingresos": dec("5021400.00"),
        "egresos": dec("4399650.00"),
        "saldo_inicial": dec("5000000.00"),
        "saldo_final": dec("5621750.00"),
    },
    "nequi_marzo.txt": {
        "banco": "Nequi",
        "cuenta": "3001234567",
        "movimientos": 8,
        "ingresos": dec("1090000.00"),
        "egresos": dec("530000.00"),
        "saldo_inicial": None,
        "saldo_final": dec("1250000.00"),
    },
    "avvillas_abril.txt": {
        "banco": "Banco AV Villas",
        "cuenta": "987654-321",
        "movimientos": 8,
        "ingresos": dec("2570000.00"),
        "egresos": dec("1851876.00"),
        "saldo_inicial": dec("2000000.00"),
        "saldo_final": dec("2718124.00"),
    },
    "bogota_abril.txt": {
        "banco": "Banco de Bogotá",
        "cuenta": "445566778",
        "movimientos": 7,
        "ingresos": dec("5565400.00"),
        "egresos": dec("4757000.00"),
        "saldo_inicial": dec("8000000.00"),
        "saldo_final": dec("8808400.00"),
    },
}


def probar_bancos() -> list:
    print("\n[2] Lectura de los cuatro bancos")
    extractos = []
    for nombre, esperado in ESPERADO.items():
        extracto = analizar_archivo(os.path.join(DATOS, nombre))
        extractos.append(extracto)
        igual(extracto.banco, esperado["banco"], f"{nombre}: banco detectado")
        igual(extracto.cuenta, esperado["cuenta"], f"{nombre}: cuenta")
        igual(
            len(extracto.movimientos),
            esperado["movimientos"],
            f"{nombre}: cantidad de movimientos",
        )
        ingresos = sum((m.ingreso for m in extracto.movimientos), dec("0"))
        egresos = sum((m.egreso for m in extracto.movimientos), dec("0"))
        igual(ingresos, esperado["ingresos"], f"{nombre}: total ingresos")
        igual(egresos, esperado["egresos"], f"{nombre}: total egresos")
        igual(extracto.saldo_inicial, esperado["saldo_inicial"], f"{nombre}: saldo inicial")
        igual(extracto.saldo_final, esperado["saldo_final"], f"{nombre}: saldo final")
        revisar(
            extracto.cuadra in (True, None),
            f"{nombre}: el extracto cuadra ({extracto.diferencia_cuadre})",
        )
        revisar(
            not extracto.lineas_no_reconocidas,
            f"{nombre}: sin líneas con valores sin interpretar "
            f"{extracto.lineas_no_reconocidas}",
        )
    return extractos


def probar_totales_reportados(extractos: list) -> None:
    print("\n[3] Cruce contra los totales que imprime cada extracto")
    for extracto in extractos:
        nombre = os.path.basename(extracto.archivo)
        if extracto.total_ingresos_reportado is not None:
            ingresos = sum((m.ingreso for m in extracto.movimientos), dec("0"))
            igual(
                ingresos,
                extracto.total_ingresos_reportado,
                f"{nombre}: ingresos vs total reportado",
            )
        if extracto.total_egresos_reportado is not None:
            egresos = sum((m.egreso for m in extracto.movimientos), dec("0"))
            igual(
                egresos,
                extracto.total_egresos_reportado,
                f"{nombre}: egresos vs total reportado",
            )


def probar_pdf() -> None:
    print("\n[4] Mismo resultado leyendo PDF en vez de texto")
    carpeta = tempfile.mkdtemp(prefix="conciliacion_pdf_")
    try:
        for nombre, esperado in ESPERADO.items():
            with open(os.path.join(DATOS, nombre), encoding="utf-8") as archivo:
                contenido = archivo.read()
            ruta_pdf = os.path.join(carpeta, nombre.replace(".txt", ".pdf"))
            with open(ruta_pdf, "wb") as salida:
                salida.write(pdf_desde_texto(contenido))

            extracto = analizar_archivo(ruta_pdf)
            igual(extracto.banco, esperado["banco"], f"PDF {nombre}: banco")
            igual(
                len(extracto.movimientos),
                esperado["movimientos"],
                f"PDF {nombre}: movimientos",
            )
            ingresos = sum((m.ingreso for m in extracto.movimientos), dec("0"))
            egresos = sum((m.egreso for m in extracto.movimientos), dec("0"))
            igual(ingresos, esperado["ingresos"], f"PDF {nombre}: ingresos")
            igual(egresos, esperado["egresos"], f"PDF {nombre}: egresos")
            igual(extracto.saldo_final, esperado["saldo_final"], f"PDF {nombre}: saldo final")
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def probar_formatos_reales() -> list:
    """Réplicas fieles de los extractos reales de los cuatro bancos.

    Cada cifra sale de un extracto de verdad, así que estas verificaciones son
    las que realmente importan.
    """
    print("\n[4b] Formatos reales de los cuatro bancos")
    extractos = []

    # --- AV Villas: FECHA/DESCRIPCION/VALOR, sin saldo, signo en CRE/DEB ---
    av = analizar_archivo(os.path.join(DATOS, "avvillas_real_dic2023.txt"))
    extractos.append(av)
    igual(av.banco, "Banco AV Villas", "AV Villas real: banco")
    igual(av.cuenta, "059-01108-0", "AV Villas real: cuenta con guiones")
    igual(len(av.movimientos), 11, "AV Villas real: movimientos")
    igual(
        sum((m.ingreso for m in av.movimientos), dec("0")),
        dec("12584726.01"),
        "AV Villas real: ingresos (validados contra 'Movimiento credito')",
    )
    igual(
        sum((m.egreso for m in av.movimientos), dec("0")),
        dec("17662416.26"),
        "AV Villas real: egresos (validados contra 'Movimiento debito')",
    )
    igual(av.saldo_inicial, dec("7954713.11"), "AV Villas real: saldo inicial")
    igual(av.saldo_final, dec("2877022.86"), "AV Villas real: saldo final")
    igual(av.cuadra, True, "AV Villas real: cuadra sin tener columna de saldo")
    revisar(
        all(m.confianza == Confianza.MARCA for m in av.movimientos),
        "AV Villas real: el signo sale de la marca CRE/DEB del banco",
    )
    creditos = [m for m in av.movimientos if m.descripcion.startswith("CRE")]
    revisar(
        all(m.valor > 0 for m in creditos),
        "AV Villas real: 'CRE PAGO PROVEEDOR' es ingreso pese a decir 'pago'",
    )
    revisar(not av.lineas_no_reconocidas,
            f"AV Villas real: sin líneas sueltas {av.lineas_no_reconocidas}")

    # --- Bancolombia: VALOR y SALDO, menos adelante, fechas d/mm ---
    bc = analizar_archivo(os.path.join(DATOS, "bancolombia_real_feb2024.txt"))
    extractos.append(bc)
    igual(bc.cuenta, "4064169148", "Bancolombia real: cuenta desde 'NUMERO'")
    igual(
        (bc.periodo_inicio, bc.periodo_fin),
        (date(2024, 1, 31), date(2024, 2, 29)),
        "Bancolombia real: periodo DESDE/HASTA con fechas ISO",
    )
    igual(len(bc.movimientos), 6, "Bancolombia real: movimientos")
    igual(bc.movimientos[0].fecha, date(2024, 2, 1), "Bancolombia real: fecha '1/02'")
    igual(
        bc.saldo_inicial,
        dec("549327803.70"),
        "Bancolombia real: saldo anterior, no el saldo promedio de al lado",
    )
    igual(bc.saldo_final, dec("549365006.50"), "Bancolombia real: saldo actual")
    igual(bc.cuadra, True, "Bancolombia real: cuadra")
    igual(
        sum((m.egreso for m in bc.movimientos), dec("0")),
        dec("2644519.96"),
        "Bancolombia real: egresos",
    )
    revisar(
        all(m.confianza == Confianza.SALDO for m in bc.movimientos),
        "Bancolombia real: todos los signos verificados con el saldo",
    )

    # --- Banco de Bogotá: montos dentro de la descripción y filas partidas ---
    bg = analizar_archivo(os.path.join(DATOS, "bogota_real_jul2020.txt"))
    extractos.append(bg)
    igual(bg.banco, "Banco de Bogotá", "Bogotá real: banco (no confundir con AV Villas)")
    igual(bg.cuenta, "613000355", "Bogotá real: cuenta desde 'Cuenta Numero:'")
    igual(
        (bg.periodo_inicio, bg.periodo_fin),
        (date(2020, 7, 1), date(2020, 7, 31)),
        "Bogotá real: periodo 'Desde: Julio 01' con el año del documento",
    )
    igual(len(bg.movimientos), 14, "Bogotá real: movimientos (7 partidos en 2 líneas)")
    igual(
        bg.movimientos[0].valor,
        dec("10800.00"),
        "Bogotá real: NO toma el (10,000.00) de la descripción como valor",
    )
    igual(
        sum((m.ingreso for m in bg.movimientos), dec("0")),
        dec("76111.00"),
        "Bogotá real: ingresos",
    )
    igual(
        sum((m.egreso for m in bg.movimientos), dec("0")),
        dec("31645.24"),
        "Bogotá real: egresos",
    )
    igual(bg.saldo_final, dec("201910619.61"), "Bogotá real: saldo final")
    igual(bg.cuadra, True, "Bogotá real: cuadra")
    cargos = [m for m in bg.movimientos if "omision consignacion" in m.descripcion]
    igual(len(cargos), 2, "Bogotá real: se leyeron los cargos por omisión")
    revisar(
        all(m.valor < 0 for m in cargos),
        "Bogotá real: 'Cargo omision consignacion' es egreso pese a decir "
        "'consignacion'",
    )
    revisar(not bg.lineas_no_reconocidas,
            f"Bogotá real: sin líneas sueltas {bg.lineas_no_reconocidas}")

    # --- Nequi: movimientos del más reciente al más antiguo ---
    nq = analizar_archivo(os.path.join(DATOS, "nequi_real_oct2021.txt"))
    extractos.append(nq)
    igual(nq.cuenta, "3017704163", "Nequi real: cuenta desde 'Numero de'")
    igual(len(nq.movimientos), 15, "Nequi real: movimientos")
    revisar(
        any("más reciente al más antiguo" in a for a in nq.advertencias),
        "Nequi real: detecta que las filas van en orden inverso",
    )
    revisar(
        all(m.valor < 0 for m in nq.movimientos),
        "Nequi real: ningún signo quedó invertido (el error que causaría leer "
        "el saldo al revés)",
    )
    igual(
        sum((m.egreso for m in nq.movimientos), dec("0")),
        dec("803466.00"),
        "Nequi real: total de egresos de la página",
    )
    igual(
        nq.movimientos[0].fecha, date(2021, 10, 22), "Nequi real: queda en orden cronológico"
    )
    igual(nq.movimientos[-1].fecha, date(2021, 10, 29), "Nequi real: última fecha")
    igual(
        sum(1 for m in nq.movimientos if m.confianza == Confianza.SALDO),
        14,
        "Nequi real: 14 de 15 verificados con el saldo",
    )
    igual(nq.saldo_inicial, dec("2547406.96"), "Nequi real: saldo anterior del mes")
    igual(nq.cuadra, False, "Nequi real: avisa que la página 1 no cuadra sola")
    revisar(
        any("no cuadra" in a for a in nq.advertencias),
        "Nequi real: advierte que faltan movimientos por leer",
    )
    revisar(
        any("2065196.88" in a for a in nq.advertencias),
        "Nequi real: cruza contra el total de abonos del extracto",
    )
    revisar(not nq.lineas_no_reconocidas,
            f"Nequi real: sin líneas sueltas {nq.lineas_no_reconocidas}")

    return extractos


def probar_pdf_reales() -> None:
    """Leer el PDF debe dar exactamente lo mismo que leer el texto.

    Es la garantía de que el lector interno de PDF conserva las columnas: si
    perdiera el alineado, los valores se irían a la columna equivocada.
    """
    print("\n[4c] Los formatos reales dan lo mismo en texto y en PDF")
    carpeta = tempfile.mkdtemp(prefix="conciliacion_real_pdf_")
    try:
        for ruta in sorted(glob.glob(os.path.join(DATOS, "*_real_*.txt"))):
            nombre = os.path.basename(ruta)
            with open(ruta, encoding="utf-8") as archivo:
                contenido = archivo.read()
            ruta_pdf = os.path.join(carpeta, nombre.replace(".txt", ".pdf"))
            with open(ruta_pdf, "wb") as salida:
                salida.write(pdf_desde_texto(contenido))

            def resumen(extracto):
                return (
                    len(extracto.movimientos),
                    sum((m.ingreso for m in extracto.movimientos), dec("0")),
                    sum((m.egreso for m in extracto.movimientos), dec("0")),
                    extracto.saldo_final,
                    extracto.cuadra,
                )

            igual(
                resumen(analizar_archivo(ruta_pdf)),
                resumen(analizar_archivo(ruta)),
                f"PDF {nombre}: idéntico al texto",
            )
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def probar_resumen_y_filtros(extractos: list) -> None:
    print("\n[5] Resumen mensual, totales por banco y filtros de periodo")
    consolidado = unificar(extractos)
    igual(len(consolidado.movimientos), 33, "movimientos consolidados")
    igual(len(consolidado.duplicados), 0, "sin duplicados en la primera carga")

    resumenes = resumen_mensual(consolidado.movimientos, consolidado.extractos)
    igual(len(resumenes), 4, "cuatro resúmenes (banco + mes)")

    por_llave = {(r.banco, r.periodo): r for r in resumenes}
    bancolombia = por_llave[("Bancolombia", "2025-03")]
    igual(bancolombia.ingresos, dec("5021400.00"), "Bancolombia marzo: ingresos")
    igual(bancolombia.egresos, dec("4399650.00"), "Bancolombia marzo: egresos")
    igual(bancolombia.neto, dec("621750.00"), "Bancolombia marzo: neto")
    igual(bancolombia.saldo_final, dec("5621750.00"), "Bancolombia marzo: saldo final")
    igual(bancolombia.cuadra, True, "Bancolombia marzo: cuadra")

    nequi = por_llave[("Nequi", "2025-03")]
    igual(nequi.saldo_inicial, dec("690000.00"), "Nequi: saldo inicial despejado")
    revisar(nequi.saldo_inicial_deducido, "Nequi: el saldo inicial queda marcado como deducido")
    igual(nequi.diferencia, None, "Nequi: no se reporta un cuadre falso")

    # Filtros de periodo
    desde, hasta = rango_de_periodo(mes="2025-03")
    igual((desde, hasta), (date(2025, 3, 1), date(2025, 3, 31)), "rango de un mes")
    marzo = filtrar(consolidado.movimientos, desde=desde, hasta=hasta)
    igual(len(marzo), 18, "movimientos de marzo (Bancolombia + Nequi)")

    desde, hasta = rango_de_periodo(anio=2025)
    igual((desde, hasta), (date(2025, 1, 1), date(2025, 12, 31)), "rango de un año")

    desde, hasta = rango_de_periodo(desde="2025-04-01", hasta="2025-04-15")
    quincena = filtrar(consolidado.movimientos, desde=desde, hasta=hasta)
    igual(len(quincena), 7, "primera quincena de abril")

    solo_nequi = filtrar(consolidado.movimientos, bancos=["Nequi"])
    igual(len(solo_nequi), 8, "filtro por banco")

    por_cuenta = filtrar(consolidado.movimientos, cuenta="456789")
    igual(len(por_cuenta), 10, "filtro por número de cuenta")

    consignaciones = filtrar(consolidado.movimientos, contiene="consignacion")
    igual(len(consignaciones), 4, "filtro por texto en la descripción")

    totales = totales_por_banco(consolidado.movimientos, resumenes)
    igual(len(totales), 4, "un total por banco")
    igual(
        sum((t.ingresos for t in totales), dec("0")),
        dec("14246800.00"),
        "ingresos de todos los bancos",
    )
    igual(
        sum((t.egresos for t in totales), dec("0")),
        dec("11538526.00"),
        "egresos de todos los bancos",
    )


def probar_clasificacion() -> None:
    """Concepto y tercero: en qué se fue la plata y con quién."""
    print("\n[5b] Clasificación por concepto y por tercero")
    from conciliacion.clasificacion import (
        cargar_reglas,
        clasificar,
        por_concepto,
        por_tercero,
    )

    movimientos = []
    for nombre in sorted(os.listdir(DATOS)):
        if "_real_" in nombre and nombre.endswith(".txt"):
            movimientos.extend(
                analizar_archivo(os.path.join(DATOS, nombre)).movimientos
            )
    clasificar(movimientos)

    por_descripcion = {m.descripcion: m for m in movimientos}

    def revisar_caso(fragmento: str, concepto: str, tercero: str) -> None:
        encontrados = [
            m for d, m in por_descripcion.items() if fragmento.lower() in d.lower()
        ]
        if not encontrados:
            revisar(False, f"no se encontró un movimiento con '{fragmento}'")
            return
        movimiento = encontrados[0]
        igual(movimiento.concepto, concepto, f"'{fragmento[:28]}' -> concepto")
        igual(movimiento.tercero, tercero, f"'{fragmento[:28]}' -> tercero")

    # Casos tomados de los extractos reales.
    revisar_caso("NOTA DEBITO I.V.A", "IVA", "(el propio banco)")
    revisar_caso("IMPUESTO FINANCIERO 4X1000", "GMF (4x1000)", "(el propio banco)")
    revisar_caso("Gravamen al Movimiento", "GMF (4x1000)", "(el propio banco)")
    revisar_caso(
        "COMISION SERVICIO NOMINA", "Comisiones y cuotas de manejo",
        "(el propio banco)",
    )
    revisar_caso("DEB PAGO NOMINA", "Nómina", "NOVD AUTOM.SISTEMAS")
    revisar_caso("PAGO DE PROV SERVIEQUIPOS", "Proveedores", "SERVIEQUIPOS")
    # La ciudad dentro del nombre del banco no se puede borrar.
    revisar_caso(
        "TRANSF INTERNET", "Transferencias recibidas", "BANCO DE BOGOTA",
    )
    revisar_caso("CRE TRANSF ACH", "Transferencias recibidas", "BANCOLOMBIA")
    # Un cargo por consignación fallida es del banco, no de un tercero.
    revisar_caso(
        "Cargo omision consignacion", "Cargos y ajustes del banco",
        "(el propio banco)",
    )
    revisar_caso("Intereses Ganados", "Intereses y rendimientos", "(el propio banco)")
    revisar_caso("Para EDILBERTO", "Transferencias enviadas", "EDILBERTO SAENZ GARCIA")

    # Invariante: los conceptos tienen que sumar exactamente lo mismo que el
    # resumen. Si un movimiento se perdiera o se contara dos veces, aquí falla.
    conceptos = por_concepto(movimientos)
    ingresos_concepto = sum(
        (f.total for f in conceptos if f.tipo == "INGRESO"), dec("0")
    )
    egresos_concepto = sum((f.total for f in conceptos if f.tipo == "EGRESO"), dec("0"))
    igual(
        ingresos_concepto,
        sum((m.ingreso for m in movimientos), dec("0")),
        "los conceptos suman los mismos ingresos que el resumen",
    )
    igual(
        egresos_concepto,
        sum((m.egreso for m in movimientos), dec("0")),
        "los conceptos suman los mismos egresos que el resumen",
    )
    igual(
        sum(f.cantidad for f in conceptos),
        len(movimientos),
        "ningún movimiento queda fuera de un concepto",
    )

    # Unificación de terceros: los cuatro envíos a la misma persona son un solo
    # tercero, no cuatro.
    terceros = {f.tercero: f for f in por_tercero(movimientos)}
    revisar("SONIA BLANCO" in terceros, "agrupa los movimientos de un mismo tercero")
    if "SONIA BLANCO" in terceros:
        igual(terceros["SONIA BLANCO"].cantidad, 4, "SONIA BLANCO: 4 movimientos")
        igual(terceros["SONIA BLANCO"].total, dec("700000.00"), "SONIA BLANCO: total")
    igual(
        sum(f.cantidad for f in por_tercero(movimientos)),
        len(movimientos),
        "ningún movimiento queda fuera del consolidado por tercero",
    )

    # Reglas propias del usuario: manda lo que diga el contador.
    carpeta = tempfile.mkdtemp(prefix="conciliacion_reglas_")
    try:
        ruta = os.path.join(carpeta, "terceros.csv")
        with open(ruta, "w", encoding="utf-8") as archivo:
            archivo.write("# patron;tercero;concepto\n")
            archivo.write("SONIA BLANCO;SONIA BLANCO RAMIREZ;Nomina\n")
        reglas, avisos = cargar_reglas(ruta)
        igual(len(reglas), 1, "lee las reglas propias del usuario")
        revisar(bool(avisos), "informa que se aplicaron reglas propias")

        clasificar(movimientos, reglas)
        sonia = [m for m in movimientos if m.tercero == "SONIA BLANCO RAMIREZ"]
        igual(len(sonia), 4, "la regla renombra al tercero")
        revisar(
            all(m.concepto == "Nomina" for m in sonia),
            "la regla reclasifica el concepto",
        )
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)

    # Se vuelve a dejar como estaba para no afectar otras pruebas.
    clasificar(movimientos)


def probar_duplicados(extractos: list) -> None:
    print("\n[6] Descarte de extractos traslapados")
    doble = unificar(extractos + extractos)
    igual(len(doble.movimientos), 33, "no se duplican movimientos repetidos")
    igual(len(doble.duplicados), 33, "los repetidos se reportan aparte")

    # El extracto real de Nequi trae dos envíos de $100.000 a la misma persona
    # el mismo día, y dos gravámenes de $400: son movimientos distintos, no
    # repeticiones. Confundirlos restaría plata del resumen.
    nequi = analizar_archivo(os.path.join(DATOS, "nequi_real_oct2021.txt"))
    solo = unificar([nequi])
    igual(
        len(solo.movimientos),
        15,
        "conserva transacciones iguales del mismo día (no son duplicados)",
    )
    igual(len(solo.duplicados), 0, "no reporta duplicados donde no hay")
    repetido = unificar([nequi, nequi])
    igual(
        len(repetido.movimientos),
        15,
        "el mismo archivo cargado dos veces no infla el resumen",
    )


TEXTO_FIN_DE_ANIO = """BANCOLOMBIA S.A.
CUENTA DE AHORROS No. 111-222333-44
PERIODO 15/12/2024 AL 15/01/2025

SALDO ANTERIOR                                            1.000.000,00

FECHA  DESCRIPCION                    DOCUMENTO      VALOR          SALDO
20/12  CONSIGNACION NACIONAL            0000001   500.000,00   1.500.000,00
28/12  PAGO PROVEEDOR                   0000002   200.000,00-  1.300.000,00
05/01  CONSIGNACION NACIONAL            0000003   300.000,00   1.600.000,00
10/01  CUOTA MANEJO                     0000004    13.500,00-  1.586.500,00

SALDO ACTUAL                                              1.586.500,00
"""


def probar_cambio_de_anio() -> None:
    print("\n[7] Extracto a caballo entre dos años (fechas sin año)")
    extracto = analizar(TEXTO_FIN_DE_ANIO, archivo="bancolombia_dic_ene.txt")
    igual(len(extracto.movimientos), 4, "movimientos leídos")
    fechas = [m.fecha for m in extracto.movimientos]
    igual(fechas[0], date(2024, 12, 20), "diciembre queda en 2024")
    igual(fechas[1], date(2024, 12, 28), "diciembre queda en 2024")
    igual(fechas[2], date(2025, 1, 5), "enero queda en 2025")
    igual(fechas[3], date(2025, 1, 10), "enero queda en 2025")
    igual(extracto.cuadra, True, "el extracto cuadra")

    resumenes = resumen_mensual(extracto.movimientos, [extracto])
    igual(len(resumenes), 2, "se separa en dos meses")
    periodos = sorted(r.periodo for r in resumenes)
    igual(periodos, ["2024-12", "2025-01"], "meses correctos")


def probar_conciliacion() -> None:
    print("\n[8] Conciliación contra el libro auxiliar")
    extracto = analizar_archivo(os.path.join(DATOS, "bancolombia_marzo.txt"))
    apuntes, advertencias = leer_libro(os.path.join(DATOS, "libro_auxiliar_marzo.csv"))
    igual(len(apuntes), 7, "apuntes del libro leídos")
    igual(advertencias, [], "sin advertencias al leer el libro")
    igual(apuntes[0].valor, dec("3200000.00"), "débito del libro es ingreso")
    igual(apuntes[1].valor, dec("-2300000.00"), "crédito del libro es egreso")

    resultado = conciliar(
        extracto.movimientos,
        apuntes,
        saldo_extracto=extracto.saldo_final,
        saldo_libros=dec("4879650.00"),
    )
    igual(len(resultado.parejas), 6, "partidas conciliadas")
    igual(len(resultado.solo_banco), 4, "partidas solo del banco")
    igual(len(resultado.solo_libro), 1, "partidas solo del libro")
    igual(resultado.ajuste_desde_banco, dec("-750000.00"), "cheque pendiente de cobro")
    igual(resultado.ajuste_desde_libros, dec("-7900.00"), "gastos bancarios sin registrar")
    igual(
        resultado.saldo_conciliado_banco,
        dec("4871750.00"),
        "saldo conciliado por el lado del banco",
    )
    igual(
        resultado.saldo_conciliado_libros,
        dec("4871750.00"),
        "saldo conciliado por el lado de los libros",
    )
    igual(resultado.diferencia, dec("0.00"), "la conciliación cierra en cero")
    igual(resultado.concilia, True, "estado conciliado")

    # Con el signo invertido debe avisar en vez de fallar en silencio.
    invertidos, _ = leer_libro(
        os.path.join(DATOS, "libro_auxiliar_marzo.csv"), invertir_signo=True
    )
    al_reves = conciliar(extracto.movimientos, invertidos)
    revisar(
        any("invertir" in a.lower() for a in al_reves.advertencias),
        "avisa cuando el libro trae los signos al revés",
    )


def probar_exportacion(extractos: list) -> None:
    print("\n[9] Exportación a CSV")
    consolidado = unificar(extractos)
    resumenes = resumen_mensual(consolidado.movimientos, consolidado.extractos)
    carpeta = tempfile.mkdtemp(prefix="conciliacion_csv_")
    try:
        ruta = escribir_csv(
            os.path.join(carpeta, "resumen.csv"), CAMPOS_RESUMEN, filas_resumen(resumenes)
        )
        revisar(os.path.isfile(ruta), "se generó el CSV del resumen")
        with open(ruta, encoding="utf-8-sig") as archivo:
            lineas = archivo.read().splitlines()
        igual(len(lineas), 5, "encabezado más cuatro filas")
        revisar("5.621.750,00" in lineas[1] or "5.621.750,00" in "".join(lineas),
                "los montos salen con formato colombiano")
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def probar_confianza(extractos: list) -> None:
    print("\n[10] Trazabilidad de la clasificación ingreso/egreso")
    por_archivo = {os.path.basename(e.archivo): e for e in extractos}
    bancolombia = por_archivo["bancolombia_marzo.txt"]
    revisar(
        all(m.confianza == Confianza.SALDO for m in bancolombia.movimientos),
        "Bancolombia: todos los signos verificados contra el saldo",
    )
    nequi = por_archivo["nequi_marzo.txt"]
    revisar(
        all(m.confianza != Confianza.DESCONOCIDA for m in nequi.movimientos),
        "Nequi: ningún movimiento queda sin clasificar",
    )
    egresos_nequi = {m.descripcion for m in nequi.movimientos if m.valor < 0}
    revisar(
        any("Envio de dinero" in d for d in egresos_nequi),
        "Nequi: los envíos de dinero son egresos",
    )
    ingresos_nequi = {m.descripcion for m in nequi.movimientos if m.valor > 0}
    revisar(
        any("Recibiste" in d for d in ingresos_nequi),
        "Nequi: lo recibido es ingreso",
    )


def main() -> int:
    print("PRUEBAS DE CONCILIACIÓN BANCARIA")
    probar_normalizacion()
    extractos = probar_bancos()
    probar_totales_reportados(extractos)
    probar_pdf()
    probar_formatos_reales()
    probar_pdf_reales()
    probar_resumen_y_filtros(extractos)
    probar_clasificacion()
    probar_duplicados(extractos)
    probar_cambio_de_anio()
    probar_conciliacion()
    probar_exportacion(extractos)
    probar_confianza(extractos)

    print("\n" + "=" * 60)
    if fallas:
        print(f"RESULTADO: {len(fallas)} falla(s) de {pruebas} verificaciones")
        for falla in fallas:
            print(f"  - {falla}")
        return 1
    print(f"RESULTADO: {pruebas} verificaciones, todas correctas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

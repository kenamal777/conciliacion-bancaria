"""Interfaz de línea de comandos.

    python -m conciliacion resumen extractos/*.pdf
    python -m conciliacion resumen extractos/ --mes 2025-03 --salida reportes/
    python -m conciliacion movimientos extractos/ --banco nequi --desde 2025-01-01
    python -m conciliacion conciliar extractos/ --libro auxiliar.csv --saldo-libros 5000000
    python -m conciliacion diagnostico extracto_raro.pdf
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from . import __version__
from .extraccion import motores_disponibles
from .extraccion.texto import EXTENSIONES_SOPORTADAS
from .modelos import Extracto
from .motor import analizar_archivo
from .normalizacion import formato_cop, parse_monto, periodo_legible
from .perfiles import PERFILES
from .reportes import (
    escribir_csv,
    exportar,
    reporte_advertencias,
    reporte_lectura,
    reporte_mensual_consolidado,
    reporte_movimientos,
    reporte_resumen_mensual,
    reporte_totales_banco,
)
from .resumen import (
    Consolidado,
    filtrar,
    rango_de_periodo,
    resumen_mensual,
    totales_por_banco,
    unificar,
)

ANCHO = 100


def _titulo(texto: str) -> str:
    return f"\n{texto}\n{'=' * min(len(texto), ANCHO)}"


# ---------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------

def expandir_rutas(entradas: list[str]) -> list[str]:
    """Convierte carpetas y comodines en una lista concreta de archivos."""
    rutas: list[str] = []
    for entrada in entradas:
        if os.path.isdir(entrada):
            for raiz, _, archivos in os.walk(entrada):
                for nombre in sorted(archivos):
                    if os.path.splitext(nombre)[1].lower() in EXTENSIONES_SOPORTADAS:
                        rutas.append(os.path.join(raiz, nombre))
        elif any(c in entrada for c in "*?["):
            rutas.extend(sorted(glob.glob(entrada, recursive=True)))
        else:
            rutas.append(entrada)

    vistos: set[str] = set()
    unicas: list[str] = []
    for ruta in rutas:
        absoluta = os.path.abspath(ruta)
        if absoluta in vistos:
            continue
        vistos.add(absoluta)
        unicas.append(ruta)
    return unicas


def leer_extractos(rutas: list[str], args: argparse.Namespace) -> Consolidado:
    """Lee todos los archivos y arma el consolidado, sin abortar por uno malo."""
    extractos: list[Extracto] = []
    errores: list[tuple[str, str]] = []

    for ruta in rutas:
        try:
            extracto = analizar_archivo(
                ruta,
                banco=getattr(args, "banco", None),
                anio_defecto=getattr(args, "anio_defecto", None),
                dpi=getattr(args, "dpi", 300),
                idioma=getattr(args, "idioma", "spa"),
                forzar_ocr=getattr(args, "forzar_ocr", False),
            )
        except Exception as error:
            errores.append((ruta, str(error)))
            print(f"  ! {os.path.basename(ruta)}: {error}", file=sys.stderr)
            continue
        extractos.append(extracto)
        print(
            f"  · {os.path.basename(ruta)}: {extracto.banco}, "
            f"{len(extracto.movimientos)} movimientos ({extracto.motor_texto})"
        )

    consolidado = unificar(extractos)
    consolidado.errores = errores
    return consolidado


def _rango(args: argparse.Namespace) -> tuple[date | None, date | None]:
    return rango_de_periodo(
        desde=getattr(args, "desde", None),
        hasta=getattr(args, "hasta", None),
        mes=getattr(args, "mes", None),
        anio=getattr(args, "anio", None),
    )


def _describir_rango(desde: date | None, hasta: date | None) -> str:
    if desde and hasta:
        return f"{desde.strftime('%d/%m/%Y')} a {hasta.strftime('%d/%m/%Y')}"
    if desde:
        return f"desde {desde.strftime('%d/%m/%Y')}"
    if hasta:
        return f"hasta {hasta.strftime('%d/%m/%Y')}"
    return "todo el periodo disponible"


def _encabezado_grupo(etiqueta: str) -> str:
    borde = "#" * ANCHO
    return f"\n\n{borde}\n#  {etiqueta.upper()}\n{borde}"


def _grupos_de_bancos(
    movimientos: list, args: argparse.Namespace
) -> list[tuple[str, list]]:
    """Decide si se emite un solo informe o uno por banco además del conjunto.

    Devuelve pares (etiqueta, movimientos). La etiqueta vacía significa que no
    hay separación por banco, así que el reporte sale sin encabezado extra.
    """
    bancos = sorted({m.banco for m in movimientos})
    if not getattr(args, "por_banco", False) or len(bancos) < 2:
        return [("", movimientos)]

    grupos: list[tuple[str, list]] = [("CONSOLIDADO", movimientos)]
    for banco in bancos:
        grupos.append((banco, [m for m in movimientos if m.banco == banco]))
    return grupos


def _monto_argumento(texto: str | None) -> Decimal | None:
    if texto is None:
        return None
    valor = parse_monto(texto)
    if valor is None:
        raise SystemExit(f"No entiendo el monto '{texto}'.")
    return valor


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def _clasificar(movimientos: list, args: argparse.Namespace) -> None:
    """Deduce concepto y tercero de cada movimiento, con las reglas del usuario."""
    from .clasificacion import cargar_reglas, clasificar

    ruta = getattr(args, "reglas", None) or "terceros.csv"
    reglas, avisos = cargar_reglas(ruta)
    for aviso in avisos:
        print(f"  {aviso}")
    clasificar(movimientos, reglas)


def comando_informe(args: argparse.Namespace) -> int:
    """Informe detallado: en qué se fue la plata y con quién."""
    from .reportes import (
        reporte_concepto_y_tercero,
        reporte_por_concepto,
        reporte_terceros,
    )

    rutas = expandir_rutas(args.archivos)
    if not rutas:
        print("No encontré archivos para procesar.", file=sys.stderr)
        return 1

    print(_titulo(f"LECTURA DE {len(rutas)} ARCHIVO(S)"))
    consolidado = leer_extractos(rutas, args)
    if not consolidado.movimientos:
        print("\nNo se obtuvo ningún movimiento.", file=sys.stderr)
        return 2

    desde, hasta = _rango(args)
    movimientos = filtrar(
        consolidado.movimientos,
        desde=desde,
        hasta=hasta,
        bancos=args.solo_banco,
        cuenta=args.cuenta,
        contiene=args.contiene,
    )
    if not movimientos:
        print("\nSin movimientos en el rango solicitado.", file=sys.stderr)
        return 2

    _clasificar(movimientos, args)

    marca = datetime.now().strftime("%Y%m%d_%H%M")
    for etiqueta, del_grupo in _grupos_de_bancos(movimientos, args):
        if etiqueta:
            print(_encabezado_grupo(etiqueta))

        print(_titulo(f"RESUMEN POR CONCEPTO  ({_describir_rango(desde, hasta)})"))
        print(reporte_por_concepto(del_grupo))

        print(_titulo("DETALLE POR CONCEPTO Y TERCERO"))
        print(reporte_concepto_y_tercero(del_grupo))

        print(_titulo("CONSOLIDADO POR TERCERO"))
        print(reporte_terceros(del_grupo, limite=args.limite))

        if args.salida:
            from .reportes import (
                CAMPOS_CONCEPTO_TERCERO,
                CAMPOS_TERCEROS,
                escribir_csv,
                etiqueta_archivo,
                filas_concepto_tercero,
                filas_terceros,
            )

            os.makedirs(args.salida, exist_ok=True)
            sufijo = f"_{etiqueta_archivo(etiqueta)}" if etiqueta else ""
            generados = [
                escribir_csv(
                    os.path.join(args.salida, f"{marca}{sufijo}_por_concepto.csv"),
                    CAMPOS_CONCEPTO_TERCERO,
                    filas_concepto_tercero(del_grupo),
                ),
                escribir_csv(
                    os.path.join(args.salida, f"{marca}{sufijo}_por_tercero.csv"),
                    CAMPOS_TERCEROS,
                    filas_terceros(del_grupo),
                ),
            ]
            print(_titulo("ARCHIVOS GENERADOS"))
            for ruta in generados:
                print(f"  {ruta}")

    return 0


def comando_resumen(args: argparse.Namespace) -> int:
    rutas = expandir_rutas(args.archivos)
    if not rutas:
        print("No encontré archivos para procesar.", file=sys.stderr)
        return 1

    print(_titulo(f"LECTURA DE {len(rutas)} ARCHIVO(S)"))
    consolidado = leer_extractos(rutas, args)
    if not consolidado.movimientos:
        print("\nNo se obtuvo ningún movimiento.", file=sys.stderr)
        print(
            "Prueba: python -m conciliacion diagnostico <archivo> "
            "para ver qué texto se está leyendo.",
            file=sys.stderr,
        )
        return 2

    desde, hasta = _rango(args)
    movimientos = filtrar(
        consolidado.movimientos,
        desde=desde,
        hasta=hasta,
        bancos=args.solo_banco,
        cuenta=args.cuenta,
        contiene=args.contiene,
    )
    if not movimientos:
        print(
            f"\nNingún movimiento en el rango solicitado ({_describir_rango(desde, hasta)}).",
            file=sys.stderr,
        )
        return 2

    _clasificar(movimientos, args)

    print(_titulo("ARCHIVOS PROCESADOS"))
    print(reporte_lectura(consolidado))

    advertencias = reporte_advertencias(consolidado)
    if advertencias:
        print(_titulo("PUNTOS A REVISAR"))
        print(advertencias)

    marca = args.prefijo or datetime.now().strftime("%Y%m%d_%H%M")
    for etiqueta, del_grupo in _grupos_de_bancos(movimientos, args):
        if etiqueta:
            print(_encabezado_grupo(etiqueta))
        _emitir_resumen(
            del_grupo,
            consolidado,
            args,
            desde,
            hasta,
            marca=marca,
            etiqueta=etiqueta,
        )
    return 0


def _emitir_resumen(
    movimientos: list,
    consolidado: Consolidado,
    args: argparse.Namespace,
    desde: date | None,
    hasta: date | None,
    *,
    marca: str,
    etiqueta: str = "",
) -> None:
    """Imprime y exporta el resumen de un conjunto de movimientos."""
    resumenes = resumen_mensual(movimientos, consolidado.extractos)
    totales = totales_por_banco(movimientos, resumenes)

    print(_titulo(f"RESUMEN MENSUAL POR BANCO  ({_describir_rango(desde, hasta)})"))
    print(reporte_resumen_mensual(resumenes))

    print(_titulo("TOTALES POR BANCO EN EL PERIODO"))
    print(reporte_totales_banco(totales))

    if len({r.banco for r in resumenes}) > 1:
        print(_titulo("CONSOLIDADO MES A MES (TODOS LOS BANCOS)"))
        print(reporte_mensual_consolidado(resumenes))

    if args.detalle:
        print(_titulo("DETALLE DE MOVIMIENTOS"))
        print(reporte_movimientos(movimientos, limite=args.detalle))

    if not args.salida:
        return

    from .reportes import etiqueta_archivo

    formatos = [f.strip().lower() for f in args.formato.split(",") if f.strip()]
    prefijo = marca if not etiqueta else f"{marca}_{etiqueta_archivo(etiqueta)}"
    generados = exportar(
        args.salida,
        consolidado=Consolidado(
            movimientos=movimientos,
            extractos=consolidado.extractos,
            duplicados=consolidado.duplicados,
            errores=consolidado.errores,
        ),
        resumenes=resumenes,
        totales=totales,
        formatos=formatos,
        prefijo=prefijo,
    )
    print(_titulo("ARCHIVOS GENERADOS"))
    for ruta in generados:
        print(f"  {ruta}")
    if "xlsx" in formatos and not any(r.endswith(".xlsx") for r in generados):
        print(
            "  (No se generó el Excel: falta openpyxl. "
            "Instala con: pip install openpyxl)"
        )


def comando_movimientos(args: argparse.Namespace) -> int:
    rutas = expandir_rutas(args.archivos)
    if not rutas:
        print("No encontré archivos para procesar.", file=sys.stderr)
        return 1

    print(_titulo(f"LECTURA DE {len(rutas)} ARCHIVO(S)"))
    consolidado = leer_extractos(rutas, args)

    desde, hasta = _rango(args)
    movimientos = filtrar(
        consolidado.movimientos,
        desde=desde,
        hasta=hasta,
        bancos=args.solo_banco,
        cuenta=args.cuenta,
        contiene=args.contiene,
    )
    if not movimientos:
        print("\nSin movimientos para los filtros dados.", file=sys.stderr)
        return 2

    print(_titulo(f"MOVIMIENTOS  ({_describir_rango(desde, hasta)})"))
    print(reporte_movimientos(movimientos, limite=args.limite))

    ingresos = sum((m.ingreso for m in movimientos), Decimal("0.00"))
    egresos = sum((m.egreso for m in movimientos), Decimal("0.00"))
    print(
        f"\n  {len(movimientos)} movimientos  ·  Ingresos {formato_cop(ingresos)}  "
        f"·  Egresos {formato_cop(egresos)}  ·  Neto {formato_cop(ingresos - egresos)}"
    )

    if args.salida:
        from .reportes import CAMPOS_MOVIMIENTOS

        ruta = args.salida
        if os.path.isdir(ruta) or not ruta.lower().endswith(".csv"):
            ruta = os.path.join(ruta, "movimientos.csv")
        escribir_csv(ruta, CAMPOS_MOVIMIENTOS, [m.como_fila() for m in movimientos])
        print(f"\n  Archivo generado: {ruta}")
    return 0


def comando_conciliar(args: argparse.Namespace) -> int:
    from .libro import (
        CAMPOS_CONCILIACION,
        conciliar,
        filas_conciliacion,
        leer_libro,
        reporte_conciliacion,
    )

    rutas = expandir_rutas(args.archivos)
    if not rutas:
        print("No encontré extractos para procesar.", file=sys.stderr)
        return 1

    print(_titulo(f"LECTURA DE {len(rutas)} EXTRACTO(S)"))
    consolidado = leer_extractos(rutas, args)

    desde, hasta = _rango(args)
    movimientos = filtrar(
        consolidado.movimientos,
        desde=desde,
        hasta=hasta,
        bancos=args.solo_banco,
        cuenta=args.cuenta,
    )
    if not movimientos:
        print("\nSin movimientos del banco para conciliar.", file=sys.stderr)
        return 2

    print(_titulo("LECTURA DEL LIBRO AUXILIAR"))
    apuntes, advertencias_libro = leer_libro(
        args.libro,
        invertir_signo=args.invertir_signo,
        anio_defecto=getattr(args, "anio_defecto", None),
    )
    print(f"  {len(apuntes)} apuntes leídos de {os.path.basename(args.libro)}")
    for advertencia in advertencias_libro:
        print(f"  - {advertencia}")

    if desde or hasta:
        apuntes = [
            a
            for a in apuntes
            if (not desde or a.fecha >= desde) and (not hasta or a.fecha <= hasta)
        ]
        print(f"  {len(apuntes)} apuntes dentro del rango solicitado")

    resumenes = resumen_mensual(movimientos, consolidado.extractos)
    saldo_extracto = _monto_argumento(args.saldo_extracto)
    if saldo_extracto is None:
        finales = [r.saldo_final for r in sorted(resumenes, key=lambda r: r.periodo)
                   if r.saldo_final is not None]
        if finales:
            saldo_extracto = finales[-1]

    resultado = conciliar(
        movimientos,
        apuntes,
        dias_tolerancia=args.dias,
        saldo_extracto=saldo_extracto,
        saldo_libros=_monto_argumento(args.saldo_libros),
    )

    print(_titulo(f"CONCILIACIÓN BANCARIA  ({_describir_rango(desde, hasta)})"))
    print(reporte_conciliacion(resultado))

    for advertencia in resultado.advertencias:
        print(f"\n  ATENCIÓN: {advertencia}")

    if args.salida:
        ruta = args.salida
        if os.path.isdir(ruta) or not ruta.lower().endswith(".csv"):
            ruta = os.path.join(ruta, "conciliacion.csv")
        escribir_csv(ruta, CAMPOS_CONCILIACION, filas_conciliacion(resultado))
        print(f"\n  Archivo generado: {ruta}")

    return 0 if resultado.concilia is not False else 3


def comando_diagnostico(args: argparse.Namespace) -> int:
    """Muestra el texto extraído y qué se reconoció: para ajustar perfiles."""
    from .extraccion import extraer_texto
    from .motor import analizar, detectar_banco
    from .perfiles import perfil_por_nombre

    rutas = expandir_rutas(args.archivos)
    if not rutas:
        print("No encontré archivos.", file=sys.stderr)
        return 1

    for ruta in rutas:
        print(_titulo(f"DIAGNÓSTICO: {os.path.basename(ruta)}"))
        try:
            extraido = extraer_texto(
                ruta, dpi=args.dpi, idioma=args.idioma, forzar_ocr=args.forzar_ocr
            )
        except Exception as error:
            print(f"  No se pudo extraer texto: {error}")
            continue

        perfil_detectado, puntaje = detectar_banco(extraido.texto)
        print(f"  Motor de lectura: {extraido.motor}")
        print(f"  Páginas: {len(extraido.paginas)}")
        print(f"  Banco detectado: {perfil_detectado.nombre} (puntaje {puntaje})")
        for advertencia in extraido.advertencias:
            print(f"  Advertencia: {advertencia}")

        perfil = perfil_por_nombre(args.banco) if args.banco else None
        extracto = analizar(
            paginas=extraido.paginas,
            perfil=perfil,
            archivo=ruta,
            anio_defecto=args.anio_defecto,
            motor_texto=extraido.motor,
        )

        print(f"\n  Cuenta: {extracto.cuenta or '(no detectada)'}")
        print(
            f"  Periodo: {extracto.periodo_inicio} a {extracto.periodo_fin}  ·  "
            f"Saldo inicial: {formato_cop(extracto.saldo_inicial)}  ·  "
            f"Saldo final: {formato_cop(extracto.saldo_final)}"
        )
        print(f"  Movimientos reconocidos: {len(extracto.movimientos)}")
        print(f"  Cuadre: {extracto.diferencia_cuadre}")

        if extracto.movimientos:
            print("\n  MOVIMIENTOS:")
            print(reporte_movimientos(extracto.movimientos))

        if extracto.lineas_no_reconocidas:
            print("\n  LÍNEAS CON VALORES QUE NO SE PUDIERON INTERPRETAR:")
            for linea in extracto.lineas_no_reconocidas[:30]:
                print(f"    | {linea}")

        if args.texto:
            print("\n  TEXTO EXTRAÍDO:")
            for numero, pagina in enumerate(extraido.paginas, start=1):
                print(f"  --- página {numero} ---")
                for linea in pagina.splitlines():
                    print(f"  | {linea}")
    return 0


def comando_entorno(_: argparse.Namespace) -> int:
    print(_titulo(f"CONCILIACIÓN BANCARIA v{__version__}"))
    print(f"  Python: {sys.version.split()[0]}")
    print("\n  Bancos soportados:")
    for perfil in PERFILES:
        print(f"    - {perfil.nombre} (--banco {perfil.id}): {perfil.notas}")

    print("\n  Componentes opcionales:")
    etiquetas = {
        "pdfplumber": "lectura precisa de PDF (recomendado)",
        "pymupdf": "lectura de PDF y rasterizado para OCR",
        "pypdf": "lectura básica de PDF",
        "pillow": "manejo de imágenes JPG/PNG",
        "pytesseract": "puente hacia Tesseract OCR",
        "tesseract_binario": "programa Tesseract OCR instalado",
        "pdf2image": "alternativa para rasterizar PDF",
        "openpyxl": "exportar a Excel y leer libros .xlsx",
    }
    disponibles = motores_disponibles()
    for nombre, etiqueta in etiquetas.items():
        estado = "instalado" if disponibles.get(nombre) else "FALTA"
        print(f"    [{estado:>9}] {nombre:<18} {etiqueta}")

    print("\n  Lectura de PDF de texto: siempre disponible (lector interno).")
    print(
        "  Lectura de JPG/PNG: "
        + ("disponible." if disponibles["ocr"] else "NO disponible, falta OCR.")
    )
    if not disponibles["ocr"]:
        print(
            "\n  Para habilitar imágenes y PDF escaneados:\n"
            "    pip install pytesseract pillow pymupdf\n"
            "    Windows: instalar Tesseract de "
            "https://github.com/UB-Mannheim/tesseract/wiki\n"
            "    macOS:   brew install tesseract tesseract-lang\n"
            "    Ubuntu:  sudo apt install tesseract-ocr tesseract-ocr-spa"
        )
    return 0


# ---------------------------------------------------------------------------
# Definición de argumentos
# ---------------------------------------------------------------------------

def _agregar_filtros(sub: argparse.ArgumentParser) -> None:
    grupo = sub.add_argument_group("filtros de periodo")
    grupo.add_argument("--desde", help="Fecha inicial (AAAA-MM-DD o DD/MM/AAAA)")
    grupo.add_argument("--hasta", help="Fecha final (AAAA-MM-DD o DD/MM/AAAA)")
    grupo.add_argument("--mes", help="Un mes puntual: AAAA-MM (ej. 2025-03)")
    grupo.add_argument("--anio", type=int, help="Un año completo: AAAA")

    otros = sub.add_argument_group("otros filtros")
    otros.add_argument(
        "--solo-banco",
        action="append",
        metavar="BANCO",
        help="Incluir solo este banco (se puede repetir)",
    )
    otros.add_argument("--cuenta", help="Filtrar por número de cuenta (o parte)")
    otros.add_argument("--contiene", help="Filtrar por texto en la descripción")
    otros.add_argument(
        "--reglas",
        help="CSV con reglas propias de tercero y concepto "
        "(por defecto: terceros.csv)",
    )


def _agregar_lectura(sub: argparse.ArgumentParser) -> None:
    grupo = sub.add_argument_group("opciones de lectura")
    grupo.add_argument(
        "--banco",
        help="Forzar el banco de todos los archivos en vez de detectarlo "
        "(bancolombia, nequi, avvillas, bogota, generico)",
    )
    grupo.add_argument(
        "--anio-defecto",
        type=int,
        help="Año a usar cuando el extracto solo trae día y mes",
    )
    grupo.add_argument("--dpi", type=int, default=300, help="Resolución del OCR")
    grupo.add_argument("--idioma", default="spa", help="Idioma del OCR (por defecto spa)")
    grupo.add_argument(
        "--forzar-ocr",
        action="store_true",
        help="Usar OCR incluso si el PDF trae texto",
    )


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conciliacion",
        description=(
            "Conciliación bancaria a partir de extractos en PDF, JPG o PNG de "
            "Bancolombia, Nequi, AV Villas y Banco de Bogotá."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python -m conciliacion resumen extractos/\n"
            "  python -m conciliacion resumen extractos/ --mes 2025-03 "
            "--salida reportes/\n"
            "  python -m conciliacion resumen extractos/ --desde 2025-01-01 "
            "--hasta 2025-06-30\n"
            "  python -m conciliacion informe extractos/ --mes 2025-03\n"
            "  python -m conciliacion movimientos extractos/ --solo-banco nequi\n"
            "  python -m conciliacion conciliar extractos/ --libro auxiliar.csv\n"
            "  python -m conciliacion diagnostico extracto.pdf --texto\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="comando", required=True)

    resumen = subparsers.add_parser(
        "resumen",
        help="Resumen mensual por banco (el reporte principal)",
        description="Genera el resumen mensual por banco de los extractos indicados.",
    )
    resumen.add_argument(
        "archivos", nargs="+", help="Archivos o carpetas con los extractos"
    )
    _agregar_filtros(resumen)
    _agregar_lectura(resumen)
    salida = resumen.add_argument_group("salida")
    salida.add_argument("--salida", help="Carpeta donde escribir los reportes")
    salida.add_argument(
        "--formato",
        default="csv,xlsx",
        help="Formatos a generar, separados por coma: csv,xlsx",
    )
    salida.add_argument("--prefijo", help="Prefijo de los archivos generados")
    salida.add_argument(
        "--detalle",
        nargs="?",
        type=int,
        const=50,
        help="Mostrar también el detalle de movimientos (opcional: cuántos)",
    )
    salida.add_argument(
        "--por-banco",
        action="store_true",
        help="Además del consolidado, un informe y un juego de archivos "
        "separado por cada banco",
    )
    resumen.set_defaults(funcion=comando_resumen)

    movimientos = subparsers.add_parser(
        "movimientos", help="Listado detallado de movimientos"
    )
    movimientos.add_argument("archivos", nargs="+")
    _agregar_filtros(movimientos)
    _agregar_lectura(movimientos)
    movimientos.add_argument("--limite", type=int, help="Máximo de filas a mostrar")
    movimientos.add_argument("--salida", help="Ruta o carpeta del CSV a generar")
    movimientos.set_defaults(funcion=comando_movimientos)

    informe = subparsers.add_parser(
        "informe",
        help="Informe detallado por concepto (IVA, comisiones, nómina...) y tercero",
        description=(
            "Muestra en qué se fue la plata y con quién: cada concepto abierto "
            "por tercero, con los nombres del mismo tercero unificados."
        ),
    )
    informe.add_argument("archivos", nargs="+")
    _agregar_filtros(informe)
    _agregar_lectura(informe)
    informe.add_argument(
        "--limite", type=int, help="Máximo de terceros a mostrar en el consolidado"
    )
    informe.add_argument(
        "--por-banco",
        action="store_true",
        help="Además del consolidado, un informe separado por cada banco",
    )
    informe.add_argument("--salida", help="Carpeta donde escribir los CSV")
    informe.set_defaults(funcion=comando_informe)

    conciliar_cmd = subparsers.add_parser(
        "conciliar",
        help="Conciliar los extractos contra el libro auxiliar contable",
    )
    conciliar_cmd.add_argument("archivos", nargs="+")
    conciliar_cmd.add_argument(
        "--libro", required=True, help="CSV o Excel con el libro auxiliar"
    )
    conciliar_cmd.add_argument(
        "--dias", type=int, default=5, help="Tolerancia de días al emparejar"
    )
    conciliar_cmd.add_argument(
        "--saldo-libros", help="Saldo contable al corte, para el cuadre completo"
    )
    conciliar_cmd.add_argument(
        "--saldo-extracto", help="Saldo del banco al corte (si no, se toma del extracto)"
    )
    conciliar_cmd.add_argument(
        "--invertir-signo",
        action="store_true",
        help="Si tu libro usa la convención de signos contraria",
    )
    _agregar_filtros(conciliar_cmd)
    _agregar_lectura(conciliar_cmd)
    conciliar_cmd.add_argument("--salida", help="Ruta o carpeta del CSV a generar")
    conciliar_cmd.set_defaults(funcion=comando_conciliar)

    diagnostico = subparsers.add_parser(
        "diagnostico",
        help="Ver qué está leyendo el programa en un archivo (para depurar)",
    )
    diagnostico.add_argument("archivos", nargs="+")
    _agregar_lectura(diagnostico)
    diagnostico.add_argument(
        "--texto", action="store_true", help="Imprimir todo el texto extraído"
    )
    diagnostico.set_defaults(funcion=comando_diagnostico)

    entorno = subparsers.add_parser(
        "entorno", help="Ver bancos soportados y componentes instalados"
    )
    entorno.set_defaults(funcion=comando_entorno)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = construir_parser()
    args = parser.parse_args(argv)
    try:
        return args.funcion(args)
    except (ValueError, InvalidOperation) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except FileNotFoundError as error:
        print(f"Error: no encontré el archivo {error.filename or error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

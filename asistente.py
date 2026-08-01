"""Asistente interactivo: usar el programa sin escribir comandos.

Es lo que abre CONCILIAR.bat al hacerle doble clic. Trabaja con dos carpetas
que están junto a este archivo:

    1-EXTRACTOS  ->  ahí se ponen los PDF, JPG o PNG del banco
    2-REPORTES   ->  ahí quedan los Excel y CSV generados

Toda la interacción es por menú, en español, y cualquier error se explica en
lugar de mostrar un mensaje técnico.
"""

from __future__ import annotations

import os
import re
import sys
import traceback
from datetime import date
from decimal import Decimal

CARPETA_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CARPETA_BASE)

CARPETA_EXTRACTOS = os.path.join(CARPETA_BASE, "1-EXTRACTOS")
CARPETA_REPORTES = os.path.join(CARPETA_BASE, "2-REPORTES")
# Aquí el usuario corrige a mano los terceros y conceptos que el programa no
# adivine bien.
RUTA_REGLAS = os.path.join(CARPETA_BASE, "terceros.csv")

ANCHO = 78


# ---------------------------------------------------------------------------
# Presentación
# ---------------------------------------------------------------------------

def linea(caracter: str = "=") -> None:
    print(caracter * ANCHO)


def titulo(texto: str) -> None:
    print()
    linea()
    print(f"  {texto}")
    linea()


def pausa(mensaje: str = "Presione ENTER para continuar...") -> None:
    try:
        input(f"\n{mensaje}")
    except (EOFError, KeyboardInterrupt):
        pass


def preguntar(mensaje: str, defecto: str = "") -> str:
    try:
        respuesta = input(mensaje).strip()
    except (EOFError, KeyboardInterrupt):
        return defecto
    return respuesta or defecto


def si_o_no(mensaje: str, defecto: bool = True) -> bool:
    marca = "S/n" if defecto else "s/N"
    respuesta = preguntar(f"{mensaje} ({marca}): ").lower()
    if not respuesta:
        return defecto
    return respuesta.startswith("s")


def abrir_carpeta(ruta: str) -> None:
    """Abre la carpeta en el explorador de Windows."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(ruta)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{ruta}"')
        else:
            os.system(f'xdg-open "{ruta}"')
    except Exception:
        print(f"  No pude abrir la carpeta. Está en: {ruta}")


# ---------------------------------------------------------------------------
# Entradas del usuario
# ---------------------------------------------------------------------------

MESES_TEXTO = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def leer_mes() -> str | None:
    """Pregunta por un mes y lo interpreta."""
    texto = preguntar(
        "\n  Mes a conciliar (ejemplos: 2025-03, 03/2025, marzo 2025): "
    )
    return interpretar_mes(texto) if texto else None


def interpretar_mes(texto: str) -> str | None:
    """Acepta 2025-03, 03/2025, marzo 2025 o marzo de 2025."""
    if not texto:
        return None

    limpio = texto.strip().lower()

    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", limpio)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"

    m = re.fullmatch(r"(\d{1,2})[-/](\d{4})", limpio)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"

    m = re.fullmatch(r"([a-záéíóúñ]+)\s*(?:de\s*)?(\d{4})", limpio)
    if m and m.group(1) in MESES_TEXTO:
        return f"{int(m.group(2)):04d}-{MESES_TEXTO[m.group(1)]:02d}"

    print(f"\n  No entendí '{texto}'. Escríbalo como 2025-03 o 03/2025.")
    return None


def leer_fecha(mensaje: str) -> str | None:
    from conciliacion.normalizacion import parse_fecha

    texto = preguntar(mensaje)
    if not texto:
        return None
    fecha = parse_fecha(texto)
    if fecha is None:
        print(f"\n  No entendí la fecha '{texto}'. Escríbala como 01/03/2025.")
        return None
    return fecha.isoformat()


def leer_anio() -> int | None:
    texto = preguntar("\n  Año a conciliar (ejemplo: 2025): ")
    if re.fullmatch(r"(19|20)\d{2}", texto or ""):
        return int(texto)
    if texto:
        print(f"\n  '{texto}' no parece un año.")
    return None


# ---------------------------------------------------------------------------
# Archivos de entrada
# ---------------------------------------------------------------------------

def buscar_extractos() -> list[str]:
    from conciliacion.extraccion.texto import EXTENSIONES_SOPORTADAS

    if not os.path.isdir(CARPETA_EXTRACTOS):
        os.makedirs(CARPETA_EXTRACTOS, exist_ok=True)
        return []

    encontrados: list[str] = []
    for raiz, _, archivos in os.walk(CARPETA_EXTRACTOS):
        for nombre in sorted(archivos):
            if nombre.lower().startswith("leeme"):
                continue
            if os.path.splitext(nombre)[1].lower() in EXTENSIONES_SOPORTADAS:
                encontrados.append(os.path.join(raiz, nombre))
    return encontrados


def explicar_carpeta_vacia() -> None:
    titulo("NO HAY EXTRACTOS PARA LEER")
    print(f"""
  La carpeta de extractos está vacía:

      {CARPETA_EXTRACTOS}

  Qué hacer:

    1. Descargue los extractos de su banco (lo ideal es en PDF).
    2. Cópielos dentro de esa carpeta. Puede mezclar bancos y meses:
       el programa reconoce cada uno por su cuenta.
    3. Vuelva a hacer doble clic en CONCILIAR.bat

  Formatos que puede poner: PDF, JPG, PNG.
  Para las fotos e imágenes se necesita Tesseract OCR instalado
  (ver LEEME.txt). Un PDF siempre da mejor resultado que una foto.
""")
    if si_o_no("  ¿Quiere que le abra la carpeta ahora?"):
        abrir_carpeta(CARPETA_EXTRACTOS)


# ---------------------------------------------------------------------------
# Proceso principal
# ---------------------------------------------------------------------------

def clasificar_movimientos(movimientos: list) -> None:
    """Deduce concepto y tercero, aplicando las reglas propias del usuario."""
    from conciliacion.clasificacion import cargar_reglas, clasificar

    reglas, avisos = cargar_reglas(RUTA_REGLAS)
    for aviso in avisos:
        print(f"  {aviso}")
    clasificar(movimientos, reglas)


def leer_todos(rutas: list[str]):
    """Lee cada archivo informando el avance, sin abortar por uno malo."""
    from conciliacion.motor import analizar_archivo
    from conciliacion.resumen import unificar

    titulo(f"LEYENDO {len(rutas)} ARCHIVO(S)")
    extractos = []
    errores: list[tuple[str, str]] = []

    for numero, ruta in enumerate(rutas, start=1):
        nombre = os.path.basename(ruta)
        print(f"  [{numero}/{len(rutas)}] {nombre} ... ", end="", flush=True)
        try:
            extracto = analizar_archivo(ruta)
        except Exception as error:
            print("NO SE PUDO LEER")
            print(f"        {error}")
            errores.append((ruta, str(error)))
            continue
        extractos.append(extracto)
        print(f"{extracto.banco}, {len(extracto.movimientos)} movimientos")

    consolidado = unificar(extractos)
    consolidado.errores = errores
    return consolidado


def veredicto(resumenes: list, consolidado) -> None:
    """Le dice al usuario, en una frase, si puede confiar en el resultado.

    Se miran los dos niveles, porque pueden discrepar: un mes puede cuadrar
    con los movimientos leídos y sin embargo el archivo declarar un saldo
    anterior que no corresponde, señal de que se cargó una página suelta.
    """
    problemas = [r for r in resumenes if r.cuadra is False]
    sin_verificar = [
        r for r in resumenes if r.cuadra is None or r.saldo_inicial_deducido
    ]
    incompletos = [e for e in consolidado.extractos if e.cuadra is False]

    titulo("¿SE PUEDE CONFIAR EN ESTOS NÚMEROS?")
    if not problemas and not sin_verificar and not incompletos:
        print("""
  SÍ. Todos los meses cuadran exactamente: el saldo inicial más los
  ingresos menos los egresos da el saldo final que reporta el banco.
  Eso significa que no se quedó ningún movimiento sin leer.
""")
        return

    if not problemas:
        print("\n  SÍ, con una advertencia.\n")
        if incompletos:
            print("""  Estos archivos declaran un saldo que no corresponde a los movimientos
  que traen. Lo normal es que sea una página suelta de un extracto de
  varias páginas: los movimientos leídos están bien, pero le faltan los
  del resto del extracto.
""")
            from conciliacion.normalizacion import formato_cop

            for extracto in incompletos:
                print(
                    f"    - {os.path.basename(extracto.archivo)}: faltan "
                    f"{formato_cop(abs(extracto.diferencia_cuadre))} por explicar"
                )
            print("\n  Qué hacer: cargue el extracto completo de ese mes.\n")
        if sin_verificar:
            print("""  Y en estos meses no hay saldos con los que comprobar la lectura por
  aritmética (el extracto no los imprime):
""")
            for r in sin_verificar:
                print(f"    - {r.banco} {r.periodo}")
            print()
        return

    print("""
  HAY QUE REVISAR. En estos meses el saldo no cuadra, lo que casi siempre
  significa que faltó leer algún movimiento (por ejemplo, si cargó solo
  una página de un extracto de varias):
""")
    from conciliacion.normalizacion import formato_cop

    for r in problemas:
        print(
            f"    - {r.banco} {r.periodo}: diferencia de "
            f"{formato_cop(r.diferencia)}"
        )
    print("""
  Qué hacer: confirme que cargó el extracto completo de ese mes. Si el
  problema sigue, use la opción 6 del menú sobre ese archivo y compárteme
  el resultado para ajustar la lectura.
""")


def conciliar(
    desde: str | None = None,
    hasta: str | None = None,
    mes: str | None = None,
    anio: int | None = None,
    con_detalle: bool = False,
    con_informe: bool = False,
) -> None:
    from conciliacion.reportes import (
        exportar,
        reporte_advertencias,
        reporte_lectura,
        reporte_mensual_consolidado,
        reporte_movimientos,
        reporte_resumen_mensual,
        reporte_totales_banco,
    )
    from conciliacion.resumen import (
        Consolidado,
        filtrar,
        rango_de_periodo,
        resumen_mensual,
        totales_por_banco,
    )

    rutas = buscar_extractos()
    if not rutas:
        explicar_carpeta_vacia()
        return

    consolidado = leer_todos(rutas)
    if not consolidado.movimientos:
        titulo("NO SE RECONOCIÓ NINGÚN MOVIMIENTO")
        print("""
  Se leyeron los archivos pero no se pudo interpretar ningún movimiento.

  Las causas más comunes:
    - El PDF es una imagen escaneada y falta instalar Tesseract OCR.
    - El extracto tiene un formato distinto al esperado.

  Use la opción 6 del menú para ver qué está leyendo el programa.
""")
        return

    inicio, fin = rango_de_periodo(desde=desde, hasta=hasta, mes=mes, anio=anio)
    movimientos = filtrar(consolidado.movimientos, desde=inicio, hasta=fin)

    if not movimientos:
        titulo("SIN MOVIMIENTOS EN ESE PERIODO")
        disponibles = sorted({m.periodo for m in consolidado.movimientos})
        print("\n  No hay movimientos en el periodo que pidió.")
        print(f"  Los extractos que cargó cubren estos meses: {', '.join(disponibles)}")
        return

    clasificar_movimientos(movimientos)
    resumenes = resumen_mensual(movimientos, consolidado.extractos)
    totales = totales_por_banco(movimientos, resumenes)

    titulo("ARCHIVOS PROCESADOS")
    print(reporte_lectura(consolidado))

    advertencias = reporte_advertencias(consolidado)
    if advertencias:
        titulo("PUNTOS A REVISAR")
        print(advertencias)

    titulo("RESUMEN MENSUAL POR BANCO")
    print(reporte_resumen_mensual(resumenes))

    titulo("TOTALES POR BANCO EN EL PERIODO")
    print(reporte_totales_banco(totales))

    if len({r.banco for r in resumenes}) > 1:
        titulo("CONSOLIDADO MES A MES (TODOS LOS BANCOS)")
        print(reporte_mensual_consolidado(resumenes))

    if con_informe:
        from conciliacion.reportes import (
            reporte_concepto_y_tercero,
            reporte_por_concepto,
            reporte_terceros,
        )

        titulo("EN QUÉ SE FUE LA PLATA (POR CONCEPTO)")
        print(reporte_por_concepto(movimientos))

        titulo("DETALLE POR CONCEPTO Y TERCERO")
        print(reporte_concepto_y_tercero(movimientos))

        titulo("CONSOLIDADO POR TERCERO")
        print(reporte_terceros(movimientos, limite=40))

        print(f"""
  ¿Un tercero quedó con el nombre incompleto, o un movimiento en el concepto
  equivocado? Se corrige en este archivo, y desde ahí el programa lo respeta:

      {RUTA_REGLAS}

  Se edita con el Bloc de notas o con Excel. Cada línea es:
      texto que aparece en el extracto;nombre del tercero;concepto
""")

    if con_detalle:
        titulo("DETALLE DE MOVIMIENTOS")
        print(reporte_movimientos(movimientos))

    veredicto(resumenes, consolidado)

    # Archivos de salida
    os.makedirs(CARPETA_REPORTES, exist_ok=True)
    filtrado = Consolidado(
        movimientos=movimientos,
        extractos=consolidado.extractos,
        duplicados=consolidado.duplicados,
        errores=consolidado.errores,
    )
    generados = exportar(
        CARPETA_REPORTES,
        consolidado=filtrado,
        resumenes=resumenes,
        totales=totales,
        formatos=["csv", "xlsx"],
    )

    titulo("ARCHIVOS GENERADOS")
    for ruta in generados:
        print(f"  {os.path.basename(ruta)}")
    print(f"\n  Carpeta: {CARPETA_REPORTES}")

    if not any(r.endswith(".xlsx") for r in generados):
        print("""
  Nota: no se generó el archivo de Excel porque falta un componente.
  Haga doble clic en INSTALAR.bat una sola vez y vuelva a intentar.
  (Los CSV sí se generaron y Excel los abre sin problema.)""")

    if si_o_no("\n  ¿Abrir la carpeta de reportes?"):
        abrir_carpeta(CARPETA_REPORTES)


def revisar_archivo() -> None:
    """Opción de diagnóstico, para cuando un extracto no se lee bien."""
    from conciliacion.cli import comando_diagnostico

    rutas = buscar_extractos()
    if not rutas:
        explicar_carpeta_vacia()
        return

    titulo("REVISAR UN EXTRACTO EN DETALLE")
    print("\n  ¿Cuál archivo quiere revisar?\n")
    for numero, ruta in enumerate(rutas, start=1):
        print(f"    [{numero}] {os.path.basename(ruta)}")

    eleccion = preguntar("\n  Número del archivo: ")
    if not eleccion.isdigit() or not (1 <= int(eleccion) <= len(rutas)):
        print("\n  Opción no válida.")
        return

    ruta = rutas[int(eleccion) - 1]
    ver_texto = si_o_no("  ¿Mostrar también todo el texto extraído?", defecto=False)

    class Opciones:
        archivos = [ruta]
        banco = None
        anio_defecto = None
        dpi = 300
        idioma = "spa"
        forzar_ocr = False
        texto = ver_texto

    comando_diagnostico(Opciones())
    print("""
  Si algo se ve mal en este reporte, cópielo y compártalo: con eso se
  ajusta la lectura de ese formato de extracto.
""")


def revisar_instalacion() -> None:
    from conciliacion.cli import comando_entorno

    comando_entorno(None)


# ---------------------------------------------------------------------------
# Menú
# ---------------------------------------------------------------------------

def menu() -> bool:
    """Muestra el menú. Devuelve False cuando el usuario quiere salir."""
    rutas = buscar_extractos()

    print()
    linea()
    print("   CONCILIACIÓN BANCARIA")
    print("   Bancolombia  ·  Nequi  ·  AV Villas  ·  Banco de Bogotá")
    linea()

    if rutas:
        print(f"\n  Extractos en 1-EXTRACTOS: {len(rutas)} archivo(s)")
        for ruta in rutas[:8]:
            print(f"     - {os.path.basename(ruta)}")
        if len(rutas) > 8:
            print(f"     ... y {len(rutas) - 8} más")
    else:
        print("\n  La carpeta 1-EXTRACTOS está vacía.")

    print("""
  ¿Qué quiere hacer?

    [1] Conciliar todo lo que haya en la carpeta
    [2] Conciliar un mes en particular
    [3] Conciliar un rango de fechas
    [4] Conciliar un año completo

    [5] INFORME DETALLADO: en qué se fue la plata y con quién
        (IVA, comisiones, nómina, proveedores, quién consignó...)

    [6] Ver el detalle de todos los movimientos
    [7] Revisar un extracto que no se leyó bien
    [8] Ver qué componentes están instalados

    [0] Salir
""")

    opcion = preguntar("  Opción: ")

    if opcion == "1":
        conciliar()
    elif opcion == "2":
        mes = leer_mes()
        if mes:
            conciliar(mes=mes)
    elif opcion == "3":
        desde = leer_fecha("\n  Fecha inicial (DD/MM/AAAA): ")
        if not desde:
            return True
        hasta = leer_fecha("  Fecha final   (DD/MM/AAAA): ")
        if not hasta:
            return True
        conciliar(desde=desde, hasta=hasta)
    elif opcion == "4":
        anio = leer_anio()
        if anio:
            conciliar(anio=anio)
    elif opcion == "5":
        texto = preguntar(
            "\n  ¿De qué periodo? (ENTER = todo, o escriba un mes como 2025-03): "
        )
        if not texto:
            conciliar(con_informe=True)
        else:
            mes = interpretar_mes(texto)
            if mes:
                conciliar(mes=mes, con_informe=True)
    elif opcion == "6":
        conciliar(con_detalle=True)
    elif opcion == "7":
        revisar_archivo()
    elif opcion == "8":
        revisar_instalacion()
    elif opcion == "0":
        return False
    else:
        print("\n  Escoja una de las opciones del menú.")
        return True

    pausa("Presione ENTER para volver al menú...")
    return True


def revisar_version_python() -> bool:
    """Avisa en español si la versión de Python es muy vieja."""
    if sys.version_info >= (3, 10):
        return True
    actual = ".".join(str(n) for n in sys.version_info[:3])
    titulo("LA VERSIÓN DE PYTHON ES MUY ANTIGUA")
    print(f"""
  Este programa necesita Python 3.10 o superior.
  El que está instalado es el {actual}.

  Qué hacer:
    1. Abra https://www.python.org/downloads/
    2. Descargue e instale la versión más reciente para Windows.
    3. Marque la casilla "Add python.exe to PATH" en el instalador.
    4. Vuelva a hacer doble clic en CONCILIAR.bat
""")
    pausa()
    return False


def main() -> int:
    if not revisar_version_python():
        return 1

    os.makedirs(CARPETA_EXTRACTOS, exist_ok=True)
    os.makedirs(CARPETA_REPORTES, exist_ok=True)

    try:
        import conciliacion  # noqa: F401
        from conciliacion.clasificacion import crear_plantilla_reglas

        crear_plantilla_reglas(RUTA_REGLAS)
    except ImportError:
        print("""
  No encuentro los archivos del programa.

  Este archivo (asistente.py) debe quedar en la misma carpeta que la
  carpeta 'conciliacion'. Si descomprimió el ZIP, asegúrese de haber
  sacado toda la carpeta y no solo algunos archivos.
""")
        return 1

    try:
        while menu():
            pass
    except KeyboardInterrupt:
        print("\n\n  Cancelado.")
        return 130
    except Exception:
        titulo("OCURRIÓ UN ERROR INESPERADO")
        print()
        traceback.print_exc()
        print("""
  Copie el texto de arriba y compártalo para poder corregirlo.
""")
        pausa()
        return 1

    print("\n  Hasta luego.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

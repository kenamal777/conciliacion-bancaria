# Conciliación bancaria — Bancolombia, Nequi, AV Villas y Banco de Bogotá

Lee extractos bancarios en **PDF, JPG o PNG**, extrae los movimientos y entrega
el **resumen mensual por banco**, con filtros por cualquier periodo de tiempo.
Opcionalmente compara los extractos contra el **libro auxiliar** de
contabilidad y arma la conciliación formal.

## Uso en Windows, sin escribir comandos

Es la forma recomendada si no trabaja con la terminal. Guía completa para el
usuario final en **[LEEME.txt](LEEME.txt)**.

```
ConciliacionBancaria/
   INSTALAR.bat        <- doble clic una sola vez
   CONCILIAR.bat       <- doble clic cada vez que quiera conciliar
   1-EXTRACTOS/        <- aquí se copian los PDF o fotos del banco
   2-REPORTES/         <- aquí aparecen los Excel y CSV
```

1. Instalar [Python](https://www.python.org/downloads/) marcando la casilla
   **"Add python.exe to PATH"** durante la instalación.
2. Doble clic en `INSTALAR.bat` (una sola vez, agrega lectura precisa de PDF y
   exportación a Excel).
3. Copiar los extractos en `1-EXTRACTOS`, mezclados sin orden: el programa
   reconoce cada banco, cada cuenta y cada mes por su cuenta.
4. Doble clic en `CONCILIAR.bat` y escoger el periodo en el menú:

```
    [1] Conciliar todo lo que haya en la carpeta
    [2] Conciliar un mes en particular          (2025-03, 03/2025 o marzo 2025)
    [3] Conciliar un rango de fechas
    [4] Conciliar un año completo
    [5] INFORME DETALLADO: en qué se fue la plata y con quién
    [6] Ver el detalle de todos los movimientos
    [7] Revisar un extracto que no se leyó bien
    [8] Ver qué componentes están instalados
```

Al terminar, el asistente dice en una frase si se puede confiar en las cifras:

```
  ¿SE PUEDE CONFIAR EN ESTOS NÚMEROS?

  SÍ. Todos los meses cuadran exactamente: el saldo inicial más los
  ingresos menos los egresos da el saldo final que reporta el banco.
  Eso significa que no se quedó ningún movimiento sin leer.
```

Y cuando algo no cuadra, explica qué revisar en lugar de mostrar un error
técnico:

```
  SÍ, con una advertencia.

  Estos archivos declaran un saldo que no corresponde a los movimientos
  que traen. Lo normal es que sea una página suelta de un extracto de
  varias páginas:

    - nequi_octubre.pdf: faltan 863.302,12 por explicar

  Qué hacer: cargue el extracto completo de ese mes.
```

Los extractos nunca salen del computador, y `.gitignore` está configurado para
que el contenido de `1-EXTRACTOS` y `2-REPORTES` no se suba nunca al
repositorio.

## Instalación para usarlo por línea de comandos

Se necesita Python 3.10 o superior.

```bash
python -m pip install -r requirements.txt
```

Todas las dependencias son opcionales: el programa trae su propio lector de PDF
y funciona sin instalar nada. Cada paquete agrega precisión o formatos.

Para leer **imágenes (JPG/PNG)** y **PDF escaneados** se necesita además el
programa Tesseract OCR:

| Sistema | Comando |
|---|---|
| Windows | Instalador de [Tesseract UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) (marcar el idioma español) |
| macOS | `brew install tesseract tesseract-lang` |
| Ubuntu/Debian | `sudo apt install tesseract-ocr tesseract-ocr-spa` |

Para verificar qué quedó instalado:

```bash
python -m conciliacion entorno
```

## Uso

Poner todos los extractos en una carpeta (mezclados, de cualquier banco y de
cualquier mes) y correr:

```bash
python -m conciliacion resumen extractos/
```

### Resumen mensual por banco

```
RESUMEN MENSUAL POR BANCO  (todo el periodo disponible)
Mes         Banco            Cuenta         Mov.  Saldo inicial      Ingresos       Egresos        Neto   Saldo final  Cuadre
Marzo 2025  Bancolombia      123-456789-01    10   5.000.000,00  5.021.400,00  4.399.650,00  621.750,00  5.621.750,00  OK
Marzo 2025  Nequi            3001234567        8     690.000,00  1.090.000,00    530.000,00  560.000,00  1.250.000,00  s/verificar
Abril 2025  Banco AV Villas  987654-321        8   2.000.000,00  2.570.000,00  1.851.876,00  718.124,00  2.718.124,00  OK
Abril 2025  Banco de Bogotá  445566778         7   8.000.000,00  5.565.400,00  4.757.000,00  808.400,00  8.808.400,00  OK
```

La columna **Cuadre** es el control de calidad de la lectura:

| Valor | Significado |
|---|---|
| `OK` | Saldo inicial + ingresos − egresos da exactamente el saldo final del extracto. La lectura está completa. |
| `DIF x` | Falta o sobra algo por ese valor. Hay que revisar el archivo con `diagnostico`. |
| `s/saldos` | El extracto no trae saldos, no se puede verificar aritméticamente. |
| `s/verificar` | El saldo inicial se despejó restando los movimientos, así que el cuadre no prueba nada. |

### Filtros por periodo

```bash
python -m conciliacion resumen extractos/ --mes 2025-03
python -m conciliacion resumen extractos/ --anio 2025
python -m conciliacion resumen extractos/ --desde 2025-01-01 --hasta 2025-06-30
python -m conciliacion resumen extractos/ --desde 01/04/2025 --hasta 15/04/2025
```

Otros filtros: `--solo-banco nequi` (se puede repetir), `--cuenta 456789`,
`--contiene consignacion`.

### Exportar a Excel y CSV

```bash
python -m conciliacion resumen extractos/ --salida reportes/
```

Genera un Excel con seis hojas (resumen mensual, por concepto, concepto y
tercero, terceros, movimientos y control de archivos leídos) más los CSV
equivalentes. El Excel requiere `openpyxl`; los CSV
salen con `;` y montos en formato colombiano para que Excel en español los abra
bien.

### Informe detallado por concepto y tercero

```bash
python -m conciliacion informe extractos/ --mes 2025-03
```

El resumen mensual dice cuánto entró y cuánto salió. Esto responde en qué se
fue la plata y con quién se movió:

```
EGRESOS  (a quién se le pagó)
Concepto / Tercero             Mov.          Total  % del total  Fechas
Nómina                            3  17.622.449,26        83,4%
    NOVD AUTOM.SISTEMAS           3  17.622.449,26               01/12/2023 a 06/12/2023
Proveedores                       2   2.463.177,00        11,7%
    SERVIEQUIPOS                  1   1.561.070,00               01/02/2024
    AGUAS INGENIE                 1     902.107,00               01/02/2024
GMF (4x1000)                     13      27.583,92         0,1%
    (el propio banco)            13      27.583,92               02/07/2020 a 02/02/2024
Comisiones y cuotas de manejo     5      23.097,24         0,1%
IVA                               2       4.340,04         0,0%
```

Conceptos que reconoce: IVA, GMF (4x1000), retención en la fuente, comisiones
y cuotas de manejo, nómina, proveedores, servicios públicos, impuestos,
seguros, obligaciones financieras, cheques, retiros, compras, transferencias,
consignaciones, intereses y cargos del banco.

El nombre del tercero se deduce del texto de la descripción, que es lo único
que da el banco. Un mismo tercero escrito de varias formas se unifica de forma
conservadora: solo si las palabras de un nombre están contenidas en el otro
("SERVIEQUIPOS" dentro de "SERVIEQUIPOS INDUSTRIALES"). Unificar de más sería
peor que de menos, porque juntaría la plata de dos terceros distintos, así que
el informe siempre muestra cuántas variantes agrupó.

#### Corregir la clasificación: `terceros.csv`

El banco no entrega el concepto ni el tercero en campos aparte, así que
deducirlos es una heurística. Cuando no acierte, manda el usuario:

```csv
# patron;tercero;concepto
SONIA BLANCO;SONIA BLANCO RAMIREZ;Nomina
SERVIEQUIPOS;SERVIEQUIPOS INDUSTRIALES SAS;Proveedores
CLIENTE ABC;COMERCIAL ABC LTDA;Consignaciones y recaudos
```

Gana la primera regla que coincida, no distingue tildes ni mayúsculas, acepta
expresiones regulares, y las dos últimas columnas son opcionales. Se busca por
defecto como `terceros.csv` en la carpeta actual, o con `--reglas ruta.csv`.

### Detalle de movimientos

```bash
python -m conciliacion movimientos extractos/ --mes 2025-03 --salida detalle.csv
```

### Conciliación contra el libro auxiliar

```bash
python -m conciliacion conciliar extractos/ --libro auxiliar.csv --saldo-libros 4879650
```

El libro auxiliar puede ser CSV o Excel. Se reconocen los títulos de columna
más comunes (`Fecha`, `Descripción`/`Detalle`/`Concepto`, `Valor` o
`Débito`/`Crédito`, `Documento`/`Comprobante`, `Banco`). Ejemplo:

```csv
Fecha;Comprobante;Descripcion;Debito;Credito
03/03/2025;RC-001;Consignacion cliente ABC;3.200.000,00;
05/03/2025;CE-045;Pago nomina;;2.300.000,00
```

Convención de signos: **débito = entra plata** al banco, **crédito = sale**.
Si el libro usa lo contrario, agregar `--invertir-signo` (el programa lo detecta
y lo avisa).

La salida separa las tres categorías clásicas y cierra el cuadre por los dos
lados:

```
Saldo según extracto bancario                    5.621.750,00
(+/-) Partidas de libros pendientes en el banco   -750.000,00   <- cheques sin cobrar
= Saldo conciliado (vía banco)                   4.871.750,00

Saldo según libros                               4.879.650,00
(+/-) Partidas del banco no registradas             -7.900,00   <- GMF, comisiones
= Saldo conciliado (vía libros)                  4.871.750,00

DIFERENCIA                                               0,00
Estado                                             CONCILIADO
```

### Cuando un extracto no se lee bien

```bash
python -m conciliacion diagnostico extracto_raro.pdf --texto
```

Muestra el motor de lectura usado, el banco detectado, los movimientos
reconocidos, las líneas con valores que no se pudieron interpretar y, con
`--texto`, todo el texto extraído. Es la herramienta para ajustar los perfiles.

## Cómo decide si un movimiento es ingreso o egreso

Este es el punto crítico, porque los extractos no siempre lo dicen de forma
explícita. Se usan cinco señales en orden de confiabilidad, y cada movimiento
queda marcado con la que se usó (columna `Origen signo`):

1. **`saldo`** — la diferencia contra el saldo de la fila anterior. Es
   aritmética, no admite error.
2. **`columna`** — el número cayó bajo la columna "Débitos" o "Créditos".
3. **`signo`** — el valor traía `-`, paréntesis o el sufijo `DB`.
4. **`marca`** — el banco marcó la naturaleza en la descripción. El `CRE`/`DEB`
   de AV Villas es un ejemplo: `CRE PAGO PROVEEDOR` es una **entrada** de plata
   aunque diga "pago", y `Cargo omision consignacion` en Banco de Bogotá es una
   **salida** aunque diga "consignación".
5. **`palabras`** — se dedujo de la descripción ("consignación" vs "retiro").
   Es la única heurística real; el programa avisa cuántos movimientos quedaron
   así para que se revisen.

Además se valida el total leído contra los totales que imprime el propio
extracto ("Total abonos", "Total cargos", "Movimiento crédito/débito",
"Entradas", "Salidas") y se avisa si no coinciden.

## Estructura real de cada banco

Cada perfil se ajustó contra un extracto real. Estas son las diferencias que
importan:

| Banco | Columnas | Signo | Fechas |
|---|---|---|---|
| **Bancolombia** | `VALOR` + `SALDO` | menos adelante: `-1,561,070.00` | `1/02` (sin año) |
| **Nequi** | `Valor` + `Saldo` | menos adelante | `29/10/2021`, **filas del más reciente al más antiguo** |
| **AV Villas** | solo `VALOR` | prefijo `CRE` / `DEB` | `2023/12/01` |
| **Banco de Bogotá** | `Valor` + `Saldo` | menos adelante | `01/07` + año del encabezado |

Los cuatro usan cifras en formato anglosajón (`1,234,567.89`), no el
colombiano. El programa acepta ambos.

## Detalles que ya están resueltos

- Montos en formato anglosajón (`1,234,567.89`) y colombiano (`1.234.567,89`),
  con el menos adelante, atrás, entre paréntesis o como sufijo `DB`.
- Fechas `dd/mm/aaaa`, `aaaa/mm/dd`, `d/mm`, `12 de marzo de 2025`, `15-ABR-2025`.
- Extractos que solo traen día y mes: el año sale del periodo del extracto,
  incluso cuando el periodo va de diciembre a enero.
- **Extractos que listan del más reciente al más antiguo** (Nequi). El sentido
  se detecta probando los dos y quedándose con el que valide más saldos:
  aplicar el sentido equivocado invertiría el signo de todo.
- **Montos dentro de la descripción**: `Consignacion nacional (10,000.00 en
  efectivo $0.00 en cheque)` no se confunde con el valor del movimiento. La
  regla es estructural: si después del monto todavía hay palabras, el monto es
  parte del texto.
- Movimientos partidos en dos líneas cuando la descripción es larga.
- Cuadros de resumen lado a lado: de `Saldo inicial 7,954,713.11  Saldo
  promedio 8,149,222.10` se toma el valor que sigue a cada etiqueta, no el
  último de la línea.
- Extractos que se traslapan: los repetidos se cuentan una sola vez, pero las
  transacciones legítimamente iguales (dos envíos de $100.000 a la misma
  persona el mismo día) se conservan como dos movimientos.
- Números de documento, oficina, código de transacción y referencia separados
  de la descripción.
- Números de celular y cédulas descartados para que no se lean como montos.

## Limitaciones

- Los perfiles se ajustaron contra un extracto real de cada banco. Los formatos
  cambian entre productos y años; si algo no cuadra, `diagnostico` muestra qué
  se leyó y los perfiles se ajustan en `conciliacion/perfiles.py`
  (expresiones regulares, sin tocar el motor).
- La lógica que usa la posición de las columnas solo se activa si el texto
  extraído conservó el alineado. Cuando no, el programa usa la estructura de la
  fila, que es más robusta pero no distingue columnas de débito y crédito
  separadas; en ese caso el signo sale del saldo o de la descripción.
- El OCR de imágenes nunca es perfecto: cuando se usa OCR el programa lo avisa
  y conviene revisar las cifras. Una foto derecha, con buena luz y sin sombras
  mejora mucho el resultado; un PDF siempre es preferible a una foto.
- Extractos con varias cuentas en un mismo archivo: los movimientos se asignan
  a la primera cuenta detectada.

## Estructura del proyecto

```
LEEME.txt               Guía para el usuario final (Windows, sin comandos)
CONCILIAR.bat           Lanzador de doble clic
INSTALAR.bat            Instalación de componentes, una sola vez
asistente.py            Menú interactivo en español
1-EXTRACTOS/            Entrada: los extractos del banco
2-REPORTES/             Salida: los Excel y CSV generados

terceros.csv            Sus reglas para corregir terceros y conceptos

conciliacion/
  normalizacion.py      Montos y fechas colombianas
  clasificacion.py      Concepto y tercero de cada movimiento
  modelos.py            Movimiento, Extracto, ResumenMensual
  perfiles.py           Reglas de cada banco (aquí se ajusta lo específico)
  motor.py              Texto -> movimientos, y decisión de ingreso/egreso
  resumen.py            Filtros de periodo, resumen mensual, totales
  reportes.py           Consola, CSV y Excel
  libro.py              Conciliación contra el libro auxiliar
  cli.py                Comandos de la línea de comandos
  extraccion/
    texto.py            Elige el motor de lectura (PDF, OCR)
    pdf_basico.py       Lector de PDF propio, sin dependencias
pruebas/
  probar_todo.py        216 verificaciones de extremo a extremo
  util_pdf.py           Genera PDF de prueba
  datos/                Extractos de ejemplo de los cuatro bancos
    *_real_*.txt        Réplicas de extractos reales (las que importan)
```

## Pruebas

```bash
python pruebas/probar_todo.py
```

216 verificaciones sobre réplicas fieles de extractos reales de los cuatro
bancos, con cifras que deben cuadrar al centavo. Cubren la normalización, la
detección de banco, el orden invertido de Nequi, los montos dentro de la
descripción de Banco de Bogotá, el `CRE`/`DEB` de AV Villas, los resúmenes y
filtros por periodo, los duplicados, el cambio de año, la clasificación por
concepto y tercero, la conciliación completa y la exportación. Entre ellas hay
una invariante clave: la suma de todos los conceptos tiene que dar exactamente
los mismos ingresos y egresos que el resumen mensual. Además comprueban que leer el PDF dé exactamente el mismo
resultado que leer el texto.

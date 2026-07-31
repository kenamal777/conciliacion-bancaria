@echo off
REM ===================================================================
REM  INSTALACION DE COMPONENTES - se ejecuta UNA SOLA VEZ
REM  Agrega: lectura precisa de PDF, exportacion a Excel y soporte OCR.
REM ===================================================================

chcp 65001 >nul 2>nul
title Instalacion - Conciliacion Bancaria
cd /d "%~dp0"

echo ===================================================================
echo   INSTALACION DE COMPONENTES
echo ===================================================================
echo.
echo  Esto se hace una sola vez y necesita conexion a internet.
echo  Agrega lectura precisa de PDF y exportacion a Excel.
echo.

set PYEXE=
where py >nul 2>nul && set PYEXE=py -3
if not defined PYEXE (
    where python >nul 2>nul && set PYEXE=python
)
if not defined PYEXE goto sin_python

echo  Python encontrado. Instalando...
echo.

%PYEXE% -m pip install --upgrade pip
%PYEXE% -m pip install -r "%~dp0requirements.txt"

if errorlevel 1 (
    echo.
    echo ===================================================================
    echo   LA INSTALACION FALLO
    echo ===================================================================
    echo.
    echo  Causas mas comunes:
    echo    - Sin conexion a internet.
    echo    - La red de la empresa bloquea la descarga.
    echo.
    echo  No es grave: el programa FUNCIONA IGUAL sin esto, porque trae su
    echo  propio lector de PDF. Solo perderia el archivo de Excel; los CSV
    echo  se generan siempre y Excel los abre sin problema.
    echo.
    pause
    exit /b 1
)

echo.
echo ===================================================================
echo   ESTADO DE LOS COMPONENTES
echo ===================================================================
%PYEXE% -m conciliacion entorno

echo.
echo ===================================================================
echo  Listo. Ya puede cerrar esta ventana y usar CONCILIAR.bat
echo ===================================================================
echo.
echo  NOTA SOBRE FOTOS Y PDF ESCANEADOS:
echo  Si va a leer imagenes (JPG/PNG) o PDF escaneados, hace falta
echo  instalar aparte el programa Tesseract OCR desde:
echo    https://github.com/UB-Mannheim/tesseract/wiki
echo  Durante su instalacion marque el idioma Spanish.
echo  Para extractos en PDF normales NO hace falta.
echo.
pause
exit /b 0


:sin_python
echo.
echo   FALTA INSTALAR PYTHON
echo.
echo   1. Abra:  https://www.python.org/downloads/
echo   2. Instale la version para Windows.
echo   3. IMPORTANTE: marque la casilla "Add python.exe to PATH".
echo   4. Cierre esta ventana y vuelva a ejecutar INSTALAR.bat
echo.
pause
exit /b 1

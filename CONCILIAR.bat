@echo off
REM ===================================================================
REM  CONCILIACION BANCARIA - haga doble clic en este archivo
REM  Este archivo solo abre el asistente; toda la logica esta en Python.
REM  Sin acentos a proposito: evita problemas de codificacion en Windows.
REM ===================================================================

chcp 65001 >nul 2>nul
title Conciliacion Bancaria
cd /d "%~dp0"

REM --- Buscar Python: primero el lanzador "py", luego "python" ---
set PYEXE=
where py >nul 2>nul && set PYEXE=py -3
if not defined PYEXE (
    where python >nul 2>nul && set PYEXE=python
)
if not defined PYEXE goto sin_python

REM --- Verificar que exista el asistente ---
if not exist "%~dp0asistente.py" goto sin_asistente

%PYEXE% "%~dp0asistente.py"
if errorlevel 1 (
    echo.
    echo El programa termino con un error. Revise el mensaje de arriba.
    pause
)
exit /b 0


:sin_python
echo.
echo ===================================================================
echo   FALTA INSTALAR PYTHON
echo ===================================================================
echo.
echo  Este programa necesita Python, que no viene con Windows.
echo  Es gratis y se instala una sola vez.
echo.
echo  1. Abra:  https://www.python.org/downloads/
echo  2. Descargue la version para Windows y ejecute el instalador.
echo  3. IMPORTANTE: en la primera pantalla del instalador marque la
echo     casilla "Add python.exe to PATH" (abajo). Es el paso que mas
echo     se olvida y sin el esto no funciona.
echo  4. Termine la instalacion, cierre esta ventana y vuelva a hacer
echo     doble clic en CONCILIAR.bat
echo.
pause
exit /b 1


:sin_asistente
echo.
echo ===================================================================
echo   ARCHIVOS INCOMPLETOS
echo ===================================================================
echo.
echo  No encuentro el archivo asistente.py en esta carpeta.
echo.
echo  Si descargo un ZIP, asegurese de haberlo descomprimido completo
echo  (clic derecho sobre el ZIP - "Extraer todo") y de ejecutar el
echo  CONCILIAR.bat que quedo dentro de la carpeta extraida.
echo.
pause
exit /b 1

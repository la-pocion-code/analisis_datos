@echo off
rem ===========================================================================
rem respaldo_local.cmd - lo que ejecuta la tarea programada "Pocion ETL respaldo".
rem Corre respaldo_local.py con el Python del venv del repo, desde el repo
rem (ahi estan el .env y los logs). Ver la cabecera de respaldo_local.py.
rem
rem Instalar (cada 30 min, solo con la sesion abierta):
rem   schtasks /Create /SC MINUTE /MO 30 /TN "Pocion ETL respaldo" /TR "\"D:\Desktop\analisis_datos\respaldo_local.cmd\"" /F
rem Apagar / encender / ver / quitar:
rem   schtasks /Change /TN "Pocion ETL respaldo" /Disable
rem   schtasks /Change /TN "Pocion ETL respaldo" /Enable
rem   schtasks /Query  /TN "Pocion ETL respaldo" /V /FO LIST
rem   schtasks /Delete /TN "Pocion ETL respaldo" /F
rem ===========================================================================
cd /d "%~dp0"
if not exist "%~dp0logs" mkdir "%~dp0logs"
"%~dp0virtual-env\Scripts\python.exe" "%~dp0respaldo_local.py" %* >> "%~dp0logs\respaldo_local.salida.log" 2>&1

@echo off
REM GRABIT GEX - dubbelklicka for dagens nivaer (NQ + GC)
cd /d "%~dp0"
python gex_cli.py
echo.
echo Markera raden efter "Klistra in i indikatorn", hogerklicka = kopiera.
pause

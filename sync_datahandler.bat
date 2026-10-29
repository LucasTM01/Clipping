@echo off
REM ===========================================================================
REM  Clipping - Sync com o DataHandler (double-click entry point)
REM  -------------------------------------------------------------------------
REM  Publica a base de noticias (output/news_database.db) no data lake do
REM  DataHandler no Google Drive, sem rodar o pipeline inteiro. Util para
REM  forcar uma atualizacao fora do horario agendado ou apos rotular artigos
REM  no Streamlit. Resolve o repo root a partir da localizacao deste arquivo
REM  (%~dp0), entao funciona de qualquer lugar.
REM ===========================================================================
setlocal
chcp 65001 > nul
title Clipping - Sync DataHandler

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERRO] Ambiente virtual nao encontrado em ".venv\Scripts\python.exe".
    echo         Rode o setup: python -m venv .venv ^&^& pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m data.drive_sync
set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE% neq 0 (
    echo [ERRO] O sync terminou com codigo %EXITCODE%.
    pause
) else (
    pause
)

endlocal
exit /b %EXITCODE%

@echo off
rem Abre o site de producao ANP no Windows. De dois cliques neste arquivo.
rem Na primeira vez instala o que falta (o uv cuida do Python), depois so abre.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

call :achar_uv
if not defined UV (
  echo.
  echo Falta o uv, que instala o Python e as bibliotecas do projeto.
  echo Ele vem do instalador oficial ^(astral.sh^) e fica na pasta do seu usuario.
  echo.
  choice /c SN /m "Instalar o uv agora"
  if errorlevel 2 goto sem_uv
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  call :achar_uv
)
if not defined UV goto sem_uv

echo Preparando o ambiente (so demora na primeira vez)...
"%UV%" sync --quiet
if errorlevel 1 (
  echo.
  echo Falhou ao instalar as dependencias. Veja a mensagem acima.
  pause
  exit /b 1
)
"%UV%" run --quiet anp servir
pause
exit /b 0

:achar_uv
set "UV="
for %%c in (uv.exe) do if not "%%~$PATH:c"=="" set "UV=%%~$PATH:c"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined UV if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "UV=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
exit /b 0

:sem_uv
echo.
echo Sem o uv nao da para continuar. Instale com um destes comandos e abra este arquivo de novo.
echo     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
echo     winget install --id=astral-sh.uv -e
echo.
pause
exit /b 1

@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

where python >nul 2>&1
if errorlevel 1 (
  echo 没有找到 python。请先安装 Python，并把它加入 PATH。
  pause
  exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
  echo 没有找到 npm。请先安装 Node.js，网页端需要它。
  pause
  exit /b 1
)

echo 服务端  http://127.0.0.1:8000
echo 网页端  http://127.0.0.1:5173

call :listen 8000
if errorlevel 1 start "fastCut API" /D "%ROOT%" cmd /k python main.py serve --host 127.0.0.1 --port 8000

call :listen 5173
if errorlevel 1 start "fastCut Web" /D "%ROOT%\frontend" cmd /k "(if not exist node_modules npm install) && npm run dev"

call :wait 8000
if errorlevel 1 goto fail
call :wait 5173
if errorlevel 1 goto fail
start "" http://127.0.0.1:5173
exit /b 0

:fail
echo 启动超时。请查看 fastCut API 和 fastCut Web 窗口里的报错。
pause
exit /b 1

:listen
netstat -ano | findstr ":%~1 " | findstr "LISTENING" >nul
if errorlevel 1 exit /b 1
exit /b 0

:wait
set /a _n=0
:wait_loop
call :listen %~1
if not errorlevel 1 exit /b 0
set /a _n+=1
if %_n% geq 90 exit /b 1
timeout /t 1 /nobreak >nul
goto wait_loop

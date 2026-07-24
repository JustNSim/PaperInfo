"""
PaperInfo 后台运行入口（由 Windows 任务计划程序调用）

与直接运行 app.py 的区别：
- 强制 debug=False、关闭 reloader，避免派生子进程导致托管混乱
- pythonw 下 sys.stdout/sys.stderr 为 None，先重定向到 logs/server.log，
  否则 scheduler.py 的 logging.StreamHandler 和 print 会抛 AttributeError

   • 以后改 .env（换 key、调参数）必须重启服务才生效。我加了 scripts/restart_server.ps1，以后在管理员或普
     通 PowerShell 里跑一条即可：                                                                        
     ```powershell                                                                                       
       powershell -ExecutionPolicy Bypass -File scripts\restart_server.ps1                               
     ``` 
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.stdout is None or sys.stderr is None:
    os.makedirs(os.path.join(BASE_DIR, 'logs'), exist_ok=True)
    log_file = open(
        os.path.join(BASE_DIR, 'logs', 'server.log'),
        'a', encoding='utf-8', buffering=1
    )
    sys.stdout = log_file
    sys.stderr = log_file

os.chdir(BASE_DIR)

import app  # noqa: E402

# app 模块被导入时不会启动后台线程；后台入口显式启动调度器。
app.setup_scheduler(app.app)

if __name__ == '__main__':
    app.app.run(
        host=app.app.config['HOST'],
        port=app.app.config['PORT'],
        debug=False,
        use_reloader=False,
    )

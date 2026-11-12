import os

# 容器内需要被同一 docker 网络中的 nginx 访问，所以绑定 0.0.0.0。
# 该端口不在 docker-compose 里对外发布，宿主机无法直接访问。
bind = "0.0.0.0:5000"

# 现有任务线程与内嵌知识库运行时要求单进程：多 worker 会让同一任务
# 被重复调度，并让内嵌知识库在多个进程里各自初始化一份。
workers = 1
worker_class = "gthread"
threads = int(os.getenv("GUNICORN_THREADS", "8"))
preload_app = False
reload = False
max_requests = 0

# AI 任务是长轮询+子进程执行，默认 30s 会在生成中途被杀。
timeout = int(os.getenv("GUNICORN_TIMEOUT", "3600"))
graceful_timeout = 120
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
capture_output = True

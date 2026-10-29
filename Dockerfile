# 柴米记账 — Docker 镜像
# 数据落盘统一指向 /data（挂载卷），与源码目录解耦：
#   CHAIMI_DB    -> SQLite 主库 chaimi.db
#   CHAIMI_CONF  -> 配置文件 chaimi.conf（LLM Key / web_auth 等）
#   TRIM_PKGVAR  -> 运行时产物（小票 receipts/、日志、自动备份 backups/）
# 目录结构保持源码原样（server.py 的 _STATIC 取 ../static，src 与 static 必须同级）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TRIM_PKGVAR=/data \
    CHAIMI_DB=/data/chaimi.db \
    CHAIMI_CONF=/data/chaimi.conf

# Pillow/pycryptodome 均有 manylinux wheel，无需编译链
COPY chaimi-ledger-fn/app/src/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY chaimi-ledger-fn/app/src/ /app/src/
COPY chaimi-ledger-fn/app/static/ /app/static/
# 应用图片（头像/图标，/images/ 路由下发）——漏拷会让 images/avatar-*.png 全 404
COPY chaimi-ledger-fn/app/server/ /app/server/

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 18710

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18710/healthz', timeout=4)" || exit 1

CMD ["python", "src/server.py", "--host", "0.0.0.0", "--port", "18710"]

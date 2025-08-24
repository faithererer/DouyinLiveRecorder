# ---- 阶段一：构建环境 (命名为 builder) ----
FROM python:3.11-slim as builder

# 1. 安装编译时需要的所有工具 (nodejs, build-essential)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential curl gnupg && \
    curl -sL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# 2. 创建一个虚拟环境，并将 Python 依赖安装进去
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN . /opt/venv/bin/activate && pip install --no-cache-dir -r requirements.txt


# ---- 阶段二：最终的运行环境 ----
FROM python:3.11-slim

WORKDIR /app

# 1. 只安装程序运行时必需的系统依赖 (ffmpeg, nodejs)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg tzdata nodejs && \
    rm -rf /var/lib/apt/lists/*

# 2. 从第一阶段(builder)中，只把安装好的 Python 依赖虚拟环境复制过来
# 这样就不会把 build-essential 等编译工具带入最终镜像
COPY --from=builder /opt/venv /opt/venv

# 3. 复制你的项目代码
COPY . .

# 4. 设置时区
RUN ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata

# 5. 将虚拟环境的路径加入系统 PATH，这样可以直接执行命令
ENV PATH="/opt/venv/bin:$PATH"

CMD ["/opt/venv/bin/python", "main.py"]

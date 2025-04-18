FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 拷贝依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝项目其他文件
COPY . .

# 启动服务
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000"]

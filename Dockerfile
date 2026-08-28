FROM python:3.11-slim
WORKDIR /app

# sh-common from its GitHub repo at a version tag.
#   public repo  -> no token needed
#   private repo -> docker build --build-arg GH_TOKEN=<PAT with read access>
ARG GH_OWNER=ORG
ARG SH_COMMON_REF=v0.1.0
ARG GH_TOKEN=
RUN pip install --no-cache-dir \
      "sh-common @ git+https://${GH_TOKEN:+${GH_TOKEN}@}github.com/${GH_OWNER}/sh-common.git@${SH_COMMON_REF}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV PYTHONPATH=/app
EXPOSE 8500
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8500"]

FROM python:3.14-slim AS pybuilder

RUN apt-get update \
    && apt-get install -y build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/
RUN pip3 install --user -r /tmp/requirements.txt

# 使用仓库内人工确认的 spam.txt 在构建期生成确定性初始模型。
# 每个目标架构独立执行构建，产物为可移植 JSON，不依赖 pickle/joblib。
WORKDIR /model-build
COPY Makefile spam.txt ./
COPY spamdet ./spamdet
RUN make train-spam-model PYTHON=python SPAM_MODEL_OUTPUT=/opt/captchabot-model


FROM python:3.14-slim AS runner
WORKDIR /CaptchaBot
ENV TZ=Asia/Shanghai
ENV SPAM_MODEL_PATH=/CaptchaBot/models/spamdet
COPY . /CaptchaBot
COPY --from=pybuilder /root/.local /usr/local
COPY --from=pybuilder /opt/captchabot-model /CaptchaBot/models/spamdet
COPY --from=pybuilder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=pybuilder /usr/share/zoneinfo /usr/share/zoneinfo

CMD ["/usr/local/bin/python", "main.py"]

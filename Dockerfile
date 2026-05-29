FROM python:3.14-slim as pybuilder

RUN apt update && apt install -y build-essential
ADD requirements.txt /tmp/
RUN pip3 install --user -r /tmp/requirements.txt


FROM python:3.14-slim AS runner
WORKDIR /CaptchaBot
ENV TZ=Asia/Shanghai
COPY . /CaptchaBot
COPY --from=pybuilder /root/.local /usr/local
COPY --from=pybuilder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=pybuilder /usr/share/zoneinfo /usr/share/zoneinfo

CMD ["/usr/local/bin/python", "main.py"]
